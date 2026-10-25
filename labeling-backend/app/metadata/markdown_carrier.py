"""Markdown 隐式标识载体：YAML frontmatter 顶层键 ``AIGC``（GB 45438-2025 附录 E）。

本模块是 Markdown 的**唯一读写真源**——写入适配器、流水线回读校验、合规检测器
全部经它，避免"自己写入成功、自己检测器判失败"（开发手册 §14）。

载体约定
--------
- 规范载体：frontmatter 顶层键 ``AIGC``，值是附录 E 的 JSON **字符串**（单引号
  YAML 标量，一行写完），与图片/视频写入的是同一份 ``aigc.serialize_aigc`` 输出。
- 次要/旧载体：正文中含 ``AIGC`` 记号的 HTML 注释。**只读不写**——检测到时给
  LEGACY_CARRIER info（不翻转结论），``replace`` 策略下连同规范载体一并清除。
- 正文自身的散文里出现 "AIGC" 字样**不算**载体（否则本项目的文档全都会被判违规）。

写入原则
--------
- 外科手术式编辑：只替换 ``AIGC`` 键的跨度，其余行、注释、顺序、缩进原样保留；
  不做 PyYAML 整块重排（那会把用户的 frontmatter 格式全部改写）。
- 保留原文件的 BOM 与行尾风格（CRLF / LF）。
- 幂等：写两次仍只有一条记录。
"""
from __future__ import annotations

import codecs
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..core import aigc
from ..core.reader import AIGCRecord

BOM = codecs.BOM_UTF8
FRONTMATTER_DELIM = "---"

# frontmatter 顶层键：行首非空白、键名限 ASCII 词字符，冒号后为值
_KEY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)[ \t]*:(.*)$")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


class MarkdownCarrierError(Exception):
    """Markdown 文件本身的问题（非 UTF-8 / 含 NUL / 无法写出）。"""


@dataclass
class MarkdownShape:
    """文件的形态描述（诊断用，进报告的 document 块）。"""
    has_frontmatter: bool
    bom: bool
    eol: str
    line_count: int
    frontmatter_parseable: bool
    body_sha256: str


# ---- 读 ---------------------------------------------------------------

def _load_text(path: str | Path) -> tuple[str, bool]:
    """读取并严格解码；返回 (文本, 是否有 BOM)。文件本身的问题抛 MarkdownCarrierError。"""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise MarkdownCarrierError(f"无法读取文件: {e}") from None
    bom = data.startswith(BOM)
    if bom:
        data = data[len(BOM):]
    if b"\x00" in data:
        raise MarkdownCarrierError("文件含 NUL 字节，不是文本文件")
    try:
        return data.decode("utf-8"), bom
    except UnicodeDecodeError as e:
        raise MarkdownCarrierError(f"文件不是合法 UTF-8 文本: {e}") from None


def _detect_eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _frontmatter_span(lines: list[str]) -> tuple[int, int] | None:
    """返回 (起始分隔行, 结束分隔行) 的索引；无 frontmatter 返回 None。

    ``---`` 只有出现在**首行**才是 frontmatter 定界符——文件中间的 ``---`` 是
    分隔线，不得误判。结束符为 ``---`` 或 ``...``（YAML 文档结束标记）。
    """
    if not lines or lines[0].rstrip("\r\n").strip() != FRONTMATTER_DELIM:
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n").strip() in (FRONTMATTER_DELIM, "..."):
            return 0, i
    return None                          # 未闭合：按普通正文处理，不擅自改写


def _value_end(lines: list[str], key_idx: int, end: int) -> int:
    """一个顶层键的取值结束行（不含）。键后无内联值时，续行由缩进决定。"""
    inline = _KEY_RE.match(lines[key_idx].rstrip("\r\n"))
    if inline and inline.group(2).strip():
        return key_idx + 1
    i = key_idx + 1
    while i < end and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
        i += 1
    return i


def _aigc_occurrences(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """frontmatter 内所有大小写不敏感等于 ``aigc`` 的顶层键 → [(键行, 值结束行)]。

    逐行扫描而非依赖 yaml.safe_load：safe_load 对重复键只保留最后一个，而"仅一份"
    判定必须看到**每一处**（重复键正是 DUPLICATE_RECORDS 的证据）。
    """
    found: list[tuple[int, int]] = []
    i = start
    while i < end:
        m = _KEY_RE.match(lines[i].rstrip("\r\n"))
        if m and m.group(1).strip().lower() == "aigc":
            found.append((i, _value_end(lines, i, end)))
        i += 1
    return found


def _scalar_text(lines: list[str], key_idx: int, val_end: int) -> str | None:
    """把某次 AIGC 键的取值解成字符串；不是标量/无法解析返回 None。

    返回 None 表示**形态不合规**（例如写成嵌套映射而非附录 E 要求的 JSON 字符串），
    调用方据此报 BAD_JSON——而不是悄悄接受一个别的形状。
    """
    raw = _KEY_RE.match(lines[key_idx].rstrip("\r\n")).group(2).strip()
    if not raw:
        raw = "".join(lines[key_idx + 1:val_end])
    if not raw.strip():
        return None
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    return value if isinstance(value, str) else None


def _code_mask(text: str) -> str:
    """把代码区域（围栏块 + 行内代码）替换成空格，**保持字符偏移不变**。

    这样在掩码文本上做正则匹配得到的下标，可直接用于原文取内容——行号与
    raw_preview 仍然准确。代码里出现的 ``<!-- AIGC ... -->``（例如本项目文档
    演示载体格式）不算载体。
    """
    chars = list(text)
    i = 0
    n = len(text)
    in_fence = False
    fence_marker = ""
    while i < n:
        line_end = text.find("\n", i)
        line_end = n if line_end == -1 else line_end + 1
        line = text[i:line_end]
        m = _FENCE_RE.match(line)
        mask_line = False
        if m:
            marker = m.group(1)[0]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            mask_line = True             # 定界行本身也不是正文
        elif in_fence:
            mask_line = True             # 围栏**内部**的每一行
        if mask_line:
            for k in range(i, line_end):
                if chars[k] != "\n":
                    chars[k] = " "
        elif "`" in line:
            k = i
            while True:
                open_at = text.find("`", k)
                if open_at == -1 or open_at >= line_end:
                    break
                close_at = text.find("`", open_at + 1)
                if close_at == -1 or close_at >= line_end:
                    break
                for j in range(open_at, close_at + 1):
                    if chars[j] != "\n":
                        chars[j] = " "
                k = close_at + 1
        i = line_end
    return "".join(chars)


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _comment_records(text: str, from_index: int = 0) -> list[AIGCRecord]:
    """正文中含 ``AIGC`` 记号的 HTML 注释（旧载体，只读不写）。

    ``from_index`` 之前的区域（frontmatter）不参与——那里的载体通道是 YAML 键，
    在 YAML 值里找 HTML 注释只会制造误报。偏移一律相对**全文**，行号才准。
    """
    masked = _code_mask(text)
    records: list[AIGCRecord] = []
    for m in _COMMENT_RE.finditer(masked):
        if m.start() < from_index:
            continue
        body = text[m.start() + 4:m.end() - 3]
        if "AIGC" not in body:
            continue
        stripped = body.strip()
        payload = stripped
        for prefix in ("AIGC:", "AIGC="):
            if stripped.upper().startswith(prefix):
                payload = stripped[len(prefix):].strip()
                break
        records.append(AIGCRecord(
            tag_key="html-comment:AIGC",
            raw=payload,
            aigc=aigc.parse_aigc(payload),
            location=f"正文 HTML 注释（第 {_line_of(text, m.start())} 行）"))
    return records


def read_records(path: str | Path) -> list[AIGCRecord]:
    """扫描 Markdown 的全部 AIGC 候选（规范 frontmatter 键 + 旧 HTML 注释）。"""
    text, _bom = _load_text(path)
    lines = text.splitlines(keepends=True)
    span = _frontmatter_span(lines)

    records: list[AIGCRecord] = []
    body_at = 0
    if span is not None:
        start, end = span
        body_at = len("".join(lines[:end + 1]))
        for key_idx, val_end in _aigc_occurrences(lines, start + 1, end):
            value = _scalar_text(lines, key_idx, val_end)
            block = "".join(lines[key_idx:val_end])
            records.append(AIGCRecord(
                tag_key="frontmatter:AIGC",
                # 形态不合规（如写成嵌套映射而非 JSON 字符串）时 aigc=None，
                # 由检测器报 BAD_JSON——不悄悄接受另一个形状。
                raw=value if value is not None else block.strip()[:200],
                aigc=aigc.parse_aigc(value) if value is not None else None,
                location=f"YAML frontmatter 顶层键 AIGC（第 {key_idx + 1} 行）"))

    records.extend(_comment_records(text, body_at))
    return records


def describe(path: str | Path) -> MarkdownShape:
    """文件形态（进报告的 document 块，供前端与审计展示）。"""
    text, bom = _load_text(path)
    lines = text.splitlines(keepends=True)
    span = _frontmatter_span(lines)
    parseable = False
    if span is not None:
        block = "".join(lines[span[0] + 1:span[1]])
        try:
            parseable = isinstance(yaml.safe_load(block), (dict, type(None)))
        except yaml.YAMLError:
            parseable = False
    return MarkdownShape(
        has_frontmatter=span is not None, bom=bom, eol=_detect_eol(text),
        line_count=len(lines), frontmatter_parseable=parseable,
        body_sha256=_sha256(media_subject(text)))


# ---- 正文 -------------------------------------------------------------

def _body_text(text: str) -> str:
    """frontmatter 之外的全部内容；无 frontmatter 时即全文。"""
    lines = text.splitlines(keepends=True)
    span = _frontmatter_span(lines)
    if span is None:
        return text
    return "".join(lines[span[1] + 1:])


def _cut_comments(body: str) -> str:
    """按 ``remove`` 的同一规则切掉含 AIGC 记号的注释（独占整行时连行一起切）。"""
    masked = _code_mask(body)
    out: list[str] = []
    last = 0
    for m in _COMMENT_RE.finditer(masked):
        if "AIGC" not in body[m.start() + 4:m.end() - 3]:
            continue
        line_start = body.rfind("\n", 0, m.start()) + 1
        if body[line_start:m.start()].strip() == "":
            cut_from = line_start
            nl = body.find("\n", m.end())
            cut_to = len(body) if nl == -1 else nl + 1
        else:
            cut_from, cut_to = m.start(), m.end()
        out.append(body[last:cut_from])
        last = cut_to
    out.append(body[last:])
    return "".join(out)


def media_subject(text: str) -> str:
    """媒体主体：frontmatter 之外、**再剔除全部 AIGC 载体**后的正文。

    这是文本模态里对应"图像像素"的东西——写入标识不得改动它。剔除载体是必须的：
    旧载体（HTML 注释）本来就落在正文里，``replace`` 策略会**有意**把它删掉，
    若把它算进主体，删除动作本身就成了一次"媒体改动"，媒体完整性将永远失败。
    同理，图片的像素哈希也不会把 EXIF/XMP 段算进去。
    """
    return _cut_comments(_body_text(text))


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---- 写 ---------------------------------------------------------------

def _yaml_quote(value: str) -> str:
    """单引号 YAML 标量：JSON 里的引号无需转义，只需把自身的 ' 写成 ''。"""
    return "'" + value.replace("'", "''") + "'"


def _write_bytes(path: str | Path, text: str, bom: bool) -> None:
    data = (BOM if bom else b"") + text.encode("utf-8")
    Path(path).write_bytes(data)


def _rewrite_frontmatter(lines: list[str], start: int, end: int,
                         new_line: str) -> str:
    """在 frontmatter 内落位新的 AIGC 键：替换首处、删除其余重复键。

    重复键一并删除是刻意的——国标 6.1 c) 要求"仅保留一份"，写入端若能收敛，
    就不该把一个必然判 DUPLICATE_RECORDS 的结果交出去。
    """
    occ = _aigc_occurrences(lines, start + 1, end)
    occ_map = dict(occ)
    out: list[str] = []
    placed = False
    i = 0
    while i <= end:
        if i in occ_map:
            if not placed:
                out.append(new_line)
                placed = True
            i = occ_map[i]                   # 跳过旧键（含其续行）
            continue
        if i == end and not placed:
            out.append(new_line)
            placed = True
        out.append(lines[i])
        i += 1
    return "".join(out) + "".join(lines[end + 1:])


def write(src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
    """把附录 E 标识写进 frontmatter 顶层键 AIGC（副本输出，绝不动原文件）。"""
    text, bom = _load_text(src)
    eol = _detect_eol(text)
    new_line = f"AIGC: {_yaml_quote(aigc.serialize_aigc(aigc_obj))}{eol}"
    lines = text.splitlines(keepends=True)
    span = _frontmatter_span(lines)

    if span is None:
        # 无 frontmatter：顶部补一段，正文逐字节不动、不额外增删空行
        out = f"{FRONTMATTER_DELIM}{eol}{new_line}{FRONTMATTER_DELIM}{eol}{text}"
    else:
        out = _rewrite_frontmatter(lines, span[0], span[1], new_line)
    _write_bytes(dst, out, bom)


def remove(src: str | Path, dst: str | Path) -> None:
    """移除全部可识别的 AIGC 标识（规范 frontmatter 键 + 旧 HTML 注释）。"""
    text, bom = _load_text(src)
    lines = text.splitlines(keepends=True)
    span = _frontmatter_span(lines)

    if span is not None:
        start, end = span
        occ = dict(_aigc_occurrences(lines, start + 1, end))
        kept: list[str] = []
        i = 0
        while i < len(lines):
            if i in occ:
                i = occ[i]
                continue
            kept.append(lines[i])
            i += 1
        text = "".join(kept)

    # 注释切割与 media_subject 共用同一份实现：两者一旦分叉，"移除旧载体"就会
    # 变成一次媒体改动，媒体完整性将永远失败。
    _write_bytes(dst, _cut_comments(text), bom)


# ---- 媒体完整性 -------------------------------------------------------

def media_integrity_report(before: dict, after: dict) -> tuple[bool, str | None]:
    """比对前后形态，返回 (是否通过, 原因)。"""
    problems: list[str] = []
    if before["body_sha256"] != after["body_sha256"]:
        problems.append("正文内容发生变化（标识写入不得改动正文）")
    if before["bom"] != after["bom"]:
        problems.append("BOM 状态发生变化")
    if before["eol"] != after["eol"]:
        problems.append(f"行尾风格变化: {before['eol']!r} -> {after['eol']!r}")
    return (not problems), ("; ".join(problems) if problems else None)
