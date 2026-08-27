"""视频（MP4）适配器测试 —— 文件测试矩阵（开发手册 §15.3 MP4 列）。

必测项：无标识正常写入 / 已有标识+reject / 已有标识+replace / 损坏或截断 /
写入后回读一致 / 只有一份 AIGC / 媒体内容保持可用。
"""
from __future__ import annotations

import hashlib

import pytest

from app.adapters import Mp4Adapter
from app.adapters.base import AdapterError
from common import CLEAN_MP4, LEGACY_MP4, VALID_AIGC


@pytest.fixture
def adapter(exiftool_config):
    return Mp4Adapter(exiftool="exiftool", ffprobe="ffprobe",
                      exiftool_config=exiftool_config)


def test_fresh_write_roundtrip(adapter, tmp_path):
    """无标识文件正常写入：回读一致、仅一份、媒体完整。"""
    dst = tmp_path / "out.mp4"
    adapter.write_metadata(CLEAN_MP4, dst, VALID_AIGC)

    records = adapter.detect_existing(dst)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC
    assert records[0].tag_key == "XMP:AIGC" or records[0].tag_key.endswith(":AIGC")

    media = adapter.media_integrity_check(CLEAN_MP4, dst)
    assert media.passed, media.reason
    # 轨道/编解码器/分辨率逐项一致（不转码）
    assert media.before["streams"] == media.after["streams"]


def test_existing_reject_detected(adapter):
    """已有标识必须能被检测到（旧载体 QuickTime:Comment 里的 AIGC）。"""
    records = adapter.detect_existing(LEGACY_MP4)
    assert len(records) >= 1


def test_write_over_existing_creates_duplicate(adapter, tmp_path):
    """已有标识时直接写入会得到多份（对应国标『仅一份』约束，必须由策略拦截）。"""
    dst = tmp_path / "dup.mp4"
    adapter.write_metadata(LEGACY_MP4, dst, VALID_AIGC)
    assert len(adapter.detect_existing(dst)) >= 2


def test_replace_removes_all_and_single_result(adapter, tmp_path):
    """replace：整体移除全部旧标识后写入一份新标识。"""
    no_aigc = tmp_path / "no_aigc.mp4"
    adapter.remove_aigc(LEGACY_MP4, no_aigc)
    assert adapter.detect_existing(no_aigc) == []

    out = tmp_path / "replaced.mp4"
    adapter.write_metadata(no_aigc, out, VALID_AIGC)
    records = adapter.detect_existing(out)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC

    media = adapter.media_integrity_check(LEGACY_MP4, out)
    assert media.passed, media.reason


def test_media_signature_unchanged(adapter, tmp_path):
    """写入前后媒体签名逐项一致（不转码的证明）。"""
    dst = tmp_path / "sig.mp4"
    adapter.write_metadata(CLEAN_MP4, dst, VALID_AIGC)
    before = adapter._ffprobe_json(CLEAN_MP4)
    after = adapter._ffprobe_json(dst)
    bs = {s["codec_type"]: s for s in before.get("streams", [])}
    af = {s["codec_type"]: s for s in after.get("streams", [])}
    for kind in ("video", "audio"):
        assert bs[kind]["codec_name"] == af[kind]["codec_name"]
    assert abs(float(before["format"]["duration"]) - float(after["format"]["duration"])) < 0.1


def test_corrupted_file_fails_media_check(adapter, tmp_path):
    """损坏/截断文件：媒体完整性校验必须失败（§9.4 播放可用性）。"""
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(CLEAN_MP4.read_bytes()[:100])
    with pytest.raises(AdapterError):
        adapter.media_integrity_check(CLEAN_MP4, bad)


def test_source_not_modified(adapter, tmp_path):
    """操作绝不修改原文件（§4.4）。"""
    before = hashlib.sha256(CLEAN_MP4.read_bytes()).hexdigest()
    dst = tmp_path / "out.mp4"
    adapter.write_metadata(CLEAN_MP4, dst, VALID_AIGC)
    assert hashlib.sha256(CLEAN_MP4.read_bytes()).hexdigest() == before
