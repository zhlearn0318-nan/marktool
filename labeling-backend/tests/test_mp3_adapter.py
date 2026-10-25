"""MP3 ID3v2 TXXX:AIGC 适配器测试（临时生成真实 MP3，不提交媒体样本）。"""
from __future__ import annotations

import hashlib
import os
import subprocess

import pytest
from mutagen.id3 import APIC, ID3, TXXX, TALB, TIT2, TPE1

from app.adapters import Mp3Adapter
from app.adapters.base import AdapterError
from common import VALID_AIGC

UPDATED_AIGC = {**VALID_AIGC, "Label": "2",
                "ProduceID": "0198F21A-6F28-7000-A102-999999999999",
                "PropagateID": "0198F21A-6F28-7000-A102-999999999999"}


def _tool(name: str, env: str) -> str:
    return os.environ.get(env, name)


@pytest.fixture
def clean_mp3(tmp_path):
    path = tmp_path / "clean.mp3"
    subprocess.run([_tool("ffmpeg", "FFMPEG_PATH"), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                    "-t", "3", "-c:a", "libmp3lame", "-q:a", "4", str(path)],
                   check=True, timeout=60)
    return path


@pytest.fixture
def adapter():
    return Mp3Adapter(ffprobe=_tool("ffprobe", "FFPROBE_PATH"),
                      ffmpeg=_tool("ffmpeg", "FFMPEG_PATH"))


def _add_non_aigc_id3(path):
    tags = ID3(str(path))
    tags.add(TIT2(encoding=3, text=["保留标题"]))
    tags.add(TPE1(encoding=3, text=["保留作者"]))
    tags.add(TALB(encoding=3, text=["保留专辑"]))
    tags.add(TXXX(encoding=3, desc="Other", text=["keep-me"]))
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=b"cover-bytes"))
    tags.save(str(path), v2_version=4)


def test_mp3_write_roundtrip_unique_and_audio_integrity(adapter, clean_mp3, tmp_path):
    original_sha = hashlib.sha256(clean_mp3.read_bytes()).hexdigest()
    out = tmp_path / "labeled.mp3"
    adapter.write_metadata(clean_mp3, out, VALID_AIGC)

    records = adapter.detect_existing(out)
    assert len(records) == 1
    assert records[0].tag_key == "ID3:TXXX:AIGC"
    assert records[0].aigc == VALID_AIGC
    assert hashlib.sha256(clean_mp3.read_bytes()).hexdigest() == original_sha

    tags = ID3(str(out))
    frames = [f for f in tags.getall("TXXX") if f.desc == "AIGC"]
    assert len(frames) == 1
    report = adapter.media_integrity_check(clean_mp3, out)
    assert report.passed, report.reason
    assert report.before == report.after


def test_mp3_replace_only_changes_aigc_and_preserves_other_id3(adapter, clean_mp3, tmp_path):
    _add_non_aigc_id3(clean_mp3)
    first = tmp_path / "first.mp3"
    adapter.write_metadata(clean_mp3, first, VALID_AIGC)
    stripped = tmp_path / "stripped.mp3"
    adapter.remove_aigc(first, stripped)
    assert adapter.detect_existing(stripped) == []

    tags = ID3(str(stripped))
    assert str(tags.get("TIT2")) == "保留标题"
    assert str(tags.get("TPE1")) == "保留作者"
    assert str(tags.get("TALB")) == "保留专辑"
    assert any(f.desc == "Other" and f.text == ["keep-me"] for f in tags.getall("TXXX"))
    assert tags.get("APIC:cover") is not None

    replaced = tmp_path / "replaced.mp3"
    adapter.write_metadata(stripped, replaced, UPDATED_AIGC)
    records = adapter.detect_existing(replaced)
    assert len(records) == 1
    assert records[0].aigc == UPDATED_AIGC
    final_tags = ID3(str(replaced))
    assert str(final_tags.get("TIT2")) == "保留标题"
    assert any(f.desc == "Other" and f.text == ["keep-me"] for f in final_tags.getall("TXXX"))
    assert final_tags.get("APIC:cover") is not None


def test_mp3_no_audio_or_truncated_file_fails(adapter, clean_mp3, tmp_path):
    fake = tmp_path / "fake.mp3"
    fake.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00not-mpeg")
    with pytest.raises(AdapterError):
        adapter.media_integrity_check(clean_mp3, fake)

    truncated = tmp_path / "truncated.mp3"
    truncated.write_bytes(clean_mp3.read_bytes()[:100])
    with pytest.raises(AdapterError):
        adapter.media_integrity_check(clean_mp3, truncated)
