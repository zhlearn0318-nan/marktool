"""多模态与场景进入合规检测 / 修复后的行为。

不带 profile=auto 时，原有单模态检测接口保持原来的拒绝和判定。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core import aigc
from app.main import create_app
from app.multimodal.service import label_multimodal
from app.scenes.interactive_scene import label_interactive_scene
from app.scenes.virtual_scene import label_virtual_scene
from app.tools import ToolPaths
from common import EXIFTOOL_CONFIG, itxt_chunk
from media_samples import make_av_subtitle_video, make_silent_video

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("exiftool") is None,
    reason="需要 ffmpeg 与 exiftool",
)


def _tools(tmp_path: Path) -> ToolPaths:
    return ToolPaths(
        exiftool="exiftool", ffprobe="ffprobe", ffmpeg="ffmpeg",
        exiftool_config=str(EXIFTOOL_CONFIG),
        registry_path=str(tmp_path / "ids.sqlite3"),
    )


def _aigc(produce_id: str) -> dict:
    return {
        "Label": "1",
        "ContentProducer": "ORG_1565201000000016",
        "ProduceID": produce_id,
        "ReservedCode1": "",
        "ContentPropagator": "ORG_1565201000000016",
        "PropagateID": produce_id,
        "ReservedCode2": "",
    }


def _client(tmp_path) -> TestClient:
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    app = create_app(settings=settings, storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    return TestClient(app)


def _inspect(client: TestClient, path: Path, *, profile: str | None = "auto"):
    data = {"profile": profile} if profile else None
    return client.post(
        "/api/v1/compliance-inspect",
        data=data,
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )


def _repair(client: TestClient, path: Path) -> dict:
    created = client.post(
        "/api/v1/composite-repair-plans",
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["repair_plan"]["executable"] is True
    job = client.post("/api/v1/composite-repair-jobs", json={
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "confirmed": True,
        "operator_label": "test",
    })
    assert job.status_code == 201, job.text
    body = job.json()
    assert body["validation"]["post_repair_conclusion"] == "compliant"
    output = client.get(body["links"]["output"])
    assert output.status_code == 200
    repaired = path.with_name("repaired-" + path.name)
    repaired.write_bytes(output.content)
    again = _inspect(client, repaired)
    assert again.status_code == 200
    assert again.json()["conclusion"] == "compliant"
    return again.json()


def _write_xmp(path: Path, raw: str) -> None:
    subprocess.check_call([
        "exiftool", "-config", str(EXIFTOOL_CONFIG), "-overwrite_original",
        "-XMP-aigc:AIGC=" + raw, str(path),
    ], stdout=subprocess.DEVNULL)


def _write_comment(path: Path, raw: str) -> None:
    subprocess.check_call([
        "exiftool", "-overwrite_original", "-QuickTime:Comment=" + raw, str(path),
    ], stdout=subprocess.DEVNULL)


def test_unlabeled_multimodal_mp4_is_not_found(tmp_path):
    source = make_av_subtitle_video(tmp_path)
    with _client(tmp_path) as client:
        report = _inspect(client, source).json()
    assert report["conclusion"] == "not_found"
    assert report["subject"]["kind"] == "multimodal-media"
    with _client(tmp_path) as client:
        plan = client.post("/api/v1/composite-repair-plans",
                           files={"file": (source.name, source.read_bytes(), "video/mp4")})
    assert plan.json()["repair_plan"]["executable"] is False


def test_duplicate_multimodal_label_repairs_to_one(tmp_path):
    source = make_av_subtitle_video(tmp_path)
    tools = _tools(tmp_path)
    label_multimodal(tmp_path / "out", _aigc("PID-COMP-DUP"), tools=tools, composite_video=source)
    composite = tmp_path / "out" / "recombined" / "composite.mp4"
    raw = aigc.serialize_aigc(_aigc("PID-COMP-DUP"))
    _write_comment(composite, raw)
    with _client(tmp_path) as client:
        before = _inspect(client, composite).json()
        assert before["conclusion"] == "noncompliant"
        assert before["reason_code"] == "DUPLICATE_RECORDS"
        _repair(client, composite)


def test_missing_field_repairs_and_bad_json_does_not(tmp_path):
    source = make_av_subtitle_video(tmp_path)
    tools = _tools(tmp_path)
    label_multimodal(tmp_path / "out", _aigc("PID-COMP-MISS"), tools=tools, composite_video=source)
    composite = tmp_path / "out" / "recombined" / "composite.mp4"
    incomplete = dict(_aigc("PID-COMP-MISS"))
    incomplete.pop("ReservedCode2")
    _write_xmp(composite, json.dumps({"AIGC": incomplete}, ensure_ascii=True, separators=(",", ":")))
    with _client(tmp_path) as client:
        report = _inspect(client, composite).json()
        assert report["conclusion"] == "noncompliant"
        assert report["reason_code"] == "MISSING_FIELD"
        _repair(client, composite)

    broken = tmp_path / "broken.mp4"
    shutil.copyfile(source, broken)
    _write_xmp(broken, "{")
    with _client(tmp_path) as client:
        report = _inspect(client, broken).json()
        assert report["conclusion"] == "noncompliant"
        assert report["reason_code"] == "BAD_JSON"
        plan = client.post("/api/v1/composite-repair-plans",
                           files={"file": (broken.name, broken.read_bytes(), "video/mp4")})
        assert plan.json()["repair_plan"]["executable"] is False


def test_virtual_and_interactive_packages(tmp_path):
    tools = _tools(tmp_path)
    label_virtual_scene(tmp_path / "virtual", aigc_obj=_aigc("PID-VS-OK"), tools=tools)
    (tmp_path / "reply.txt").write_text("这是 AI 的回复。\n", encoding="utf-8")
    label_interactive_scene(
        tmp_path / "interactive", content=tmp_path / "reply.txt", content_role="text",
        aigc_obj=_aigc("PID-IS-OK"), tools=tools,
    )
    with _client(tmp_path) as client:
        virtual_report = _inspect(client, tmp_path / "virtual" / "virtual-scene.zip")
        assert virtual_report.json()["conclusion"] == "compliant"
        assert virtual_report.json()["subject"]["kind"] == "virtual-scene"
        interactive_zip = tmp_path / "interactive" / "interactive-scene.zip"
        assert _inspect(client, interactive_zip).json()["conclusion"] == "compliant"

        duplicate = _duplicate_virtual(tmp_path, tmp_path / "virtual" / "virtual-scene.zip")
        before = _inspect(client, duplicate).json()
        assert before["conclusion"] == "noncompliant"
        _repair(client, duplicate)

        doubled = _double_interactive_label(tmp_path, interactive_zip)
        assert _inspect(client, doubled).json()["reason_code"] == "DUPLICATE_RECORDS"
        _repair(client, doubled)

        damaged = _damage_scene_json(tmp_path, interactive_zip)
        damaged_report = _inspect(client, damaged).json()
        assert damaged_report["conclusion"] == "indeterminate"


def test_single_modality_inspect_is_unchanged_without_profile(tmp_path):
    silent = make_silent_video(tmp_path)
    with _client(tmp_path) as client:
        plain = client.post("/api/v1/compliance-inspect",
                            files={"file": ("silent.mp4", silent.read_bytes(), "video/mp4")})
        assert plain.status_code == 200
        assert "subject" not in plain.json()
        auto = _inspect(client, silent).json()
        assert "subject" not in auto
        bundle = tmp_path / "bundle.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("scene.json", "{")
        rejected = client.post("/api/v1/compliance-inspect",
                               files={"file": ("scene.zip", bundle.read_bytes(), "application/zip")})
        assert rejected.status_code == 415


def _duplicate_virtual(tmp_path: Path, package: Path) -> Path:
    target = tmp_path / "virtual-duplicate.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(target, "w") as dest:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "start_frame.png":
                frame = tmp_path / "frame.png"
                frame.write_bytes(payload)
                xmp = subprocess.check_output(["exiftool", "-b", "-XMP", str(frame)])
                insert_at = payload.rfind(b"IEND") - 4
                payload = payload[:insert_at] + itxt_chunk(xmp.decode("utf-8")) + payload[insert_at:]
            dest.writestr(info.filename, payload)
    return target


def _double_interactive_label(tmp_path: Path, package: Path) -> Path:
    target = tmp_path / "interactive-duplicate.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(target, "w") as dest:
        descriptor = json.loads(source.read("scene.json"))
        content_name = descriptor["content_file"]
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == content_name:
                marker = b"\n\x1eGB45438-AIGC\x1e"
                offset = payload.find(marker)
                payload = payload + payload[offset:]
            dest.writestr(info.filename, payload)
    return target


def _damage_scene_json(tmp_path: Path, package: Path) -> Path:
    target = tmp_path / "broken-scene.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(target, "w") as dest:
        for info in source.infolist():
            payload = b"{" if info.filename == "scene.json" else source.read(info.filename)
            dest.writestr(info.filename, payload)
    return target
