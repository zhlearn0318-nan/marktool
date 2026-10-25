"""第二遍读取（独立于 ExifTool 的那条路）。

修复会自动改写文件，所以动手前必须确认"这份文件里到底有几处标识"。图片靠
两条独立路径互相印证；视频与文档没有现成的第二条，由 ``cross_read`` 现造。
本文件要证明的正是**它真的独立**、以及它读不到的边界：

* 独立：ffprobe 看不见 XMP uuid box（实测），字节扫描看不见 QuickTime 原生
  标签——两条合起来才覆盖 MP4 的两类载体，任何一条都不能单独代表"读全了"；
* 边界：字节扫描只看明文 XML，读不到就报"两条路径不一致"（进而转人工），
  而不是报"文件里没有标识"——后者会让一份藏着标识的文件被当成干净文件。
"""
from __future__ import annotations

import shutil

import pytest

from app.adapters import Mp4Adapter, PdfAdapter
from app.core import aigc
from app.core.raw_xmp_scan import scan_aigc_elements
from app.core.reader import AIGCRecord
from app.metadata.cross_read import (MARKDOWN_CROSS_READ, cross_read_mp4,
                                     cross_read_pdf)
from tests.common import CLEAN_MP4, EXIFTOOL_CONFIG, VALID_AIGC, make_pdf

AIGC_JSON = aigc.serialize_aigc(VALID_AIGC)


@pytest.fixture
def mp4_adapter():
    return Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))


def _labeled_mp4(path, adapter, *, legacy=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CLEAN_MP4, path)
    adapter._exiftool_cmd(["-overwrite_original", "-XMP-aigc:AIGC=" + AIGC_JSON],
                          str(path))
    if legacy:
        adapter._exiftool_cmd(["-overwrite_original", "-QuickTime:Comment=" + AIGC_JSON],
                              str(path))
    return path


# ---- 字节扫描本身 ----------------------------------------------------------

def test_scan_reads_the_xmp_element_and_unescapes_it(tmp_path, mp4_adapter):
    path = _labeled_mp4(tmp_path / "a.mp4", mp4_adapter)
    values = scan_aigc_elements(path)
    assert len(values) == 1
    # XMP 里引号是 &quot;，不还原就解析不出 JSON——还原与否决定了这条路径有没有用
    assert aigc.parse_aigc(values[0]) == VALID_AIGC


def test_scan_finds_both_carrier_element_names(tmp_path, mp4_adapter):
    """标准 aigc:AIGC 与旧 aigc:metadata 都要扫到，否则双载体文件会被少算一份。"""
    adapter = PdfAdapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    source = tmp_path / "doc.pdf"
    adapter.write_metadata(make_pdf(tmp_path / "src.pdf"), source, VALID_AIGC)
    adapter._exiftool_cmd(["-overwrite_original", "-XMP-aigc:metadata=" + AIGC_JSON],
                          str(source))
    assert len(scan_aigc_elements(source)) == 2


def test_scan_survives_an_element_split_across_chunk_boundaries(tmp_path):
    """元素跨 1 MiB 读取边界时必须仍然只算一次（重叠窗口 + 绝对偏移去重）。"""
    path = tmp_path / "big.bin"
    padding = b"padding-bytes-" * 81920          # ~1.1 MB
    element = f"<aigc:AIGC>{AIGC_JSON.replace(chr(34), '&quot;')}</aigc:AIGC>"
    path.write_bytes(padding[: (1 << 20) - 30] + element.encode() + padding)

    values = scan_aigc_elements(path)
    assert len(values) == 1, f"跨块元素被算成了 {len(values)} 处"
    assert aigc.parse_aigc(values[0]) == VALID_AIGC


def test_scan_ignores_a_truncated_element(tmp_path):
    """只有开标签没有闭标签：不算一处标识，也不能抛异常。"""
    path = tmp_path / "broken.md"
    path.write_text(f"正文\n<aigc:AIGC>{AIGC_JSON}\n", encoding="utf-8")
    assert scan_aigc_elements(path) == []


# ---- 视频：两条第二读取器合起来才覆盖得住 ----------------------------------

def test_ffprobe_cannot_see_the_xmp_carrier(tmp_path, mp4_adapter):
    """这条断言是整个设计的前提：标准 XMP 标识对 ffprobe 完全不可见。"""
    path = _labeled_mp4(tmp_path / "a.mp4", mp4_adapter)
    assert len(mp4_adapter.detect_existing(path)) == 1        # ExifTool 看得见
    assert len(scan_aigc_elements(path)) == 1                 # 字节扫描看得见
    # ffprobe 只看得到原生容器标签，这里没有任何 AIGC
    from app.metadata.cross_read import _ffprobe_aigc_values
    values, error = _ffprobe_aigc_values(path, "ffprobe")
    assert error is None and values == []


@pytest.mark.parametrize("legacy", [False, True])
def test_cross_read_matches_exiftool(tmp_path, mp4_adapter, legacy):
    path = _labeled_mp4(tmp_path / "a.mp4", mp4_adapter, legacy=legacy)
    result, issues = cross_read_mp4(path, mp4_adapter.detect_existing(path),
                                    ffprobe="ffprobe")
    assert result.status == "matched", result.detail
    assert issues == []


def test_cross_read_on_a_clean_file_matches(tmp_path):
    path = tmp_path / "clean.mp4"
    shutil.copyfile(CLEAN_MP4, path)
    result, _ = cross_read_mp4(path, [], ffprobe="ffprobe")
    assert result.status == "matched"


def test_cross_read_flags_divergence(tmp_path, mp4_adapter):
    """ExifTool 说有一处、文件字节里却找不到——必须判分歧而不是放行。"""
    path = _labeled_mp4(tmp_path / "a.mp4", mp4_adapter)
    phantom = [AIGCRecord(tag_key="XMP:AIGC", raw=AIGC_JSON,
                          aigc=dict(VALID_AIGC), location=None)]
    path.write_bytes(path.read_bytes().replace(b"<aigc:AIGC>", b"<aigc:XXGC>"))

    result, issues = cross_read_mp4(path, phantom, ffprobe="ffprobe")
    assert result.status == "diverged"
    assert [i.code for i in issues] == ["AIGC_READERS_DIVERGED"]
    assert issues[0].level == "error"


def test_cross_read_without_ffprobe_blocks_instead_of_guessing(tmp_path, mp4_adapter):
    """ffprobe 缺席不是"文件没问题"，而是"少了一条读取路径"——不该自动改写。"""
    path = _labeled_mp4(tmp_path / "a.mp4", mp4_adapter)
    result, issues = cross_read_mp4(path, mp4_adapter.detect_existing(path),
                                    ffprobe="ffprobe-does-not-exist")
    assert result.status == "unavailable"
    assert [i.code for i in issues] == ["CROSS_READ_UNAVAILABLE"]


# ---- PDF 与 Markdown -------------------------------------------------------

def test_cross_read_pdf_matches(tmp_path):
    adapter = PdfAdapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    source = tmp_path / "doc.pdf"
    adapter.write_metadata(make_pdf(tmp_path / "src.pdf"), source, VALID_AIGC)
    assert cross_read_pdf(source, adapter.detect_existing(source))[0].status == "matched"


def test_markdown_declares_not_applicable():
    """已知没有第二条读取路径，不是"该做没做"——规划器只拦后者。"""
    assert MARKDOWN_CROSS_READ.status == "not_applicable"
    assert MARKDOWN_CROSS_READ.detail
