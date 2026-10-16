"""M4A 通过既有 metadata-label-jobs 接口的集成测试。"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from common import EXIFTOOL_CONFIG, VALID_AIGC

UPDATED_AIGC = {**VALID_AIGC, "Label": "2",
                "ProduceID": "0198F21A-6F28-7000-A102-999999999999",
                "PropagateID": "0198F21A-6F28-7000-A102-999999999999"}


def _tool(name: str, env: str) -> str:
    return os.environ.get(env, name)


@pytest.fixture
def m4a_bytes(tmp_path):
    path = tmp_path / "upload.m4a"
    subprocess.run([_tool("ffmpeg", "FFMPEG_PATH"), "-y", "-v", "error",
                    "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
                    "-t", "3", "-c:a", "aac", "-profile:a", "aac_low", str(path)],
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


def _upload(client, content: bytes, aigc: dict, modality="audio", policy="reject", name="sound.m4a"):
    request = json.dumps({"standard": "GB45438-2025", "modality": modality,
                          "existing_metadata_policy": policy, "AIGC": aigc})
    return client.post("/api/v1/metadata-label-jobs",
                       files={"file": (name, content, "audio/mp4")},
                       data={"request": request})


def _wait_terminal(client, job_id):
    deadline = time.time() + 30
    while time.time() < deadline:
        response = client.get(f"/api/v1/metadata-label-jobs/{job_id}")
        body = response.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(.1)
    raise AssertionError("M4A 任务超时")


def test_m4a_api_roundtrip_download_and_audio_capability(client, m4a_bytes):
    assert client.get("/api/v1/health").json()["capabilities"]["audio/mp4"] is True
    input_sha = hashlib.sha256(m4a_bytes).hexdigest()
    accepted = _upload(client, m4a_bytes, VALID_AIGC)
    assert accepted.status_code == 202, accepted.text
    done = _wait_terminal(client, accepted.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert done["input"]["sha256"] == input_sha
    assert done["output"]["mime_type"] == "audio/mp4"
    assert done["output"]["file_name"].endswith(".m4a")
    assert done["output"]["carrier"] == "m4a-aigc-v1"
    assert done["embedded_metadata"]["AIGC"] == VALID_AIGC
    assert done["validation"]["single_aigc_record"] is True
    download = client.get(done["links"]["output"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/mp4")


def test_m4a_video_modality_mismatch_and_inspection_unsupported(client, m4a_bytes):
    mismatch = _upload(client, m4a_bytes, VALID_AIGC, modality="video")
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "MODALITY_MISMATCH"
    inspected = client.post("/api/v1/compliance-inspect",
                            files={"file": ("sound.m4a", m4a_bytes, "audio/mp4")})
    assert inspected.status_code == 415
    assert inspected.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_m4a_existing_reject_and_replace_leave_one_record(client, m4a_bytes):
    """已有 XMP：reject 不建任务，replace 清除旧记录并只保留新记录。"""
    first = _upload(client, m4a_bytes, VALID_AIGC)
    assert first.status_code == 202, first.text
    initial = _wait_terminal(client, first.json()["job_id"])
    assert initial["status"] == "succeeded", initial
    labeled = client.get(initial["links"]["output"])
    assert labeled.status_code == 200

    rejected = _upload(client, labeled.content, UPDATED_AIGC)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AIGC_METADATA_EXISTS"

    replaced = _upload(client, labeled.content, UPDATED_AIGC, policy="replace")
    assert replaced.status_code == 202, replaced.text
    done = _wait_terminal(client, replaced.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert done["embedded_metadata"]["AIGC"] == UPDATED_AIGC
    assert done["validation"]["single_aigc_record"] is True


def test_mp4_content_named_m4a_does_not_enter_audio_adapter(client):
    # BMFF video brand must win over extension and MIME declaration.
    mp4_head = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    response = _upload(client, mp4_head + b"x" * 100, VALID_AIGC, name="actually-video.m4a")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODALITY_MISMATCH"
