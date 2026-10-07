"""文档（Markdown / PDF）合规检测器测试（合规方案 §8.3 / §9.2）。

重点不在重复 MP4 检测器已有的结构判定（那由 tests/test_inspector.py 覆盖），
而在两件文档特有的事：
1. 候选从哪来、哪种标签算旧载体；
2. §2.4「媒体状态与元数据结论分离」在加密/签名/截断上的落地。
外加一条横向契约：报告结构与 MP4 同构，前端无需为模态分叉（§4.5）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.core import aigc
from app.core.inspector import MetadataComplianceInspector
from app.metadata import markdown_carrier as mc
from app.metadata.document_inspector import (DETECTOR_VERSION,
                                             DocumentComplianceInspector,
                                             DocumentInspectError, PDF_MIME)
from tests.common import (CLEAN_MP4, EXIFTOOL_CONFIG, VALID_AIGC, make_markdown,
                          make_pdf, make_signed_pdf)

MARKDOWN_MIME = "text/markdown"
AIGC_JSON = aigc.serialize_aigc(aigc.normalize_first_write(VALID_AIGC))


def _find_exiftool() -> str | None:
    configured = os.getenv("EXIFTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if discovered:
        return discovered
    local_windows_copy = Path(r"D:\exiftool\exiftool.exe")
    return str(local_windows_copy) if local_windows_copy.is_file() else None


@pytest.fixture
def exiftool() -> str:
    executable = _find_exiftool()
    if not executable:
        if os.getenv("AIGC_REQUIRE_EXIFTOOL") == "1":
            pytest.fail("CI 要求真实 ExifTool，但当前未找到可执行文件")
        pytest.skip("需要 ExifTool；请设置 EXIFTOOL_PATH 后运行集成测试")
    return executable


def _md_inspector() -> DocumentComplianceInspector:
    """Markdown 检测不调外部工具，用默认路径即可。"""
    return DocumentComplianceInspector(MARKDOWN_MIME)


def _pdf_inspector(exiftool: str) -> DocumentComplianceInspector:
    return DocumentComplianceInspector(PDF_MIME, exiftool=exiftool,
                                       exiftool_config=str(EXIFTOOL_CONFIG))


def _labeled_md(tmp_path) -> Path:
    src = make_markdown(tmp_path / "src.md", "# 标题\n\n正文。\n")
    dst = tmp_path / "labeled.md"
    mc.write(src, dst, aigc.normalize_first_write(VALID_AIGC))
    return dst


# ---- Markdown：候选与结论 -------------------------------------------------

def test_markdown_clean_is_not_found(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    r = _md_inspector().inspect(src)
    assert r["conclusion"] == "not_found"
    assert r["record_count"] == 0 and r["candidates"] == []
    assert r["reason_code"] is None and r["issues"] == []
    assert r["detected_mime_type"] == MARKDOWN_MIME
    assert r["media_status"] == "ok"


def test_markdown_labeled_is_compliant(tmp_path):
    r = _md_inspector().inspect(_labeled_md(tmp_path))
    assert r["conclusion"] == "compliant"
    assert r["record_count"] == 1 and r["issues"] == []
    assert r["repairability"] is None
    assert r["candidates"][0]["parseable"] is True


def test_markdown_prose_mention_stays_not_found(tmp_path):
    """正文提到 AIGC 不是标识——检测器不能把项目自己的文档判成违规。"""
    src = make_markdown(tmp_path / "a.md", "# AIGC 说明\n\n本文讲 AIGC 标识。\n")
    assert _md_inspector().inspect(src)["conclusion"] == "not_found"


def test_markdown_duplicate_carriers_is_noncompliant(tmp_path):
    """frontmatter 键 + HTML 注释双载体 → DUPLICATE_RECORDS，且可自动修复。"""
    src = make_markdown(
        tmp_path / "a.md", f"正文\n\n<!-- AIGC: {AIGC_JSON} -->\n",
        frontmatter=f"AIGC: '{AIGC_JSON}'")
    r = _md_inspector().inspect(src)
    assert r["conclusion"] == "noncompliant"
    assert r["reason_code"] == "DUPLICATE_RECORDS"
    assert r["record_count"] == 2
    assert r["repairability"] == "auto_fixable"


def test_markdown_legacy_comment_alone_stays_compliant(tmp_path):
    """只有旧载体（HTML 注释）不算重复：给 LEGACY_CARRIER 提示，不翻转结论。"""
    src = make_markdown(tmp_path / "a.md", f"正文\n\n<!-- AIGC: {AIGC_JSON} -->\n")
    r = _md_inspector().inspect(src)
    assert r["conclusion"] == "compliant"
    assert r["record_count"] == 1
    assert [i["code"] for i in r["issues"]] == ["LEGACY_CARRIER"]
    assert r["issues"][0]["severity"] == "info"


def test_markdown_bad_json_is_noncompliant(tmp_path):
    src = make_markdown(tmp_path / "a.md", "正文\n", frontmatter="AIGC: '不是 JSON'")
    r = _md_inspector().inspect(src)
    assert r["conclusion"] == "noncompliant"
    assert r["reason_code"] == "BAD_JSON"
    assert r["candidates"][0]["parseable"] is False


def test_markdown_missing_field_is_reported(tmp_path):
    partial = {"Label": "1", "ContentProducer": "ORG_X"}
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter=f"AIGC: '{aigc.serialize_aigc(partial)}'")
    r = _md_inspector().inspect(src)
    assert r["conclusion"] == "noncompliant"
    assert r["reason_code"] == "MISSING_FIELD"
    assert r["repairability"] == "needs_human"


def test_markdown_bad_label_is_reported(tmp_path):
    bad = dict(aigc.normalize_first_write(VALID_AIGC), Label="9")
    src = make_markdown(tmp_path / "a.md", "正文\n",
                        frontmatter=f"AIGC: '{aigc.serialize_aigc(bad)}'")
    assert _md_inspector().inspect(src)["reason_code"] == "BAD_LABEL"


def test_markdown_document_block_describes_the_file(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n",
                        frontmatter="title: t")
    r = _md_inspector().inspect(src)
    doc = r["document"]
    assert doc["format"] == "markdown"
    assert doc["frontmatter_present"] is True and doc["frontmatter_parseable"] is True
    assert doc["eol"] == "LF" and doc["bom"] is False
    assert r["bmff"]["applicable"] is False          # 前端按此渲染，不必分叉


def test_markdown_crlf_and_bom_are_reported(tmp_path):
    src = tmp_path / "a.md"
    src.write_bytes(mc.BOM + "---\r\ntitle: t\r\n---\r\n正文\r\n".encode())
    doc = _md_inspector().inspect(src)["document"]
    assert doc["eol"] == "CRLF" and doc["bom"] is True


def test_markdown_non_utf8_raises_inspect_error(tmp_path):
    """文件压根不是文本 → 不是"结论"，是 415 的材料（与图片侧损坏文件同处理）。"""
    p = tmp_path / "a.md"
    p.write_bytes(b"# \xff\xfe\x00\x01 not utf-8\n")
    with pytest.raises(DocumentInspectError):
        _md_inspector().inspect(p)


def test_unsupported_mime_rejected():
    with pytest.raises(ValueError):
        DocumentComplianceInspector("text/html")


# ---- PDF ------------------------------------------------------------------

def test_pdf_clean_is_not_found(exiftool, tmp_path):
    r = _pdf_inspector(exiftool).inspect(make_pdf(tmp_path / "a.pdf", pages=2))
    assert r["conclusion"] == "not_found"
    assert r["media_status"] == "ok"
    assert r["document"]["page_count"] == 2
    assert r["document"]["eof_marker_present"] is True
    assert r["bmff"]["applicable"] is False


def test_pdf_labeled_is_compliant(exiftool, tmp_path):
    from app.adapters import PdfAdapter
    src = make_pdf(tmp_path / "a.pdf")
    dst = tmp_path / "labeled.pdf"
    PdfAdapter(exiftool=exiftool, exiftool_config=str(EXIFTOOL_CONFIG)).write_metadata(
        src, dst, aigc.normalize_first_write(VALID_AIGC))

    r = _pdf_inspector(exiftool).inspect(dst)
    assert r["conclusion"] == "compliant"
    assert r["record_count"] == 1
    assert r["candidates"][0]["location"] == "PDF XMP 元数据包（Metadata 流）"


def test_pdf_legacy_info_key_is_flagged_not_flipped(exiftool, tmp_path):
    """Info 字典键旧载体：给 LEGACY_CARRIER，不翻转结论。

    用 Subject 而非 Keywords 承载：ExifTool 把 ``PDF:Keywords`` 定义为列表标签，
    读到逗号就切段，正好把 JSON 腰斩——那是夹具的坑，不是载体的坑。
    """
    from app.adapters import PdfAdapter
    p = make_pdf(tmp_path / "a.pdf")
    PdfAdapter(exiftool=exiftool)._exiftool_cmd(
        ["-overwrite_original", "-PDF:Subject=" + AIGC_JSON], str(p))

    r = _pdf_inspector(exiftool).inspect(p)
    assert r["conclusion"] == "compliant"
    assert [i["code"] for i in r["issues"]] == ["LEGACY_CARRIER"]
    assert r["candidates"][0]["location"] == "PDF Document Info 字典键（旧载体）"


def test_pdf_encrypted_is_indeterminate(exiftool, tmp_path):
    """加密 → 元数据整体不可读，"没有标识"无从确认，不得当干净的 not_found（§2.4）。"""
    p = make_pdf(tmp_path / "e.pdf")
    p.write_bytes(p.read_bytes().replace(
        b"trailer\n", b"trailer\n<< /Encrypt 9 0 R >>\n", 1))

    r = _pdf_inspector(exiftool).inspect(p)
    assert r["conclusion"] == "indeterminate"
    assert r["reason_code"] == "PDF_ENCRYPTED"
    assert r["media_status"] == "unreadable"
    assert r["confidence"] == "low" and r["repairability"] == "needs_human"
    assert r["document"]["encrypted"] is True


def test_pdf_signed_is_degraded_but_still_judged(exiftool, tmp_path):
    """签名文档只读检测是安全的：如实标 degraded + 提示不可打标，结论照常。"""
    r = _pdf_inspector(exiftool).inspect(make_signed_pdf(tmp_path / "s.pdf"))
    assert r["media_status"] == "degraded"
    assert r["document"]["signed"] is True
    assert "PDF_SIGNED_PRESENT" in [i["code"] for i in r["issues"]]
    assert r["conclusion"] in ("not_found", "compliant", "noncompliant")


def test_pdf_truncated_never_reads_as_clean_not_found(exiftool, tmp_path):
    """截断文件"读不到标识"不是确定结论，一律落 indeterminate 交人工。"""
    raw = make_pdf(tmp_path / "t.pdf", pages=3).read_bytes()
    p = tmp_path / "trunc.pdf"
    p.write_bytes(raw[:len(raw) // 2])

    r = _pdf_inspector(exiftool).inspect(p)
    assert r["conclusion"] == "indeterminate"
    assert r["reason_code"] == "UNREADABLE_CARRIER"
    assert r["media_status"] == "degraded"
    assert r["document"]["eof_marker_present"] is False


# ---- 横向契约：报告结构与 MP4 同构 ---------------------------------------

def test_report_shape_matches_mp4(exiftool, tmp_path):
    """同一套字段名，前端报告页不为模态分叉（§4.5）。"""
    mp4 = MetadataComplianceInspector(
        exiftool=exiftool,
        exiftool_config=str(EXIFTOOL_CONFIG)).inspect(CLEAN_MP4)
    md = _md_inspector().inspect(make_markdown(tmp_path / "probe.md", "# 标题\n"))

    # 文档报告是 MP4 报告的超集：公共字段一个不少，额外只多一个 document 块
    assert set(mp4) <= set(md)
    assert set(md) - set(mp4) == {"document"}
    assert md["detector_version"] == DETECTOR_VERSION
    assert mp4["detector_version"] != md["detector_version"]   # 版本可区分模态
