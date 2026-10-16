"""MP3 通过既有 metadata-label-jobs 接口的集成测试。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.mimetype import detect_mime
from app.main import create_app
from common import EXIFTOOL_CONFIG, VALID_AIGC

UPDATED_AIGC = {**VALID_AIGC, "Label": "2",
                "ProduceID": "0198F21A-6F28-7000-A102-999999999999",
                "PropagateID": "0198F21A-6F28-7000-A102-999999999999"}


def _tool(name: str, env: str) -> str:
    return os.environ.get(env, name)


@pytest.fixture
def mp3_bytes(tmp_path):
    path = tmp_path / "upload.mp3"
    subprocess.run([_tool("ffmpeg", "FFMPEG_PATH"), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                    "-t", "3", "-c:a", "libmp3lame", "-q:a", "4", str(path)],
                   check=True, timeout=60)
    return path.read_bytes()


@pytest.fixture
def client(tmp_path):
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = 10 * 1024 * 1024
    settings.paths.exiftool = _tool("exiftool", "EXIFTOOL_PATH")
    settings.paths.ffprobe = _tool("ffprobe", "FFPROBE_PATH")
    settings.paths.ffmpeg = _tool("ffmpeg", "FFMPEG_PATH")
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    app = create_app(settings=settings, storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    with TestClient(app) as test_client:
        yield test_client


def _upload(client, content: bytes, aigc: dict, modality="audio", policy="reject", name="sound.mp3"):
    request = json.dumps({"standard": "GB45438-2025", "modality": modality,
                          "existing_metadata_policy": policy, "AIGC": aigc})
    return client.post("/api/v1/metadata-label-jobs",
                       files={"file": (name, content, "audio/mpeg")},
                       data={"request": request})


def _wait_terminal(client, job_id):
    deadline = time.time() + 30
    while time.time() < deadline:
        body = client.get(f"/api/v1/metadata-label-jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(.1)
    raise AssertionError("MP3 任务超时")


def test_mp3_api_roundtrip_download_and_audio_capability(client, mp3_bytes):
    assert client.get("/api/v1/health").json()["capabilities"]["audio/mpeg"] is True
    input_sha = hashlib.sha256(mp3_bytes).hexdigest()
    accepted = _upload(client, mp3_bytes, VALID_AIGC)
    assert accepted.status_code == 202, accepted.text
    done = _wait_terminal(client, accepted.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert done["input"]["sha256"] == input_sha
    assert done["output"]["mime_type"] == "audio/mpeg"
    assert done["output"]["file_name"].endswith(".mp3")
    assert done["output"]["carrier"] == "mp3-id3v2-aigc-v1"
    assert done["embedded_metadata"]["AIGC"] == VALID_AIGC
    assert done["validation"]["single_aigc_record"] is True
    download = client.get(done["links"]["output"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/mpeg")


def test_mp3_content_is_detected_without_mp3_extension(client, mp3_bytes):
    # 文件内容而非文件名/MIME 声明决定适配器；真实 MP3 改名仍应进入 MP3 链路。
    accepted = _upload(client, mp3_bytes, VALID_AIGC, name="renamed.bin")
    assert accepted.status_code == 202, accepted.text
    done = _wait_terminal(client, accepted.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert done["output"]["mime_type"] == "audio/mpeg"


def test_mp4_content_named_mp3_cannot_enter_mp3_adapter(client):
    # BMFF 视频品牌必须优先于扩展名和 multipart 声明，故 audio 模态应错配。
    mp4_head = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    response = _upload(client, mp4_head + b"x" * 100, VALID_AIGC, name="actually-video.mp3")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODALITY_MISMATCH"


def test_mp3_mismatch_and_inspection_unsupported(client, mp3_bytes):
    mismatch = _upload(client, mp3_bytes, VALID_AIGC, modality="video")
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "MODALITY_MISMATCH"
    inspected = client.post("/api/v1/compliance-inspect",
                            files={"file": ("sound.mp3", mp3_bytes, "audio/mpeg")})
    assert inspected.status_code == 415
    assert inspected.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_mp3_existing_reject_and_replace_leave_one_record(client, mp3_bytes):
    first = _upload(client, mp3_bytes, VALID_AIGC)
    initial = _wait_terminal(client, first.json()["job_id"])
    assert initial["status"] == "succeeded", initial
    labeled = client.get(initial["links"]["output"])
    source_sha = hashlib.sha256(labeled.content).hexdigest()

    rejected = _upload(client, labeled.content, UPDATED_AIGC)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AIGC_METADATA_EXISTS"
    # reject 在任何副本写入前返回；调用方持有的源字节不应发生变化。
    assert hashlib.sha256(labeled.content).hexdigest() == source_sha
    replaced = _upload(client, labeled.content, UPDATED_AIGC, policy="replace")
    done = _wait_terminal(client, replaced.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert done["embedded_metadata"]["AIGC"] == UPDATED_AIGC
    assert done["validation"]["single_aigc_record"] is True


def test_fake_or_truncated_mp3_rejected(client):
    fake = b"ID3\x04\x00\x00\x00\x00\x00\x00this-is-not-mpeg"
    response = _upload(client, fake, VALID_AIGC)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_id3v24_footer_offset_is_recognized():
    # ID3v2.4 footer 不属于 tag size；真实音频帧位于 footer 之后。
    head = b"ID3\x04\x00\x10\x00\x00\x00\x00" + b"3DI" + b"\x00" * 7 + b"\xff\xfb\x90\x00"
    assert detect_mime(head) == "audio/mpeg"
