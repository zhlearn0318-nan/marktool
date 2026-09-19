"""图片（JPEG/PNG）合规检测测试。

重点验证两件事：
  1. 图片报告与 MP4 报告**同构**（同一套结论/问题码/字段），前端无需分叉渲染；
  2. 图片特有的载体问题（PNG 多份 XMP 块、JPEG Extended XMP、无法解码）
     能落到与 MP4 等价的结论上，不漏判成"无标识"。
"""
from __future__ import annotations

import json

import pytest
from PIL import Image

from app.adapters.image import ImageAdapter
from app.core import aigc as aigc_mod
from app.core import inspector as insp
from app.core.image_inspector import ImageComplianceInspector
from common import (VALID_AIGC, insert_before_iend, itxt_chunk, make_jpeg,
                    make_png, xmp_packet)


@pytest.fixture
def inspector(exiftool_config):
    return ImageComplianceInspector(exiftool="exiftool",
                                    exiftool_config=exiftool_config)


@pytest.fixture
def adapter(exiftool_config):
    return ImageAdapter(exiftool="exiftool", exiftool_config=exiftool_config)


@pytest.fixture
def jpeg(tmp_path):
    return make_jpeg(tmp_path / "clean.jpg")


@pytest.fixture
def png(tmp_path):
    return make_png(tmp_path / "clean.png")


def _codes(report: dict) -> set[str]:
    return {i["code"] for i in report["issues"]}


# ---- 基本结论 ----

@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_clean_image_not_found(inspector, request, fmt):
    """无标识图片：not_found，高置信度。"""
    src = request.getfixturevalue(fmt)
    report = inspector.inspect(src, file_name=src.name)
    assert report["conclusion"] == insp.CONCLUSION_NOT_FOUND
    assert report["record_count"] == 0
    assert report["confidence"] == insp.CONFIDENCE_HIGH
    assert report["media_status"] == insp.MEDIA_OK


@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_labeled_image_compliant(inspector, adapter, request, fmt, tmp_path):
    """写入合规标识后：compliant，且回读到与提交一致的字段。"""
    src = request.getfixturevalue(fmt)
    out = tmp_path / f"labeled.{fmt}"
    adapter.write_metadata(src, out, VALID_AIGC)

    report = inspector.inspect(out, file_name=out.name)
    assert report["conclusion"] == insp.CONCLUSION_COMPLIANT
    assert report["reason_code"] is None
    assert report["record_count"] == 1
    # parsed_fields 是"解析出的字段名列表"，不是字段值字典
    assert set(report["candidates"][0]["parsed_fields"]) == set(VALID_AIGC)


def test_report_is_isomorphic_with_mp4(inspector, jpeg):
    """图片报告必须具备 MP4 报告的全部字段（前端同构渲染的前提）。"""
    report = inspector.inspect(jpeg, file_name=jpeg.name)
    expected = {
        "request_id", "file_name", "detected_mime_type", "size_bytes", "sha256",
        "record_count", "candidates", "issues", "conclusion", "reason_code",
        "repairability", "c2pa_presence", "media_status", "confidence",
        "media", "bmff", "registry", "detector_version", "exiftool_version",
        "elapsed_ms",
    }
    assert expected <= set(report)
    assert report["detected_mime_type"] == "image/jpeg"
    # 图片没有 BMFF box 树：字段保留但标注不适用，前端据此隐藏该区块
    assert report["bmff"]["applicable"] is False
    assert report["detector_version"].startswith("image-compliance-inspector/")


def test_no_mp4_specific_wording_leaks(inspector, adapter, jpeg, tmp_path):
    """面向人的文案不得出现 MP4 专有措辞，否则会误导诊断。

    `bmff` 块里的 `has_moov` 等**键名**是有意保留的：报告结构保持同构，
    前端靠 `applicable: false` 整块隐藏。所以只检查给人读的字段。
    """
    out = tmp_path / "labeled.jpg"
    adapter.write_metadata(jpeg, out, VALID_AIGC)
    report = inspector.inspect(out)

    prose = [i["message"] for i in report["issues"]]
    prose += [str(v) for v in report["media"].values()]
    prose.append(json.dumps(report["bmff"].get("note", ""), ensure_ascii=False))
    blob = "\n".join(prose)
    for word in ("moov", "ftyp", "mdat", "BMFF box 结构越界", "uuid 盒"):
        assert word not in blob, f"图片文案里出现了 MP4 措辞：{word}"


# ---- 结构错误（复用 MP4 的问题码）----

def test_bad_json_noncompliant(inspector, png, tmp_path):
    """AIGC 值不是合法 JSON → noncompliant / BAD_JSON。"""
    broken = xmp_packet('{"AIGC": {"Label": "1"')
    dup = tmp_path / "badjson.png"
    dup.write_bytes(insert_before_iend(png.read_bytes(), itxt_chunk(broken)))

    report = inspector.inspect(dup, file_name=dup.name)
    assert report["conclusion"] == insp.CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "BAD_JSON"


def test_missing_field_noncompliant(inspector, png, tmp_path):
    """缺少七字段之一 → noncompliant / MISSING_FIELD。"""
    partial = dict(VALID_AIGC)
    del partial["ContentPropagator"]
    xmp = xmp_packet(aigc_mod.serialize_aigc(partial))
    path = tmp_path / "missing.png"
    path.write_bytes(insert_before_iend(png.read_bytes(), itxt_chunk(xmp)))

    report = inspector.inspect(path, file_name=path.name)
    assert report["conclusion"] == insp.CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "MISSING_FIELD"
    assert "MISSING_FIELD" in _codes(report)


def test_duplicate_xmp_chunks_noncompliant_with_disagreement(inspector, png,
                                                             tmp_path):
    """PNG 两份 XMP 块 → DUPLICATE_RECORDS；内容不同时附 FIELDS_DISAGREE。

    ExifTool 只回读一份，若没有 image_reader 逐块扫描，这里会误判成 compliant。
    """
    other = dict(VALID_AIGC, ProduceID="0198F21A-6F28-7000-A102-BBBBBBBBBBBB")
    data = png.read_bytes()
    data = insert_before_iend(
        data, itxt_chunk(xmp_packet(aigc_mod.serialize_aigc(VALID_AIGC))))
    data = insert_before_iend(
        data, itxt_chunk(xmp_packet(aigc_mod.serialize_aigc(other))))
    dup = tmp_path / "dup.png"
    dup.write_bytes(data)

    report = inspector.inspect(dup, file_name=dup.name)
    assert report["conclusion"] == insp.CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "DUPLICATE_RECORDS"
    assert report["record_count"] == 2
    assert "FIELDS_DISAGREE" in _codes(report)
    assert "ProduceID" in json.dumps(report["issues"], ensure_ascii=False)
    # 每条候选都要带位置，供前端定位"到底哪一份出问题"
    assert all(c.get("location") for c in report["candidates"])


def test_identical_duplicates_are_auto_fixable(inspector, png, tmp_path):
    """两份完全相同 → 仍判多份，但可自动去重（REPAIR_AUTO）。"""
    xmp = xmp_packet(aigc_mod.serialize_aigc(VALID_AIGC))
    data = png.read_bytes()
    data = insert_before_iend(data, itxt_chunk(xmp))
    data = insert_before_iend(data, itxt_chunk(xmp))
    dup = tmp_path / "dup_same.png"
    dup.write_bytes(data)

    report = inspector.inspect(dup, file_name=dup.name)
    assert report["conclusion"] == insp.CONCLUSION_NONCOMPLIANT
    assert report["repairability"] == insp.REPAIR_AUTO


# ---- 载体不可靠 → 不得当"无标识"定论 ----

def test_undecodable_image_indeterminate(inspector, jpeg, tmp_path):
    """图片无法解码 → indeterminate / UNREADABLE_CARRIER，而非 not_found。"""
    broken = tmp_path / "broken.png"
    broken.write_bytes(make_png(tmp_path / "tmp.png").read_bytes()[:80])

    report = inspector.inspect(broken, file_name=broken.name)
    assert report["conclusion"] == insp.CONCLUSION_INDETERMINATE
    assert report["reason_code"] == "UNREADABLE_CARRIER"
    assert report["confidence"] == insp.CONFIDENCE_LOW


def test_extended_xmp_indeterminate(inspector, jpeg, tmp_path):
    """存在 Adobe Extended XMP 分段且标准 XMP 读不到 → indeterminate。"""
    # 在 SOI 之后插入一个 Extended XMP APP1 段
    payload = b"http://ns.adobe.com/xmp/extension/\x00" + b"\x00" * 40
    segment = (b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload)
    ext = tmp_path / "extended.jpg"
    raw = jpeg.read_bytes()
    ext.write_bytes(raw[:2] + segment + raw[2:])

    report = inspector.inspect(ext, file_name=ext.name)
    assert report["conclusion"] == insp.CONCLUSION_INDETERMINATE
    assert report["reason_code"] == "UNREADABLE_CARRIER"


# ---- C2PA 存在性 ----

@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_c2pa_absent_on_plain_image(inspector, request, fmt):
    src = request.getfixturevalue(fmt)
    report = inspector.inspect(src, file_name=src.name)
    assert report["c2pa_presence"] == insp.C2PA_ABSENT


def test_c2pa_detected_in_png_cabx_chunk(inspector, png, tmp_path):
    """PNG 的 caBX 块 → present_unverified（只判存在，不验签）。"""
    import struct
    import zlib as _zlib

    payload = b"c2pa-test-payload"
    chunk = (struct.pack(">I", len(payload)) + b"caBX" + payload
             + struct.pack(">I", _zlib.crc32(b"caBX" + payload) & 0xFFFFFFFF))
    path = tmp_path / "c2pa.png"
    path.write_bytes(insert_before_iend(png.read_bytes(), chunk))

    report = inspector.inspect(path, file_name=path.name)
    assert report["c2pa_presence"] == insp.C2PA_PRESENT


# ---- 媒体签名 ----

def test_media_signature_reports_dimensions(inspector, jpeg):
    report = inspector.inspect(jpeg, file_name=jpeg.name)
    media = report["media"]
    assert media["width"] == 64
    assert media["height"] == 48
    assert media["mode"] == "RGB"
    assert media["pixels_sha256"]
