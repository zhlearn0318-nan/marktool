"""PDF 结构探测（纯标准库 + ExifTool）：加密 / 签名 / %%EOF / 页数。

项目不引入 PDF 库（无网络依赖、也不为一个字段多背一个第三方解析器）：
- **页数**由 ExifTool 报告（``PDF:PageCount``），与元数据读取走同一次工具调用；
- **加密 / 签名 / 文件尾完整性**用字节扫描判定。这两类标记按 PDF 规范都落在
  尾部 trailer / 增量更新段，故对尾部窗口扫描即可覆盖常见情况；无法识别的
  极端布局会退化成"照常尝试写入"，由 ExifTool 的真实失败来兜底报错，
  不会静默产出一个错的结论。

为什么加密与签名要拦在写入之前：ExifTool 是**整体重写** PDF，任何字节改动都会
让已签名文档的 ``/ByteRange`` 摘要失效——而保护签名正是国标附录 E 预留字段
（ReservedCode1/2）想做的事，工具不该反过来把它毁掉。加密文档则根本无法安全重写。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

EOF_MARKER = b"%%EOF"
PDF_SIGNATURE = b"%PDF-"

# 尾部窗口：trailer 字典、xref 流与增量更新段都在这段范围内
_TAIL_WINDOW = 64 * 1024
_EOF_WINDOW = 1024

_ENCRYPT_TOKEN = b"/Encrypt"
_BYTERANGE_TOKEN = b"/ByteRange"
_SIG_TYPE_TOKENS = (b"/Type/Sig", b"/Type /Sig")

# ---- 内容指纹（content_digest）用 ----
_STREAM_KEYWORD = b"stream"
_ENDSTREAM_KEYWORD = b"endstream"
# 对象字典在流关键字之前多长算"这段字典"：PDF 的对象字典不会很长，2 KiB 足够，
# 再长就可能把上一个对象的字典捞进来。
_DICT_WINDOW = 2048
_LENGTH_RE = re.compile(rb"/Length\s+(\d+)\b")
# XMP 元数据流的两种识别方式：字典声明（标准）与内容特征（兜底）
_METADATA_DICT_TOKENS = (b"/Metadata", b"/XML")
_METADATA_PAYLOAD_TOKENS = (b"xmpmeta", b"<?xpacket", b"<rdf:RDF", b"aigc:")


@dataclass
class PdfProbe:
    """一次 PDF 结构探测的结果（诊断 + 判定共用）。"""
    size_bytes: int
    has_eof: bool
    encrypted: bool
    signed: bool
    version: str | None
    page_count: int | None = None

    @property
    def writable(self) -> bool:
        """能否安全写入标识。"""
        return not self.encrypted and not self.signed

    def to_dict(self) -> dict:
        return {
            "applicable": True,
            "size_bytes": self.size_bytes,
            "version": self.version,
            "page_count": self.page_count,
            "eof_marker_present": self.has_eof,
            "encrypted": self.encrypted,
            "signed": self.signed,
        }


def _tail(path: str | Path, window: int = _TAIL_WINDOW) -> bytes:
    size = Path(path).stat().st_size
    with open(path, "rb") as f:
        if size > window:
            f.seek(size - window)
        return f.read()


def scan(path: str | Path) -> PdfProbe:
    """探测 PDF 结构。只读，不抛文件内容类异常（读不了的由调用方处理）。"""
    p = Path(path)
    size = p.stat().st_size
    tail = _tail(p)
    head = tail[:8] if size <= 8 else _head(p)

    version = None
    if head.startswith(PDF_SIGNATURE):
        version = head[:8].decode("latin-1", "replace").strip()

    signed = _BYTERANGE_TOKEN in tail or any(t in tail for t in _SIG_TYPE_TOKENS)
    return PdfProbe(
        size_bytes=size,
        has_eof=EOF_MARKER in tail[-_EOF_WINDOW:],
        encrypted=_ENCRYPT_TOKEN in tail,
        signed=signed,
        version=version,
    )


def _head(path: str | Path, n: int = 8) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def content_digest(path: str | Path, *, page_count: int | None,
                   has_eof: bool) -> str:
    """PDF 内容指纹：所有**内容流**载荷的 sha256（+ 页数与结束标记）。

    早先这里只有"页数 + ``%%EOF``"，粒度粗到**两份页数相同的不同文档算出来
    一模一样**——登记库核对"是不是同一份内容"时基本没用。改用流载荷后能真正
    区分内容，理由：
    - ExifTool 写 PDF 是整体重写文件布局（对象偏移、``startxref``、``/ID`` 都变），
      所以整个文件的哈希跨写入必变，不能当指纹；
    - 但**流的载荷字节原样复制**，写标识不会碰它们，所以流载荷跨写入稳定。

    XMP 元数据流要排除（每次写标识都会重建它，含标识的字节不能进指纹）：
    按对象字典里的 ``/Metadata`` / ``/XML`` 判定，再用内容特征（``xmpmeta`` /
    ``aigc:`` / ``<?xpacket``）兜一层——只按字典判会漏掉不走标准写法的生成器。

    读不到任何内容流时（对象流压缩等少见布局）退化成"页数 + 结束标记"，
    与改动前同级：**不比原来差**，只是没变强。
    """
    data = Path(path).read_bytes()
    digest = hashlib.sha256()
    digest.update(f"pages={page_count}|eof={int(has_eof)}|".encode("utf-8"))
    count = 0
    for payload in _content_streams(data):
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    digest.update(b"|streams=%d" % count)
    return digest.hexdigest()


def _content_streams(data: bytes) -> Iterator[bytes]:
    """逐个产出内容流的载荷（跳过 XMP 元数据流与解析不出的片段）。"""
    position = 0
    while True:
        found = data.find(_STREAM_KEYWORD, position)
        if found < 0:
            return
        # ``endstream`` 里也含 "stream" 这六个字母，别把结尾当开头
        if data[found - 3:found] == b"end":
            position = found + len(_STREAM_KEYWORD)
            continue
        # 规范要求关键字后紧跟 CRLF 或 LF；不满足就说明这不是流关键字
        newline = 2 if data[found + 6:found + 8] in (b"\r\n", b"\n\r") else 1
        if data[found + 6:found + 6 + newline].strip() not in (b"", b"\n"):
            position = found + len(_STREAM_KEYWORD)
            continue
        start = found + 6 + newline
        dictionary = data[max(0, found - _DICT_WINDOW):found]
        payload = _stream_payload(data, start, dictionary)
        if payload is None:
            position = start
            continue
        position = start + len(payload)
        if _is_metadata(dictionary, payload):
            continue
        yield payload


def _stream_payload(data: bytes, start: int, dictionary: bytes) -> bytes | None:
    """按 ``/Length`` 取载荷；取不到就退到下一个 ``endstream``。"""
    match = None
    for match in _LENGTH_RE.finditer(dictionary):
        pass
    if match is not None:
        end = start + int(match.group(1))
        if data[end:end + 2] in (b"\r\n", b"\n"):
            end += 1
            if data[end - 2:end] == b"\r\n":
                end += 1
        if data[end:end + 20].lstrip().startswith(_ENDSTREAM_KEYWORD):
            return data[start:end - (1 if data[end - 1:end] == b"\n" else 0)]
    closing = data.find(_ENDSTREAM_KEYWORD, start)
    if closing < 0:
        return None
    return data[start:closing].rstrip(b"\r\n")


def _is_metadata(dictionary: bytes, payload: bytes) -> bool:
    if any(token in dictionary for token in _METADATA_DICT_TOKENS):
        return True
    return any(token in payload[:4096] for token in _METADATA_PAYLOAD_TOKENS)


def page_count_from_tags(tags: dict) -> int | None:
    """从 ExifTool 全标签字典里取页数（``PDF:PageCount``）。取不到返回 None。"""
    for key in ("PDF:PageCount", "PageCount"):
        if key in tags:
            try:
                return int(tags[key])
            except (TypeError, ValueError):
                return None
    return None


def integrity_problems(before: PdfProbe, after: PdfProbe) -> list[str]:
    """写入前后的结构差异；空列表表示通过（§9.4 媒体完整性）。"""
    problems: list[str] = []
    if not after.has_eof:
        problems.append("结果文件缺少 %%EOF 结束标记（PDF 尾部损坏）")
    if before.page_count is not None and after.page_count is not None:
        if before.page_count != after.page_count:
            problems.append(
                f"页数变化: {before.page_count} -> {after.page_count}")
    elif before.page_count is not None and after.page_count is None:
        problems.append("结果文件无法读出页数（结构可能损坏）")
    if after.encrypted != before.encrypted:
        problems.append("加密状态发生变化")
    # 结果文件不该比源文件小到不合理（整体重写丢失对象的典型症状）
    if before.size_bytes and after.size_bytes < before.size_bytes * 0.5:
        problems.append(
            f"文件体积异常缩小: {before.size_bytes}B -> {after.size_bytes}B")
    return problems
