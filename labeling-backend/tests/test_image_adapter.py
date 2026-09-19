"""图片（JPEG/PNG）适配器测试 —— 文件测试矩阵（开发手册 §15.3 图片列）。

必测项：无标识正常写入 / 已有标识+reject / 已有标识+replace / 扩展名伪装 /
损坏或截断 / 写入后回读一致 / 只有一份 AIGC / 像素与无关元数据保持。
"""
from __future__ import annotations

import hashlib

import pytest
from PIL import Image

from app.adapters.base import AdapterError
from app.adapters.image import ImageAdapter
from app.core import aigc as aigc_mod
from app.core import image_reader
from app.core.mimetype import detect_mime
from common import (VALID_AIGC, insert_before_iend, itxt_chunk, make_jpeg,
                    make_png, xmp_packet)


@pytest.fixture
def adapter(exiftool_config):
    return ImageAdapter(exiftool="exiftool", exiftool_config=exiftool_config)


@pytest.fixture
def jpeg(tmp_path):
    return make_jpeg(tmp_path / "clean.jpg")


@pytest.fixture
def png(tmp_path):
    return make_png(tmp_path / "clean.png")


# ---- 写入 / 回读 ----

@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_fresh_write_roundtrip(adapter, request, fmt, tmp_path):
    """无标识文件正常写入：回读一致、仅一份、媒体完整。"""
    src = request.getfixturevalue(fmt)
    dst = tmp_path / f"out.{fmt}"

    adapter.write_metadata(src, dst, VALID_AIGC)

    records = adapter.detect_existing(dst)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC

    media = adapter.media_integrity_check(src, dst)
    assert media.passed, media.reason
    assert media.before["width"] == media.after["width"]
    assert media.before["pixels_sha256"] == media.after["pixels_sha256"]


@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_carrier_namespace_is_team_baseline(adapter, request, fmt, tmp_path):
    """落盘命名空间必须是团队基线 http://example.com/aigc#（与视频一致）。"""
    src = request.getfixturevalue(fmt)
    dst = tmp_path / f"ns.{fmt}"
    adapter.write_metadata(src, dst, VALID_AIGC)
    assert b"http://example.com/aigc#" in dst.read_bytes()


def test_existing_detected_after_write(adapter, jpeg, tmp_path):
    """已有标识必须能被检测到（reject 策略据此拦截）。"""
    once = tmp_path / "once.jpg"
    adapter.write_metadata(jpeg, once, VALID_AIGC)
    assert len(adapter.detect_existing(once)) == 1


def test_replace_removes_all_and_single_result(adapter, jpeg, tmp_path):
    """replace：整体移除全部旧标识后写入一份新标识。"""
    once = tmp_path / "once.jpg"
    adapter.write_metadata(jpeg, once, VALID_AIGC)

    stripped = tmp_path / "stripped.jpg"
    adapter.remove_aigc(once, stripped)
    assert adapter.detect_existing(stripped) == []

    out = tmp_path / "replaced.jpg"
    adapter.write_metadata(stripped, out, VALID_AIGC)
    records = adapter.detect_existing(out)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC
    assert adapter.media_integrity_check(jpeg, out).passed


def test_source_not_modified(adapter, jpeg, tmp_path):
    """操作绝不修改原文件（§4.4）。"""
    before = hashlib.sha256(jpeg.read_bytes()).hexdigest()
    adapter.write_metadata(jpeg, tmp_path / "out.jpg", VALID_AIGC)
    assert hashlib.sha256(jpeg.read_bytes()).hexdigest() == before


# ---- 媒体完整性（§6.3）----

def test_pixel_change_fails_integrity(adapter, jpeg, tmp_path):
    """像素被改动（重新编码）必须判失败 —— 只看宽高是发现不了的。"""
    changed = tmp_path / "changed.jpg"
    im = Image.open(jpeg)
    im.putpixel((0, 0), (0, 255, 0))
    im.save(changed, format="JPEG", quality=90)

    media = adapter.media_integrity_check(jpeg, changed)
    assert not media.passed
    assert "像素" in media.reason


def test_dimension_change_fails_integrity(adapter, jpeg, tmp_path):
    """尺寸变化必须判失败。"""
    resized = tmp_path / "resized.jpg"
    Image.open(jpeg).resize((128, 96)).save(resized, format="JPEG")

    media = adapter.media_integrity_check(jpeg, resized)
    assert not media.passed
    assert "宽度" in media.reason or "高度" in media.reason


def test_corrupted_file_fails_media_check(adapter, jpeg, tmp_path):
    """损坏/截断文件：媒体完整性校验必须失败（§9.4）。"""
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(jpeg.read_bytes()[:60])
    with pytest.raises(AdapterError):
        adapter.media_integrity_check(jpeg, bad)


# ---- 扩展名伪装（§9.1：按内容而非扩展名）----

def test_extension_spoofing_uses_real_mime(adapter, png, tmp_path):
    """PNG 伪装成 .jpg：按**内容**判定真实类型，落点仍是 PNG 承载。

    流水线落盘用的是按文件头判出的真实后缀（pipeline 里 `suffix_for_mime`），
    所以这里也按真实类型给目标名 —— 若照扩展名写成 .jpg，exiftool 自己会
    以 "Not a valid JPG" 拒绝写入。
    """
    spoofed = tmp_path / "actually_png.jpg"
    spoofed.write_bytes(png.read_bytes())
    assert detect_mime(spoofed.read_bytes()[:16]) == "image/png"

    out = tmp_path / "out_spoofed.png"
    adapter.write_metadata(spoofed, out, VALID_AIGC)
    records = adapter.detect_existing(out)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC
    # 承载仍是 PNG：XMP 落在 iTXt 块里，而不是被改写成 JPEG
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(image_reader.scan_png_xmp_packets(str(out))) == 1


# ---- PNG 多份同名 XMP 块（本模块存在的理由）----

def test_png_duplicate_xmp_chunks_all_detected(adapter, png, tmp_path):
    """PNG 里两份同名 XMP 块必须都被读出 —— ExifTool 只回读一份。

    这是 image_reader 逐块扫描存在的理由：检测器要靠"多份"判 DUPLICATE_RECORDS。
    """
    first = xmp_packet(aigc_mod.serialize_aigc(VALID_AIGC))
    second_payload = dict(VALID_AIGC, ProduceID="0198F21A-6F28-7000-A102-AAAAAAAAAAAA")
    second = xmp_packet(aigc_mod.serialize_aigc(second_payload))

    data = png.read_bytes()
    data = insert_before_iend(data, itxt_chunk(first))
    data = insert_before_iend(data, itxt_chunk(second))
    dup = tmp_path / "dup.png"
    dup.write_bytes(data)

    packets = image_reader.scan_png_xmp_packets(str(dup))
    assert len(packets) == 2, f"应逐块读出 2 份 XMP，实际 {len(packets)}"

    records = adapter.detect_existing(dup)
    assert len(records) == 2, f"应检出 2 份记录，实际 {len(records)}"
    # 两份位置不同 → 位置标签必须能区分
    assert len({r.location for r in records}) == 2
    assert {r.aigc["ProduceID"] for r in records} == {
        VALID_AIGC["ProduceID"], second_payload["ProduceID"]}


def test_png_single_packet_no_false_duplicate(adapter, png, tmp_path):
    """只有一份 XMP 时不得误报多份。"""
    out = tmp_path / "single.png"
    adapter.write_metadata(png, out, VALID_AIGC)
    assert len(image_reader.scan_png_xmp_packets(str(out))) == 1
    assert len(adapter.detect_existing(out)) == 1


def test_namespace_declaration_is_not_an_aigc_record():
    """命名空间声明 `xmlns:aigc=…` 不是 AIGC 属性，不得被当成一份标识。

    回归用例：包损坏走正则兜底时，属性名模式若不加词边界，会从 `xmlns:aigc`
    的中间开始匹配，把一个纯声明读成"又一份标识"——后果是干净文件被误报
    成多份/字段冲突。
    """
    # 故意的非法 XML：属性值里有未转义的引号 → ElementTree 解析失败走兜底
    packet = (
        "<rdf:Description xmlns:aigc='http://example.com/aigc#' "
        'aigc:AIGC="{"AIGC":{"Label":"1"}}"/>'
    )
    values = image_reader.extract_aigc_values(packet)

    names = [name for name, _ in values]
    assert not any("xmlns" in n for n in names), f"命名空间声明被当成了记录: {names}"
    assert not any(v.startswith("http://example.com/aigc#") for _, v in values), values
    # 只剩真正的 aigc:AIGC（值本身是坏的，由 BAD_JSON 判定，不在这里）
    assert len(values) == 1, values
    assert names[0].endswith("AIGC")


# ---- 位置文案必须是图片语境 ----

def test_location_wording_is_image_specific(adapter, jpeg, tmp_path):
    """位置文案不得沿用 MP4 的"顶层 Adobe-XMP uuid 盒"措辞。"""
    out = tmp_path / "loc.jpg"
    adapter.write_metadata(jpeg, out, VALID_AIGC)
    records = adapter.detect_existing(out)
    assert records[0].location is not None
    assert "uuid" not in records[0].location
    assert "APP1" in records[0].location
