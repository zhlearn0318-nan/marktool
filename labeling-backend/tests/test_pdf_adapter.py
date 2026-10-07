"""PDF 载体：结构探测（pdfstruct）+ 适配器（开发手册 §9.3/§9.4）。

两层分开测：
- ``pdfstruct`` 是纯字节扫描，不依赖 ExifTool，任何环境都要跑；
- ``PdfAdapter`` 的读写走 ExifTool，沿用 test_image_adapter 的
  ``_find_exiftool()`` + skip 约定（CI 用 AIGC_REQUIRE_EXIFTOOL=1 升级为 fail）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.adapters import PdfAdapter, get_adapter
from app.adapters.base import FileContentError
from app.core import aigc, pdfstruct
from tests.common import (EXIFTOOL_CONFIG, VALID_AIGC, make_pdf,
                          make_signed_pdf)


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


@pytest.fixture
def adapter(exiftool) -> PdfAdapter:
    return PdfAdapter(exiftool=exiftool, exiftool_config=str(EXIFTOOL_CONFIG))


@pytest.fixture
def obj() -> dict:
    return aigc.normalize_first_write(VALID_AIGC)


# ---- pdfstruct：纯字节探测（无外部工具）--------------------------------

def test_scan_clean_pdf(tmp_path):
    probe = pdfstruct.scan(make_pdf(tmp_path / "a.pdf", pages=2))
    assert probe.has_eof and probe.version == "%PDF-1.4"
    assert not probe.encrypted and not probe.signed
    assert probe.writable


def test_scan_detects_signature(tmp_path):
    probe = pdfstruct.scan(make_signed_pdf(tmp_path / "s.pdf"))
    assert probe.signed and not probe.writable
    assert probe.has_eof


def test_scan_detects_encryption(tmp_path):
    p = make_pdf(tmp_path / "e.pdf")
    p.write_bytes(p.read_bytes().replace(
        b"trailer\n", b"trailer\n<< /Encrypt 9 0 R >>\n", 1))
    assert pdfstruct.scan(p).encrypted


def test_scan_detects_truncation(tmp_path):
    """截掉尾部 → %%EOF 落在窗口外，判为不可信载体。"""
    raw = make_pdf(tmp_path / "t.pdf", pages=3).read_bytes()
    p = tmp_path / "trunc.pdf"
    p.write_bytes(raw[:len(raw) // 2])
    assert pdfstruct.scan(p).has_eof is False


def test_page_count_from_tags():
    assert pdfstruct.page_count_from_tags({"PDF:PageCount": 7}) == 7
    assert pdfstruct.page_count_from_tags({"PDF:PageCount": "7"}) == 7
    assert pdfstruct.page_count_from_tags({}) is None
    assert pdfstruct.page_count_from_tags({"PDF:PageCount": "n/a"}) is None


def _probe(**kw) -> pdfstruct.PdfProbe:
    base = dict(size_bytes=1000, has_eof=True, encrypted=False, signed=False,
                version="%PDF-1.4", page_count=2)
    return pdfstruct.PdfProbe(**{**base, **kw})


def test_integrity_problems_is_empty_for_identical_probes():
    assert pdfstruct.integrity_problems(_probe(), _probe()) == []


def test_integrity_problems_flags_page_loss():
    """页数变化是"整体重写丢了对象"最直接的信号。"""
    problems = pdfstruct.integrity_problems(_probe(), _probe(page_count=1))
    assert any("页数变化" in p for p in problems)


def test_integrity_problems_flags_unreadable_page_count():
    problems = pdfstruct.integrity_problems(_probe(), _probe(page_count=None))
    assert any("无法读出页数" in p for p in problems)


def test_integrity_problems_flags_missing_eof():
    problems = pdfstruct.integrity_problems(_probe(), _probe(has_eof=False))
    assert any("%%EOF" in p for p in problems)


def test_integrity_problems_flags_shrunk_file():
    problems = pdfstruct.integrity_problems(_probe(size_bytes=1000),
                                            _probe(size_bytes=100))
    assert any("体积异常缩小" in p for p in problems)


# ---- preflight：写入前拒绝不可安全写的文件 -----------------------------

def test_preflight_accepts_clean_pdf(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    probe = PdfAdapter(exiftool="exiftool").preflight(src)
    assert probe.writable and probe.has_eof


def test_preflight_rejects_signed_pdf(tmp_path):
    """签名文档写进去就等于把签名毁掉——附录 E 的 ReservedCode 正是为保护它而设。"""
    src = make_signed_pdf(tmp_path / "s.pdf")
    with pytest.raises(FileContentError, match="签名"):
        PdfAdapter(exiftool="exiftool").preflight(src)


def test_preflight_rejects_encrypted_pdf(tmp_path):
    p = make_pdf(tmp_path / "e.pdf")
    p.write_bytes(p.read_bytes().replace(
        b"trailer\n", b"trailer\n<< /Encrypt 9 0 R >>\n", 1))
    with pytest.raises(FileContentError, match="加密"):
        PdfAdapter(exiftool="exiftool").preflight(p)


def test_preflight_rejects_truncated_pdf(tmp_path):
    raw = make_pdf(tmp_path / "t.pdf", pages=3).read_bytes()
    p = tmp_path / "trunc.pdf"
    p.write_bytes(raw[:len(raw) // 2])
    with pytest.raises(FileContentError, match="%%EOF"):
        PdfAdapter(exiftool="exiftool").preflight(p)


def test_write_metadata_refuses_signed_even_if_called_directly(tmp_path, obj):
    """预检不能只放在 API 层：别的入口建的任务也不得绕过"不破坏签名"的保证。"""
    src = make_signed_pdf(tmp_path / "s.pdf")
    with pytest.raises(FileContentError):
        PdfAdapter(exiftool="exiftool").write_metadata(src, tmp_path / "o.pdf", obj)


def test_adapter_is_registered_for_pdf():
    assert isinstance(get_adapter("application/pdf", exiftool="exiftool"),
                      PdfAdapter)


# ---- 真实 ExifTool：写入链 --------------------------------------------

def test_carrier_and_modality(adapter):
    assert adapter.carrier_id == "pdf-xmp-aigc-v1"
    assert adapter.modality == "text"


def test_clean_pdf_has_no_record(adapter, tmp_path):
    assert adapter.detect_existing(make_pdf(tmp_path / "a.pdf")) == []


def test_write_produces_exactly_one_record(adapter, tmp_path, obj):
    src = make_pdf(tmp_path / "a.pdf", pages=2)
    dst = tmp_path / "out.pdf"
    adapter.write_metadata(src, dst, obj)

    records = adapter.detect_existing(dst)
    assert len(records) == 1
    assert records[0].tag_key == "XMP:AIGC"
    assert records[0].aigc == obj


def test_write_is_idempotent(adapter, tmp_path, obj):
    src = make_pdf(tmp_path / "a.pdf")
    first, second = tmp_path / "1.pdf", tmp_path / "2.pdf"
    adapter.write_metadata(src, first, obj)
    adapter.write_metadata(first, second, obj)
    assert len(adapter.detect_existing(second)) == 1


def test_exiftool_does_not_synthesize_a_conflicting_info_key(adapter, tmp_path, obj):
    """写入后必须**恰好一条**：ExifTool 若额外合成 Document Info 键，"仅一份"就破了。

    这是桌面文档 markdown-pdf-carrier-validation.md 里那次实测的回归护栏。
    """
    src = make_pdf(tmp_path / "a.pdf")
    dst = tmp_path / "out.pdf"
    adapter.write_metadata(src, dst, obj)
    assert [r.tag_key for r in adapter.detect_existing(dst)] == ["XMP:AIGC"]


def test_remove_clears_record(adapter, tmp_path, obj):
    src = make_pdf(tmp_path / "a.pdf")
    labeled, cleaned = tmp_path / "l.pdf", tmp_path / "c.pdf"
    adapter.write_metadata(src, labeled, obj)
    adapter.remove_aigc(labeled, cleaned)
    assert adapter.detect_existing(cleaned) == []


def test_replace_flow_collapses_legacy_info_key(adapter, tmp_path, obj):
    """旧载体（Document Info 字典键）+ XMP 共存时，replace 后必须收敛成一条。

    旧载体在真实世界里的样子很朴素：有人把 AIGC JSON 塞进了 PDF 的 Keywords
    （值里含 "AIGC" 字样，正是 ``_looks_like_aigc`` 的判据）。检测侧要认它，
    replace 时也要能一并清掉——否则"仅一份"永远做不到。
    """
    src = make_pdf(tmp_path / "a.pdf")
    seeded = tmp_path / "seeded.pdf"
    raw = aigc.serialize_aigc(VALID_AIGC)
    adapter._exiftool_cmd(["-overwrite_original", "-PDF:Keywords=" + raw], str(src))
    adapter._exiftool_cmd(["-overwrite_original", "-XMP-aigc:AIGC=" + raw], str(src))

    # 前提：新旧两处载体都读得到（顺序由 ExifTool 决定，不约定）
    assert sorted(r.tag_key for r in adapter.detect_existing(src)) == ["PDF:Keywords", "XMP:AIGC"]

    adapter.remove_aigc(src, seeded)
    assert adapter.detect_existing(seeded) == []

    out = tmp_path / "out.pdf"
    adapter.write_metadata(seeded, out, obj)
    assert [r.tag_key for r in adapter.detect_existing(out)] == ["XMP:AIGC"]


def test_media_integrity_passes_and_keeps_page_count(adapter, tmp_path, obj):
    src = make_pdf(tmp_path / "a.pdf", pages=3)
    dst = tmp_path / "out.pdf"
    adapter.write_metadata(src, dst, obj)

    report = adapter.media_integrity_check(src, dst, duration_tolerance=0.1)
    assert report.passed, report.reason
    assert report.before["page_count"] == 3
    assert report.after["page_count"] == 3
    assert report.after["eof_marker_present"] is True


def test_media_integrity_fails_when_output_is_truncated(adapter, tmp_path, obj):
    src = make_pdf(tmp_path / "a.pdf", pages=3)
    dst = tmp_path / "out.pdf"
    adapter.write_metadata(src, dst, obj)
    raw = dst.read_bytes()
    dst.write_bytes(raw[:len(raw) // 2])              # 模拟写出后尾部损坏

    report = adapter.media_integrity_check(src, dst, duration_tolerance=0.1)
    assert not report.passed and "%EOF" in report.reason


def test_write_does_not_touch_source(adapter, tmp_path, obj):
    """副本输出：源文件（含其页数）必须原封不动。"""
    src = make_pdf(tmp_path / "a.pdf", pages=2)
    before = src.read_bytes()
    adapter.write_metadata(src, tmp_path / "out.pdf", obj)
    assert src.read_bytes() == before
