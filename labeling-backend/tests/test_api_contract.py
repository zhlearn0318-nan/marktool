"""API 契约测试（开发手册 §15.1 / §7）。

覆盖：合法创建返回 202、状态轮询到终态、结果下载、幂等、各错误码映射。
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from common import CLEAN_MP4, EXIFTOOL_CONFIG, LEGACY_MP4, VALID_AIGC

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(tmp_path):
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = 10 * 1024 * 1024
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    app = create_app(settings=settings,
                     storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    with TestClient(app) as c:
        yield c


def _req(aigc, modality="video", policy="reject", standard="GB45438-2025"):
    return json.dumps({"standard": standard, "modality": modality,
                       "existing_metadata_policy": policy, "AIGC": aigc})


def _upload(client, path_or_bytes, aigc, modality="video", policy="reject",
            fname=None, headers=None, content_type="video/mp4"):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        data = open(path_or_bytes, "rb").read()
    files = {"file": (fname or "upload.mp4", data, content_type)}
    body = {"request": _req(aigc, modality, policy)}
    return client.post("/api/v1/metadata-label-jobs", files=files, data=body,
                       headers=headers or {})


def _wait_terminal(client, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/v1/metadata-label-jobs/{job_id}")
        assert r.status_code == 200
        d = r.json()
        if d["status"] in ("succeeded", "failed"):
            return d
        time.sleep(0.2)
    raise AssertionError("任务超时未结束")


def test_health_capabilities(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["capabilities"]["video/mp4"] is True
    assert body["capabilities"]["image/png"] is False


def test_create_success_roundtrip(client):
    r = _upload(client, CLEAN_MP4, VALID_AIGC)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert body["request_id"]
    assert body["links"]["self"].endswith(body["job_id"])

    done = _wait_terminal(client, body["job_id"])
    assert done["status"] == "succeeded", done
    assert done["stage"] == "completed"
    assert done["progress"] == 100
    assert done["validation"]["read_back_succeeded"] is True
    assert done["validation"]["schema_valid"] is True
    assert done["validation"]["single_aigc_record"] is True
    assert done["validation"]["media_integrity_valid"] is True
    assert done["embedded_metadata"]["AIGC"] == VALID_AIGC
    assert done["output"]["carrier"] == "mp4-aigc-v1"
    assert done["output"]["expires_at"]

    dl = client.get(done["output"]["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("video/mp4")
    assert len(dl.content) > 0
    # 下载文件名安全化
    assert "Content-Disposition" in dl.headers


def test_missing_multipart_field_400(client):
    files = {"file": ("x.mp4", open(CLEAN_MP4, "rb").read(), "video/mp4")}
    r = client.post("/api/v1/metadata-label-jobs", files=files)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_MULTIPART"


def test_invalid_request_json_400(client):
    files = {"file": ("x.mp4", open(CLEAN_MP4, "rb").read(), "video/mp4")}
    r = client.post("/api/v1/metadata-label-jobs", files=files,
                    data={"request": "{not json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_MULTIPART"


def test_bad_aigc_422_schema(client):
    bad = {**VALID_AIGC, "Label": 1}
    r = _upload(client, CLEAN_MP4, bad)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AIGC_SCHEMA_INVALID"
    assert r.json()["error"]["field_errors"]


def test_bad_aigc_422_character(client):
    bad = {**VALID_AIGC, "ContentProducer": "中文名称"}
    r = _upload(client, CLEAN_MP4, bad)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AIGC_CHARACTER_INVALID"


def test_unsupported_type_415(client):
    r = _upload(client, b"plain text not media", VALID_AIGC,
                fname="evil.txt", content_type="text/plain")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_unimplemented_image_adapter_415(client):
    """图片适配器未实现：上传 PNG 应返回 415 而非进入处理（§7.5 能力开关）。"""
    r = _upload(client, TINY_PNG, VALID_AIGC, modality="image",
                fname="x.png", content_type="image/png")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_modality_mismatch_422(client):
    r = _upload(client, CLEAN_MP4, VALID_AIGC, modality="image")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MODALITY_MISMATCH"


def test_existing_reject_409(client):
    r = _upload(client, LEGACY_MP4, VALID_AIGC)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "AIGC_METADATA_EXISTS"


def test_existing_replace_succeeds(client):
    r = _upload(client, LEGACY_MP4, VALID_AIGC, policy="replace")
    assert r.status_code == 202, r.text
    done = _wait_terminal(client, r.json()["job_id"])
    assert done["status"] == "succeeded", done
    assert done["validation"]["single_aigc_record"] is True
    assert done["embedded_metadata"]["AIGC"] == VALID_AIGC


def test_job_not_found_404(client):
    r = client.get("/api/v1/metadata-label-jobs/job_nonexistent")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_idempotency_key_reuse(client):
    headers = {"Idempotency-Key": "idem-test-1"}
    r1 = _upload(client, CLEAN_MP4, VALID_AIGC, headers=headers)
    assert r1.status_code == 202
    r2 = _upload(client, CLEAN_MP4, VALID_AIGC, headers=headers)
    assert r2.status_code == 202
    assert r1.json()["job_id"] == r2.json()["job_id"]


def test_cleanup_expired_removes_files_and_records(client, tmp_path):
    """§12.3：超过保留期的任务，原文件/结果文件/任务记录一并清理。"""
    from app.core.jobs import FAILED, SUCCEEDED

    store = client.app.state.store
    storage = client.app.state.storage

    # ---- 成功任务：文件可下载，过期后全部清理 ----
    r = _upload(client, CLEAN_MP4, VALID_AIGC)
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    done = _wait_terminal(client, job_id)
    assert done["status"] == "succeeded"
    job = store.get(job_id)
    orig = storage.original_path(job["original_stored_name"])
    out = storage.output_path(job["output_stored_name"])
    assert orig.is_file() and out.is_file()

    store.update(job_id, expires_at="2000-01-01T00:00:00Z")
    assert storage.cleanup_expired(store, "2000-01-02T00:00:00Z", (SUCCEEDED,)) == 1
    assert not orig.exists() and not out.exists()
    assert store.get(job_id) is None
    assert client.get(f"/api/v1/metadata-label-jobs/{job_id}").status_code == 404
    assert client.get(f"/api/v1/metadata-label-jobs/{job_id}/output").status_code == 404

    # ---- 失败任务：必须被设置过期时间，过期后原文件与记录一并清理 ----
    from app.core.pipeline import run_job
    from app.core import util

    fjob = {
        "job_id": "job_fail_cleanup",
        "request_id": "test",
        "idempotency_key": None,
        "status": "queued",
        "stage": "queued",
        "progress": None,
        "created_at": util.now_iso(),
        "updated_at": util.now_iso(),
        "input": {"original_file_name": "x.mp4", "detected_mime_type": "video/mp4",
                  "size_bytes": 0, "sha256": "a" * 64},
        "original_stored_name": "no-such-original-file",
        "existing_metadata_policy": "reject",
        "submitted_aigc": dict(VALID_AIGC),
        "audit": {},
    }
    store.create(fjob)
    run_job("job_fail_cleanup", store, storage, client.app.state.settings)
    row = store.get("job_fail_cleanup")
    assert row["status"] == "failed"
    assert row.get("expires_at"), "失败任务必须有过期时间（§12.3）"

    store.update("job_fail_cleanup", expires_at="2000-01-01T00:00:00Z")
    assert storage.cleanup_expired(store, "2000-01-02T00:00:00Z", (FAILED,)) == 1
    assert store.get("job_fail_cleanup") is None
