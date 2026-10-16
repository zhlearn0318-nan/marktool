"""Audio must satisfy the shared registry contract introduced by the text branch."""
import os
import subprocess

import pytest

from app.adapters import get_adapter
from common import EXIFTOOL_CONFIG, VALID_AIGC


@pytest.mark.parametrize("mime,suffix,codec", [
    ("audio/mp4", ".m4a", "aac"),
    ("audio/mpeg", ".mp3", "libmp3lame"),
])
def test_audio_fingerprint_survives_metadata_and_distinguishes_content(tmp_path, mime, suffix, codec):
    ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
    adapter = get_adapter(mime, exiftool="exiftool", ffmpeg=ffmpeg,
                          ffprobe=os.environ.get("FFPROBE_PATH", "ffprobe"),
                          exiftool_config=str(EXIFTOOL_CONFIG))
    originals = []
    for frequency in (440, 880):
        source = tmp_path / f"tone-{frequency}{suffix}"
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={frequency}:sample_rate=44100", "-t", "1",
                        "-c:a", codec, str(source)], check=True, timeout=60)
        originals.append(source)
    before = adapter.content_fingerprint(originals[0])
    assert before != adapter.content_fingerprint(originals[1])
    labeled = tmp_path / f"labeled{suffix}"
    adapter.write_metadata(originals[0], labeled, VALID_AIGC)
    assert adapter.content_fingerprint(labeled) == before
    stripped = tmp_path / f"stripped{suffix}"
    adapter.remove_aigc(labeled, stripped)
    assert adapter.content_fingerprint(stripped) == before
