"""冲突标识由用户选择保留哪一份。不传选择时仍不自动挑选。"""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.repair_routes import get_metadata_repair_service
from app.config import Settings
from app.core import aigc
from app.main import create_app
from app.metadata.repair_service import MetadataRepairConfig, MetadataRepairService
from app.scenes.virtual_scene import label_virtual_scene
from app.tools import ToolPaths
from common import EXIFTOOL_CONFIG, itxt_chunk
from fixtures import UPDATED_AIGC, VALID_AIGC, duplicate_xmp, make_png

pytestmark = pytest.mark.skipif(shutil.which("exiftool") is None, reason="需要 exiftool")


def _client(tmp_path) -> tuple[TestClient, MetadataRepairService]:
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    config = MetadataRepairConfig(
        storage_root=tmp_path / "repair-files",
        database_path=tmp_path / "repairs.sqlite3",
        audit_root=tmp_path / "repair-audit",
        identifier_database_path=tmp_path / "ids.sqlite3",
        exiftool_config=str(EXIFTOOL_CONFIG),
    )
    service = MetadataRepairService(config)
    app = create_app(settings=settings, storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    app.dependency_overrides[get_metadata_repair_service] = lambda: service
    return TestClient(app), service


def _aigc(produce_id: str, label: str = "1") -> dict:
    base = dict(VALID_AIGC)
    base["ProduceID"] = produce_id
    base["PropagateID"] = produce_id
    base["Label"] = label
    return base


def _wait_job(client: TestClient, job_id: str) -> dict:
    import time
    for _ in range(80):
        body = client.get(f"/api/v1/metadata-repair-jobs/{job_id}").json()
        if body["status"] in {"succeeded", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("修复任务没有结束")


def test_single_modality_conflict_requires_choice_then_repairs(tmp_path):
    path = tmp_path / "conflict.png"
    make_png(path, raw_xmp=duplicate_xmp(VALID_AIGC, UPDATED_AIGC))
    client, service = _client(tmp_path)
    with client:
        inspected = client.post(
            "/api/v1/compliance-inspect",
            files={"file": ("conflict.png", path.read_bytes(), "image/png")},
        )
        assert inspected.status_code == 200, inspected.text
        report = inspected.json()
        assert report["conclusion"] == "noncompliant"
        groups = report["conflict_groups"]
        assert len(groups) == 1
        assert "ProduceID" in groups[0]["differing_fields"]
        assert len(groups[0]["candidates"]) == 2
        ids = [item["id"] for item in groups[0]["candidates"]]
        assert len(set(ids)) == 2

        untouched = client.post(
            "/api/v1/metadata-repair-plans",
            files={"file": ("conflict.png", path.read_bytes(), "image/png")},
        )
        assert untouched.status_code == 201, untouched.text
        assert untouched.json()["repair_plan"]["executable"] is False
        assert any("冲突" in item for item in untouched.json()["repair_plan"]["blocking_reasons"])

        bogus = client.post(
            "/api/v1/metadata-repair-plans",
            data={"request": json.dumps({"selected_candidate_id": "cand_does_not_exist"})},
            files={"file": ("conflict.png", path.read_bytes(), "image/png")},
        )
        assert bogus.status_code == 422
        assert bogus.json()["error"]["code"] == "INVALID_CANDIDATE_ID"

        chosen = groups[0]["candidates"][1]
        planned = client.post(
            "/api/v1/metadata-repair-plans",
            data={"request": json.dumps({"selected_candidate_id": chosen["id"]})},
            files={"file": ("conflict.png", path.read_bytes(), "image/png")},
        )
        assert planned.status_code == 201, planned.text
        body = planned.json()
        assert body["repair_plan"]["executable"] is True
        assert body["repair_plan"]["proposed_document"]["AIGC"]["ProduceID"] == chosen["fields"]["ProduceID"]
        accepted = client.post("/api/v1/metadata-repair-jobs", json={
            "plan_id": body["plan_id"],
            "plan_hash": body["plan_hash"],
            "confirmed": True,
            "operator_label": "test",
        })
        assert accepted.status_code == 202, accepted.text
        job = _wait_job(client, accepted.json()["job_id"])
        assert job["status"] == "succeeded", job
        assert job["validation"]["post_repair_conclusion"] == "compliant"
    service.close()


def test_multimodal_bundle_groups_are_selected_independently(tmp_path):
    first = _aigc("PID-GROUP-A1")
    first_other = _aigc("PID-GROUP-A2", "2")
    second = _aigc("PID-GROUP-B1")
    second_other = _aigc("PID-GROUP-B2", "3")
    package = tmp_path / "bundle.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("report.json", "{}")
        archive.writestr("components/a.txt", _text(first, first_other))
        archive.writestr("components/b.txt", _text(second, second_other))
    client, service = _client(tmp_path)
    with client:
        report = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("bundle.zip", package.read_bytes(), "application/zip")},
        ).json()
        groups = report["conflict_groups"]
        assert len(groups) == 2
        assert {group["file"] for group in groups} == {"components/a.txt", "components/b.txt"}

        only_one = {groups[0]["id"]: groups[0]["candidates"][0]["id"]}
        partial = client.post(
            "/api/v1/composite-repair-plans",
            data={"selections": json.dumps(only_one)},
            files={"file": ("bundle.zip", package.read_bytes(), "application/zip")},
        )
        assert partial.status_code == 422
        assert partial.json()["error"]["code"] == "SELECTION_REQUIRED"

        unknown = client.post(
            "/api/v1/composite-repair-plans",
            data={"selections": json.dumps({groups[0]["id"]: "cand_missing", groups[1]["id"]: groups[1]["candidates"][0]["id"]})},
            files={"file": ("bundle.zip", package.read_bytes(), "application/zip")},
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "INVALID_CANDIDATE_ID"

        selections = {
            group["id"]: group["candidates"][0]["id"] for group in groups
        }
        blocked = client.post(
            "/api/v1/composite-repair-plans",
            files={"file": ("bundle.zip", package.read_bytes(), "application/zip")},
        )
        assert blocked.json()["repair_plan"]["executable"] is False

        planned = client.post(
            "/api/v1/composite-repair-plans",
            data={"selections": json.dumps(selections)},
            files={"file": ("bundle.zip", package.read_bytes(), "application/zip")},
        )
        assert planned.status_code == 201, planned.text
        plan = planned.json()
        assert plan["repair_plan"]["executable"] is True
        job = client.post("/api/v1/composite-repair-jobs", json={
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "confirmed": True,
        })
        assert job.status_code == 201, job.text
        assert job.json()["validation"]["post_repair_conclusion"] == "compliant"
        output = client.get(job.json()["links"]["output"])
        again = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("repaired.zip", output.content, "application/zip")},
        )
        assert again.json()["conclusion"] == "compliant"
        assert again.json().get("conflict_groups") in (None, [])
    service.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_virtual_scene_conflict_uses_selected_label(tmp_path):
    tools = ToolPaths(
        exiftool="exiftool", ffprobe="ffprobe", ffmpeg="ffmpeg",
        exiftool_config=str(EXIFTOOL_CONFIG),
        registry_path=str(tmp_path / "ids.sqlite3"),
    )
    label_virtual_scene(tmp_path / "virtual", aigc_obj=_aigc("PID-SCENE-KEEP"), tools=tools)
    package = _conflict_scene(tmp_path, tmp_path / "virtual" / "virtual-scene.zip")
    client, service = _client(tmp_path)
    with client:
        report = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("virtual-scene.zip", package.read_bytes(), "application/zip")},
        ).json()
        assert report["conclusion"] == "noncompliant"
        group = report["conflict_groups"][0]
        assert group["file"] == "start_frame.png"
        kept = next(item for item in group["candidates"] if item["fields"]["ProduceID"] == "PID-SCENE-KEEP")
        planned = client.post(
            "/api/v1/composite-repair-plans",
            data={"selected_candidate_id": kept["id"]},
            files={"file": ("virtual-scene.zip", package.read_bytes(), "application/zip")},
        )
        assert planned.status_code == 201, planned.text
        assert planned.json()["repair_plan"]["executable"] is True
        proposed = [
            unit["proposed"]["AIGC"]["ProduceID"]
            for unit in planned.json()["repair_plan"]["units"]
            if unit.get("proposed")
        ]
        assert proposed == ["PID-SCENE-KEEP"]
        job = client.post("/api/v1/composite-repair-jobs", json={
            "plan_id": planned.json()["plan_id"],
            "plan_hash": planned.json()["plan_hash"],
            "confirmed": True,
        })
        assert job.json()["validation"]["post_repair_conclusion"] == "compliant"
    service.close()


def _text(*documents: dict) -> bytes:
    body = "正文".encode("utf-8")
    marker = b"\n\x1eGB45438-AIGC\x1e"
    for document in documents:
        body += marker + aigc.serialize_aigc(document).encode("ascii")
    return body + b"\n"


def _conflict_scene(tmp_path: Path, package: Path) -> Path:
    target = tmp_path / "virtual-conflict.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(target, "w") as dest:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "start_frame.png":
                frame = tmp_path / "frame.png"
                frame.write_bytes(payload)
                current = subprocess.check_output(["exiftool", "-b", "-XMP", str(frame)])
                other = current.decode("utf-8").replace("PID-SCENE-KEEP", "PID-SCENE-DROP", 1)
                insert_at = payload.rfind(b"IEND") - 4
                payload = payload[:insert_at] + itxt_chunk(other) + payload[insert_at:]
            dest.writestr(info.filename, payload)
    return target
