"""Markdown 载体模块单测（GB 45438-2025 附录 E，开发手册 §9.3/§9.4）。

这个模块是 Markdown 的唯一读写真源，所以本文件不碰 ExifTool、不碰 API——
纯文本进、纯文本出，把载体规则本身钉死。
"""
from __future__ import annotations

import hashlib

import pytest

from app.core import aigc
from app.metadata import markdown_carrier as mc
from tests.common import VALID_AIGC, make_markdown

AIGC_JSON = aigc.serialize_aigc(aigc.normalize_first_write(VALID_AIGC))


def _body_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _records_of(path):
    return mc.read_records(path)


# ---- 读：干净文件与正文误报 -------------------------------------------

def test_clean_markdown_has_no_record(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    assert _records_of(src) == []


def test_prose_mention_of_aigc_is_not_a_carrier(tmp_path):
    """正文散文里出现 "AIGC" 字样不算载体——否则本项目的文档全都会判违规。"""
    src = make_markdown(tmp_path / "a.md",
                        "# AIGC 标注说明\n\n本文讨论 AIGC 隐式标识与 AIGC 元数据。\n")
    assert _records_of(src) == []


def test_frontmatter_without_aigc_key_has_no_record(tmp_path):
    src = make_markdown(tmp_path / "a.md", "正文\n", frontmatter="title: t\nauthor: x")
    assert _records_of(src) == []


def test_mid_document_rule_is_not_frontmatter(tmp_path):
    """文件中间的 ``---`` 是分隔线，不得误判成 frontmatter 起点。"""
    src = make_markdown(tmp_path / "a.md", "上\n\n---\n\n下\n\n---\n\n尾\n")
    assert _records_of(src) == []
    assert mc.describe(src).has_frontmatter is False


def test_unrelated_broken_frontmatter_does_not_raise(tmp_path):
    """与 AIGC 无关的坏 frontmatter 不该让读取失败，也不该产出候选。"""
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter="title: [未闭合\n  bad: : x")
    assert _records_of(src) == []
    assert mc.describe(src).frontmatter_parseable is False


def test_broken_frontmatter_containing_aigc_yields_candidate(tmp_path):
    """含 AIGC 记号的坏 frontmatter → 有候选但解析不出对象（检测器据此报 BAD_JSON）。"""
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter='AIGC: {"AIGC": {未闭合')
    recs = _records_of(src)
    assert len(recs) == 1
    assert recs[0].tag_key == "frontmatter:AIGC"
    assert recs[0].aigc is None


def test_nested_mapping_is_not_accepted_as_json_string(tmp_path):
    """附录 E 要求值是 JSON **字符串**；写成嵌套映射是形态不合规，不悄悄接受。"""
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter="AIGC:\n  AIGC:\n    Label: '1'")
    recs = _records_of(src)
    assert len(recs) == 1
    assert recs[0].aigc is None


# ---- 读：旧载体（正文 HTML 注释）---------------------------------------

def test_html_comment_with_aigc_is_a_legacy_record(tmp_path):
    src = make_markdown(tmp_path / "a.md", f"正文\n\n<!-- AIGC: {AIGC_JSON} -->\n")
    recs = _records_of(src)
    assert len(recs) == 1
    assert recs[0].tag_key == "html-comment:AIGC"
    assert recs[0].aigc["Label"] == "1"
    assert "第 3 行" in recs[0].location


def test_html_comment_without_aigc_is_ignored(tmp_path):
    src = make_markdown(tmp_path / "a.md", "正文\n\n<!-- 只是普通注释 -->\n")
    assert _records_of(src) == []


def test_html_comment_inside_code_fence_is_not_a_carrier(tmp_path):
    """代码块里演示载体格式（本项目文档就这样写）不得被当成真标识。"""
    src = make_markdown(
        tmp_path / "a.md",
        "正文\n\n```markdown\n<!-- AIGC: {\"AIGC\":{}} -->\n```\n")
    assert _records_of(src) == []


def test_html_comment_inside_inline_code_is_not_a_carrier(tmp_path):
    src = make_markdown(tmp_path / "a.md", "正文 `<!-- AIGC: xx -->` 续\n")
    assert _records_of(src) == []


# ---- 写：落位与外科手术式编辑 ------------------------------------------

def test_write_without_frontmatter_prepends_block_and_keeps_body(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    dst = tmp_path / "out.md"
    mc.write(src, dst, aigc.normalize_first_write(VALID_AIGC))

    text = dst.read_text(encoding="utf-8")
    assert text.startswith("---\nAIGC: '")
    assert text.endswith("# 标题\n\n正文。\n")          # 正文逐字节保留
    recs = _records_of(dst)
    assert len(recs) == 1
    assert recs[0].aigc == aigc.normalize_first_write(VALID_AIGC)


def test_write_preserves_other_frontmatter_keys_and_order(tmp_path):
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter="# 注释行\ntitle: t\ntags:\n  - a\n  - b")
    dst = tmp_path / "out.md"
    mc.write(src, dst, aigc.normalize_first_write(VALID_AIGC))

    text = dst.read_text(encoding="utf-8")
    assert "# 注释行" in text                          # 注释保留
    assert text.index("title: t") < text.index("AIGC: '")   # 已有键在原位
    assert "  - a\n  - b\n" in text                    # 缩进序列未被重排
    assert text.index("AIGC: '") < text.index("---\n正文")  # 插在闭合符前


def test_write_is_idempotent(tmp_path):
    """写两次仍只有一条记录（外科手术式替换而非追加）。"""
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    first, second = tmp_path / "1.md", tmp_path / "2.md"
    obj = aigc.normalize_first_write(VALID_AIGC)
    mc.write(src, first, obj)
    mc.write(first, second, obj)
    assert second.read_bytes() == first.read_bytes()   # 第二次写入零改动
    assert len(_records_of(second)) == 1


def test_write_collapses_duplicate_keys(tmp_path):
    """写入端主动收敛重复键：国标 6.1 c) 要求仅保留一份，不该把一个必然判
    DUPLICATE_RECORDS 的结果交出去。"""
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter=f"AIGC: '{AIGC_JSON}'\ntitle: t\nAIGC: '{AIGC_JSON}'")
    assert len(_records_of(src)) == 2                  # 前提：读得到两处
    dst = tmp_path / "out.md"
    mc.write(src, dst, aigc.normalize_first_write(VALID_AIGC))
    assert len(_records_of(dst)) == 1
    assert "title: t" in dst.read_text(encoding="utf-8")


def test_write_preserves_crlf_and_bom(tmp_path):
    src = tmp_path / "a.md"
    src.write_bytes(mc.BOM + "---\r\ntitle: t\r\n---\r\n# 标题\r\n\r\n正文。\r\n".encode())
    dst = tmp_path / "out.md"
    mc.write(src, dst, aigc.normalize_first_write(VALID_AIGC))

    raw = dst.read_bytes()
    assert raw.startswith(mc.BOM)                      # BOM 保留
    assert b"\r\n" in raw and b"\nAIGC: '" in raw.replace(b"\r\n", b"\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")      # 不混入裸 LF
    shape = mc.describe(dst)
    assert shape.eol == "\r\n" and shape.bom is True


def test_write_does_not_touch_source(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    before = src.read_bytes()
    mc.write(src, tmp_path / "out.md", aigc.normalize_first_write(VALID_AIGC))
    assert src.read_bytes() == before                  # 副本输出，绝不动原文件


def test_single_quote_in_value_survives_the_roundtrip(tmp_path):
    """JSON 里的双引号靠 YAML 单引号标量免转义；值自身的 ' 必须写成 '' 才不破句。"""
    src = make_markdown(tmp_path / "a.md", "正文\n")
    obj = aigc.normalize_first_write(VALID_AIGC)
    obj["ReservedCode1"] = "it's"
    dst = tmp_path / "out.md"
    mc.write(src, dst, obj)

    assert mc._yaml_quote("it's") == "'it''s'"
    recs = _records_of(dst)
    assert len(recs) == 1
    assert recs[0].aigc == obj                         # 逐字段还原


# ---- 写：非 UTF-8 / 二进制一律拒绝 -------------------------------------

@pytest.mark.parametrize("payload", [
    b"---\ntitle: \xff\xfe\n---\n\xff\xfe\xfd\n",     # 非法 UTF-8
    b"# \xe6\xa0\x87\xe9\xa2\x98\x00\x01\n",          # 含 NUL
])
def test_non_text_markdown_is_rejected(tmp_path, payload):
    p = tmp_path / "a.md"
    p.write_bytes(payload)
    with pytest.raises(mc.MarkdownCarrierError):
        mc.read_records(p)
    with pytest.raises(mc.MarkdownCarrierError):
        mc.describe(p)
    with pytest.raises(mc.MarkdownCarrierError):
        mc.write(p, tmp_path / "out.md", aigc.normalize_first_write(VALID_AIGC))


# ---- 移除 -------------------------------------------------------------

def test_remove_strips_key_and_legacy_comment(tmp_path):
    """移除只吃标识本身：``AIGC`` 键连其续行、注释连其独占行，其余正文一字不动。

    注释两侧原本各有空行，删掉注释行后剩下的 ``\\n\\n`` 保留原样——那是文件
    本来就在那里的内容，不该由"移除标识"顺手代删。
    """
    src = make_markdown(
        tmp_path / "a.md",
        f"# 标题\n\n<!-- AIGC: {AIGC_JSON} -->\n\n正文。\n",
        frontmatter="title: t")
    dst = tmp_path / "no.md"
    mc.remove(src, dst)

    assert _records_of(dst) == []
    text = dst.read_text(encoding="utf-8")
    assert text == "---\ntitle: t\n---\n# 标题\n\n\n正文。\n"
    assert "<!--" not in text and "AIGC" not in text


def test_remove_inline_comment_keeps_the_line(tmp_path):
    src = make_markdown(tmp_path / "a.md", f"前 <!-- AIGC: {AIGC_JSON} --> 后\n")
    dst = tmp_path / "no.md"
    mc.remove(src, dst)
    assert _records_of(dst) == []
    assert dst.read_text(encoding="utf-8") == "前  后\n"


def test_remove_on_clean_file_is_a_noop(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    dst = tmp_path / "no.md"
    mc.remove(src, dst)
    assert dst.read_bytes() == src.read_bytes()


# ---- 媒体完整性 -------------------------------------------------------

def test_media_integrity_passes_when_body_untouched(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    dst = tmp_path / "out.md"
    mc.write(src, dst, aigc.normalize_first_write(VALID_AIGC))
    passed, reason = mc.media_integrity_report(
        _shape(src), _shape(dst))
    assert passed and reason is None


def test_media_integrity_detects_tampered_body(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    dst = make_markdown(tmp_path / "b.md", "# 标题\n\n被改过的正文。\n")
    passed, reason = mc.media_integrity_report(_shape(src), _shape(dst))
    assert not passed and "正文" in reason


def _shape(path) -> dict:
    s = mc.describe(path)
    return {"has_frontmatter": s.has_frontmatter, "bom": s.bom,
            "eol": "CRLF" if s.eol == "\r\n" else "LF",
            "line_count": s.line_count, "body_sha256": s.body_sha256}


def test_body_hash_covers_everything_after_frontmatter(tmp_path):
    """正文哈希 = frontmatter 之外的全部内容；无 frontmatter 时即全文。"""
    a = make_markdown(tmp_path / "a.md", "正文\n")
    b = make_markdown(tmp_path / "b.md", "正文\n", frontmatter="title: t")
    assert mc.describe(a).body_sha256 == mc.describe(b).body_sha256
    assert mc.describe(a).body_sha256 == _body_hash("正文\n")
