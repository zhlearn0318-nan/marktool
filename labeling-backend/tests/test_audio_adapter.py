"""M4A 适配器：真实 AAC-LC 样本上的写入与音频完整性测试。"""
from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

from app.adapters import M4aAdapter
from app.adapters.base import AdapterError
from common import EXIFTOOL_CONFIG, VALID_AIGC

UPDATED_AIGC = {**VALID_AIGC, "Label": "2",
                "ProduceID": "0198F21A-6F28-7000-A102-999999999999",
                "PropagateID": "0198F21A-6F28-7000-A102-999999999999"}


def _tool(name: str, env: str) -> str:
    return os.environ.get(env, name)


@pytest.fixture
def clean_m4a(tmp_path):
    path = tmp_path / "clean.m4a"
    subprocess.run([_tool("ffmpeg", "FFMPEG_PATH"), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                    "-t", "3", "-c:a", "aac", "-profile:a", "aac_low", str(path)],
                   check=True, timeout=60)
    return path


@pytest.fixture
def adapter():
    return M4aAdapter(exiftool=_tool("exiftool", "EXIFTOOL_PATH"),
                      ffprobe=_tool("ffprobe", "FFPROBE_PATH"),
                      ffmpeg=_tool("ffmpeg", "FFMPEG_PATH"),
                      exiftool_config=str(EXIFTOOL_CONFIG))


def test_m4a_write_roundtrip_and_audio_integrity(adapter, clean_m4a, tmp_path):
    original_sha = hashlib.sha256(clean_m4a.read_bytes()).hexdigest()
    out = tmp_path / "labeled.m4a"
    adapter.write_metadata(clean_m4a, out, VALID_AIGC)

    records = adapter.detect_existing(out)
    assert len(records) == 1
    assert records[0].tag_key == "XMP:AIGC" or records[0].tag_key.endswith(":AIGC")
    assert records[0].aigc == VALID_AIGC
    assert hashlib.sha256(clean_m4a.read_bytes()).hexdigest() == original_sha

    report = adapter.media_integrity_check(clean_m4a, out)
    assert report.passed, report.reason
    assert report.before["format_name"] == report.after["format_name"]
    assert report.before["audio_stream_count"] == report.after["audio_stream_count"] == 1
    assert report.before["audio_streams"] == report.after["audio_streams"]


def test_replace_leaves_one_aigc_record(adapter, clean_m4a, tmp_path):
    first = tmp_path / "first.m4a"
    adapter.write_metadata(clean_m4a, first, VALID_AIGC)
    stripped = tmp_path / "stripped.m4a"
    adapter.remove_aigc(first, stripped)
    assert adapter.detect_existing(stripped) == []

    replaced = tmp_path / "replaced.m4a"
    adapter.write_metadata(stripped, replaced, UPDATED_AIGC)
    records = adapter.detect_existing(replaced)
    assert len(records) == 1
    assert records[0].aigc == UPDATED_AIGC


def test_no_audio_or_truncated_m4a_fails(adapter, clean_m4a, tmp_path):
    video_only = tmp_path / "video-only.mp4"
    subprocess.run([adapter.ffmpeg, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=16x16:r=1", "-t", "1", "-c:v", "mpeg4",
                    str(video_only)], check=True, timeout=60)
    report = adapter.media_integrity_check(video_only, video_only)
    assert not report.passed
    assert "音频轨" in (report.reason or "")

    truncated = tmp_path / "truncated.m4a"
    truncated.write_bytes(clean_m4a.read_bytes()[:100])
    with pytest.raises(AdapterError):
        adapter.media_integrity_check(clean_m4a, truncated)
