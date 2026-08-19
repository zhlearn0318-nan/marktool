import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.metadata_routes import get_metadata_job_service
from app.main import app
from app.metadata.exiftool_client import ExifToolClient
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.image_adapter import ImageMetadataService
from app.metadata.job_service import MetadataJobConfig, MetadataLabelJobService
from app.metadata.xmp_reader import read_aigc_records
from tests.fixtures import VALID_AIGC, make_jpeg, make_png


def _find_exiftool() -> str | None:
    configured = os.getenv("EXIFTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if discovered:
        return discovered
    local_windows_copy = Path(r"D:\exiftool\exiftool.exe")
    return str(local_windows_copy) if local_windows_copy.is_file() else None


@pytest.fixture
def metadata_api(tmp_path, monkeypatch):
    executable = _find_exiftool()
    if not executable:
        pytest.skip("需要 ExifTool；请设置 EXIFTOOL_PATH 后运行接口集成测试")
    monkeypatch.setenv("EXIFTOOL_PATH", executable)
    config = MetadataJobConfig(
        storage_root=tmp_path / "job-files",
        database_path=tmp_path / "jobs.sqlite3",
        identifier_database_path=tmp_path / "identifiers.sqlite3",
        max_upload_bytes=5 * 1024 * 1024,
        retention_hours=24,
        worker_count=2,
        exiftool_timeout_seconds=30,
    )
    image_service = ImageMetadataService(
        ExifToolClient(executable=executable),
        SQLiteIdentifierRegistry(str(config.identifier_database_path)),
    )
    service = MetadataLabelJobService(config, image_service=image_service)
    app.dependency_overrides[get_metadata_job_service] = lambda: service
    with TestClient(app) as client:
        yield client, service
    app.dependency_overrides.pop(get_metadata_job_service, None)
    service.close()


def _request_payload(*, policy="reject", **aigc_overrides):
    content_id = f"PRD-{uuid.uuid4().hex.upper()}"
    aigc = {
        **VALID_AIGC,
        "ProduceID": content_id,
        "PropagateID": content_id,
        **aigc_overrides,
    }
    return {
        "standard": "GB45438-2025",
        "modality": "image",
        "existing_metadata_policy": policy,
        "AIGC": aigc,
    }


def _post_job(client, filename, data, payload, *, key=None):
    headers = {"Idempotency-Key": key} if key else None
    return client.post(
        "/api/v1/metadata-label-jobs",
        files={
            "file": (filename, data, "application/octet-stream"),
            "request": (
                "request.json",
                json.dumps(payload, ensure_ascii=False),
                "application/json",
            ),
        },
        headers=headers,
    )


def _wait_for_terminal(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/metadata-label-jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    pytest.fail("异步任务未在测试时限内结束")


def test_generate_produce_id_and_versioned_health(metadata_api):
    client, _ = metadata_api

    generated = client.post("/api/v1/metadata-label-identifiers")
    assert generated.status_code == 201
    body = generated.json()
    assert re.fullmatch(
        r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        body["produce_id"],
    )
    assert generated.headers["x-request-id"] == body["request_id"]

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["capabilities"] == {
        "image/jpeg": True,
        "image/png": True,
        "video/mp4": False,
    }
    assert health.headers["x-request-id"].startswith("req_")


@pytest.mark.parametrize(
    ("maker", "filename", "expected_mime"),
    [
        (make_jpeg, "sample.jpg", "image/jpeg"),
        (make_png, "sample.png", "image/png"),
    ],
)
def test_create_poll_download_and_read_back_image(
    tmp_path, metadata_api, maker, filename, expected_mime
):
    client, _ = metadata_api
    source = Path(maker(tmp_path / filename))
    original = source.read_bytes()
    request_payload = _request_payload()

    created = _post_job(client, filename, original, request_payload)

    assert created.status_code == 202
    accepted = created.json()
    assert accepted["job_id"].startswith("job_")
    assert accepted["status"] == "queued"
    assert created.headers["x-request-id"] == accepted["request_id"]

    result = _wait_for_terminal(client, accepted["job_id"])
    assert result["status"] == "succeeded"
    assert result["stage"] == "completed"
    assert result["progress"] == 100
    assert result["input"]["detected_mime_type"] == expected_mime
    assert result["embedded_metadata"] == {"AIGC": request_payload["AIGC"]}
    assert all(result["validation"].values())
    assert result["output"]["carrier"] == "xmp-aigc-v1"
    assert "input_path" not in json.dumps(result)
    assert "output_path" not in json.dumps(result)

    downloaded = client.get(result["output"]["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == expected_mime
    assert "_labeled" in downloaded.headers["content-disposition"]
    output = tmp_path / f"downloaded{Path(filename).suffix}"
    output.write_bytes(downloaded.content)
    records = read_aigc_records(str(output))
    assert len(records) == 1
    assert records[0].document == {"AIGC": request_payload["AIGC"]}
    assert source.read_bytes() == original


def test_real_content_type_wins_over_filename_extension(tmp_path, metadata_api):
    client, _ = metadata_api
    source = Path(make_png(tmp_path / "actually-png.jpg"))

    created = _post_job(
        client, "actually-png.jpg", source.read_bytes(), _request_payload()
    )
    result = _wait_for_terminal(client, created.json()["job_id"])

    assert result["status"] == "succeeded"
    assert result["input"]["detected_mime_type"] == "image/png"
    assert result["output"]["file_name"].endswith(".png")


def test_existing_metadata_reject_then_explicit_replace(tmp_path, metadata_api):
    client, _ = metadata_api
    source = Path(make_png(tmp_path / "marked.png", aigc_dict=VALID_AIGC))
    data = source.read_bytes()

    rejected = _post_job(client, "marked.png", data, _request_payload())
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AIGC_METADATA_EXISTS"

    replacement = _request_payload(policy="replace", Label="2")
    created = _post_job(client, "marked.png", data, replacement)
    result = _wait_for_terminal(client, created.json()["job_id"])
    assert result["status"] == "succeeded"
    downloaded = client.get(result["output"]["download_url"])
    output = tmp_path / "replaced.png"
    output.write_bytes(downloaded.content)
    records = read_aigc_records(str(output))
    assert len(records) == 1
    assert records[0].document == {"AIGC": replacement["AIGC"]}


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda p: p["AIGC"].pop("ReservedCode2"), "AIGC_SCHEMA_INVALID"),
        (lambda p: p["AIGC"].update(Label=1), "AIGC_SCHEMA_INVALID"),
        (
            lambda p: p["AIGC"].update(ContentProducer="中文机构"),
            "AIGC_CHARACTER_INVALID",
        ),
        (
            lambda p: p["AIGC"].update(ContentPropagator="ORG_OTHER"),
            "AIGC_INITIAL_RELATION_INVALID",
        ),
    ],
)
def test_request_validation_returns_stable_error_codes(
    tmp_path, metadata_api, mutate, expected_code
):
    client, _ = metadata_api
    data = Path(make_png(tmp_path / "source.png")).read_bytes()
    payload = _request_payload()
    mutate(payload)

    response = _post_job(client, "source.png", data, payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["request_id"].startswith("req_")


def test_invalid_multipart_unsupported_media_and_modality(tmp_path, metadata_api):
    client, service = metadata_api
    missing = client.post("/api/v1/metadata-label-jobs", files={})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "INVALID_MULTIPART"

    non_image = _post_job(client, "x.txt", b"not an image", _request_payload())
    assert non_image.status_code == 415
    assert non_image.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

    too_large = _post_job(
        client,
        "large.png",
        b"x" * (service.config.max_upload_bytes + 1),
        _request_payload(),
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "FILE_TOO_LARGE"

    data = Path(make_png(tmp_path / "source.png")).read_bytes()
    payload = _request_payload()
    payload["modality"] = "video"
    mismatch = _post_job(client, "source.png", data, payload)
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "MODALITY_MISMATCH"


def test_idempotency_key_returns_same_job_and_rejects_different_request(
    tmp_path, metadata_api
):
    client, _ = metadata_api
    data = Path(make_png(tmp_path / "source.png")).read_bytes()
    payload = _request_payload()
    key = "browser-retry-001"

    first = _post_job(client, "source.png", data, payload, key=key)
    second = _post_job(client, "source.png", data, payload, key=key)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]

    conflict = _post_job(
        client, "source.png", data, _request_payload(Label="2"), key=key
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    terminal = _wait_for_terminal(client, first.json()["job_id"])
    assert terminal["status"] == "succeeded"


def test_unknown_job_and_output_not_ready(metadata_api, monkeypatch):
    client, service = metadata_api
    unknown = client.get("/api/v1/metadata-label-jobs/not-found")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "JOB_NOT_FOUND"

    record = {
        "job_id": "job_waiting",
        "request_id": "req_waiting",
        "idempotency_key": None,
        "request_fingerprint": "fingerprint",
        "status": "queued",
        "stage": "queued",
        "progress": None,
        "created_at": "2026-08-19T00:00:00Z",
        "updated_at": "2026-08-19T00:00:00Z",
        "original_file_name": "x.png",
        "detected_mime_type": "image/png",
        "input_size_bytes": 1,
        "input_sha256": "hash",
        "input_path": str(service.input_dir / "x.png"),
        "output_path": str(service.output_dir / "x.png"),
        "output_file_name": "x_labeled.png",
        "request_json": _request_payload(),
    }
    service.store.create(record)
    waiting = client.get("/api/v1/metadata-label-jobs/job_waiting/output")
    assert waiting.status_code == 409
    assert waiting.json()["error"]["code"] == "OUTPUT_NOT_READY"
