import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.repair_routes import get_metadata_repair_service
from app.main import app
from app.metadata.compliance import MetadataComplianceInspector
from app.metadata.exiftool_client import ExifToolClient
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.image_adapter import ImageMetadataService
from app.metadata.repair_service import MetadataRepairConfig, MetadataRepairService
from app.metadata.xmp_reader import read_aigc_records
from tests.fixtures import (
    VALID_AIGC,
    broken_json_xmp,
    legacy_xmp,
    make_jpeg,
    make_png,
)


def _find_exiftool() -> str | None:
    configured = os.getenv("EXIFTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if discovered:
        return discovered
    local = Path(r"D:\exiftool\exiftool.exe")
    return str(local) if local.is_file() else None


@pytest.fixture
def repair_api(tmp_path, monkeypatch):
    executable = _find_exiftool()
    if not executable:
        pytest.skip("需要 ExifTool；请设置 EXIFTOOL_PATH")
    monkeypatch.setenv("EXIFTOOL_PATH", executable)
    config = MetadataRepairConfig(
        storage_root=tmp_path / "repair-files",
        database_path=tmp_path / "repairs.sqlite3",
        audit_root=tmp_path / "repair-audit",
        identifier_database_path=tmp_path / "identifiers.sqlite3",
        max_upload_bytes=5 * 1024 * 1024,
        plan_retention_hours=24,
        file_retention_days=180,
        worker_count=1,
    )
    registry = SQLiteIdentifierRegistry(str(config.identifier_database_path))
    exiftool = ExifToolClient(executable=executable)
    image_service = ImageMetadataService(exiftool, registry)
    inspector = MetadataComplianceInspector(
        exiftool=exiftool,
        identifier_registry=registry,
    )
    service = MetadataRepairService(
        config,
        image_service=image_service,
        inspector=inspector,
    )
    app.dependency_overrides[get_metadata_repair_service] = lambda: service
    with TestClient(app) as client:
        yield client, service
    app.dependency_overrides.pop(get_metadata_repair_service, None)
    service.close()


def _post_plan(client, path, options=None):
    files = {"file": (path.name, path.read_bytes(), "application/octet-stream")}
    if options is not None:
        files["request"] = (
            "request.json",
            json.dumps(options, ensure_ascii=False),
            "application/json",
        )
    return client.post("/api/v1/metadata-repair-plans", files=files)


def _wait_job(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/metadata-repair-jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    pytest.fail("异步修复任务未在时限内结束")


def test_not_found_plan_cannot_create_repair_job(repair_api, tmp_path):
    client, service = repair_api
    source = Path(make_png(tmp_path / "plain.png"))

    planned = _post_plan(client, source)
    assert planned.status_code == 201
    body = planned.json()
    assert body["inspection"]["conclusion"] == "not_found"
    assert body["repair_plan"]["executable"] is False

    confirmed = client.post(
        "/api/v1/metadata-repair-jobs",
        json={
            "plan_id": body["plan_id"],
            "plan_hash": body["plan_hash"],
            "confirmed": True,
            "operator_label": "本科生测试操作人",
        },
    )
    assert confirmed.status_code == 409
    assert confirmed.json()["error"]["code"] == "REPAIR_PLAN_CONFLICT"

    stored = service.store.get_plan(body["plan_id"])
    stored_input = Path(stored["input_path"])
    with sqlite3.connect(service.config.database_path) as connection:
        connection.execute(
            "UPDATE metadata_repair_plans SET expires_at=? WHERE plan_id=?",
            ("2000-01-01T00:00:00Z", body["plan_id"]),
        )
    expired_plan = client.get(f"/api/v1/metadata-repair-plans/{body['plan_id']}")
    assert expired_plan.json()["status"] == "expired"
    assert stored_input.is_file(), "计划失效不应提前删除应保留 180 天的原文件"

    with sqlite3.connect(service.config.database_path) as connection:
        connection.execute(
            "UPDATE metadata_repair_plans SET input_expires_at=? WHERE plan_id=?",
            ("2000-01-01T00:00:00Z", body["plan_id"]),
        )
    purged_plan = client.get(f"/api/v1/metadata-repair-plans/{body['plan_id']}")
    assert purged_plan.json()["input"]["available"] is False
    assert not stored_input.exists()


def test_confirmation_requires_true_and_matching_plan_hash(repair_api, tmp_path):
    client, _ = repair_api
    missing_reserved = {
        key: value
        for key, value in VALID_AIGC.items()
        if key not in {"ReservedCode1", "ReservedCode2"}
    }
    source = Path(
        make_png(tmp_path / "confirm.png", raw_xmp=legacy_xmp(missing_reserved))
    )
    plan = _post_plan(client, source).json()
    base = {
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "operator_label": "测试操作人",
    }

    not_confirmed = client.post(
        "/api/v1/metadata-repair-jobs", json={**base, "confirmed": False}
    )
    assert not_confirmed.status_code == 422

    wrong_hash = client.post(
        "/api/v1/metadata-repair-jobs",
        json={**base, "plan_hash": "0" * 64, "confirmed": True},
    )
    assert wrong_hash.status_code == 409
    assert wrong_hash.json()["error"]["code"] == "REPAIR_PLAN_CONFLICT"

    still_pending = client.get(
        f"/api/v1/metadata-repair-plans/{plan['plan_id']}"
    )
    assert still_pending.status_code == 200
    assert still_pending.json()["status"] == "pending"


def test_invalid_trusted_fields_return_422_not_internal_error(repair_api, tmp_path):
    client, _ = repair_api
    source = Path(
        make_png(tmp_path / "broken-trusted.png", raw_xmp=broken_json_xmp())
    )
    options = {
        "trusted_input": {
            "AIGC": {"Label": "1"},
            "source_type": "authorized_manual",
            "source_reference": "TEST-INVALID",
            "write_context": "unknown",
        }
    }

    response = _post_plan(client, source, options)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TRUSTED_METADATA_INVALID"


@pytest.mark.parametrize(
    ("maker", "filename"),
    [(make_png, "legacy.png"), (make_jpeg, "legacy.jpg")],
)
def test_confirmed_repair_keeps_original_and_returns_one_valid_record(
    repair_api, tmp_path, maker, filename
):
    client, service = repair_api
    missing_reserved = {
        key: value
        for key, value in VALID_AIGC.items()
        if key not in {"ReservedCode1", "ReservedCode2"}
    }
    source = Path(maker(tmp_path / filename, raw_xmp=legacy_xmp(missing_reserved)))
    original_bytes = source.read_bytes()

    planned = _post_plan(client, source)
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    assert plan["inspection"]["conclusion"] == "noncompliant"
    assert plan["repair_plan"]["repairability"] == "confirmable"
    assert plan["repair_plan"]["executable"] is True
    assert any("补为空字符串" in item for item in plan["repair_plan"]["actions"])

    accepted = client.post(
        "/api/v1/metadata-repair-jobs",
        json={
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "confirmed": True,
            "operator_label": "本科生测试操作人",
        },
    )
    assert accepted.status_code == 202, accepted.text
    accepted_job_id = accepted.json()["job_id"]
    linked_plan = client.get(
        f"/api/v1/metadata-repair-plans/{plan['plan_id']}"
    ).json()
    assert linked_plan["job_id"] == accepted_job_id
    assert linked_plan["links"]["job"].endswith(accepted_job_id)
    completed = _wait_job(client, accepted_job_id)
    assert completed["status"] == "succeeded", completed
    assert completed["confirmation"] == {
        "method": "manual_api",
        "operator_label": "本科生测试操作人",
        "identity_verified": False,
    }
    assert completed["validation"]["post_repair_conclusion"] == "compliant"
    assert completed["validation"]["pixel_sha256_unchanged"] is True

    expires = datetime.fromisoformat(completed["output"]["expires_at"].replace("Z", "+00:00"))
    remaining_days = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 179.9 < remaining_days <= 180.1

    downloaded = client.get(completed["output"]["download_url"])
    assert downloaded.status_code == 200
    repaired = tmp_path / f"downloaded{source.suffix}"
    repaired.write_bytes(downloaded.content)
    records = read_aigc_records(str(repaired))
    assert len(records) == 1
    assert records[0].document == {"AIGC": VALID_AIGC}
    assert source.read_bytes() == original_bytes

    with sqlite3.connect(service.config.database_path) as connection:
        events = connection.execute(
            "SELECT event_type, previous_event_hash, event_hash "
            "FROM metadata_repair_audit_events WHERE plan_id=? "
            "ORDER BY sequence_number",
            (plan["plan_id"],),
        ).fetchall()
        artifacts = connection.execute(
            "SELECT artifact_type FROM metadata_repair_artifacts WHERE plan_id=?",
            (plan["plan_id"],),
        ).fetchall()
    assert [item[0] for item in events] == [
        "repair_plan_created",
        "repair_confirmed",
        "repair_started",
        "repair_succeeded",
    ]
    assert events[0][1] == "0" * 64
    assert all(events[index][1] == events[index - 1][2] for index in range(1, len(events)))
    assert {item[0] for item in artifacts} == {
        "inspection_and_original_labels",
        "repaired_metadata_and_validation",
    }

    if source.suffix == ".png":
        stored_plan = service.store.get_plan(plan["plan_id"])
        stored_job = service.store.get_job(completed["job_id"])
        audit_paths = []
        with sqlite3.connect(service.config.database_path) as connection:
            audit_paths = [
                Path(item[0])
                for item in connection.execute(
                    "SELECT storage_path FROM metadata_repair_artifacts WHERE plan_id=?",
                    (plan["plan_id"],),
                ).fetchall()
            ]
            connection.execute(
                "UPDATE metadata_repair_jobs SET files_expires_at=? WHERE job_id=?",
                ("2000-01-01T00:00:00Z", completed["job_id"]),
            )
        assert all(path.is_file() for path in audit_paths)
        purged = service.get_job(completed["job_id"])
        assert purged["stage"] == "files_purged"
        assert purged["output"]["available"] is False
        assert purged["output"]["download_url"] is None
        assert not Path(stored_plan["input_path"]).exists()
        assert not Path(stored_job["output_path"]).exists()
        assert all(path.is_file() for path in audit_paths)
        with sqlite3.connect(service.config.database_path) as connection:
            last_event = connection.execute(
                "SELECT event_type FROM metadata_repair_audit_events "
                "WHERE plan_id=? ORDER BY sequence_number DESC LIMIT 1",
                (plan["plan_id"],),
            ).fetchone()
        assert last_event[0] == "repair_files_purged"
