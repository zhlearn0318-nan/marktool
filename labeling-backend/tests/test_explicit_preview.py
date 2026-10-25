"""场景显式提示先预览，确认后才写回。不确认不改文件。"""
from __future__ import annotations

import io
import json
import random
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.scenes.explicit_preview import choose_overlay_position
from app.scenes.interactive_scene import label_interactive_scene
from app.scenes.overlay import find_font
from app.scenes.virtual_scene import label_virtual_scene
from app.tools import ToolPaths
from common import EXIFTOOL_CONFIG

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


def _rewrite(source: Path, dest: Path, mutate) -> None:
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    descriptor = json.loads(members["scene.json"])
    mutate(descriptor)
    members["scene.json"] = json.dumps(descriptor, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _member(package: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return archive.read(name)


def _crop(png: bytes, box: list) -> bytes:
    x, y, width, height = (int(value) for value in box)
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGB").crop((x, y, x + width, y + height)).tobytes()


def _break_virtual(source: Path, dest: Path) -> None:
    def mutate(descriptor: dict) -> None:
        descriptor["explicit"]["text"] = "未按标准提示"
        descriptor["explicit"]["position"] = "center"
    _rewrite(source, dest, mutate)


def _break_interactive(source: Path, dest: Path) -> None:
    def mutate(descriptor: dict) -> None:
        descriptor["explicit"]["text"] = "你好"
        descriptor["explicit"]["placements"] = ["middle"]
        descriptor["explicit"]["persistent"] = False
        descriptor["explicit"]["color"] = "#777777"
        descriptor["explicit"]["background"] = "#888888"
    _rewrite(source, dest, mutate)


def test_virtual_preview_keeps_old_prompt_until_confirm(tmp_path):
    tools = _tools(tmp_path)
    label_virtual_scene(tmp_path / "virtual", aigc_obj=_aigc("PID-EXPLICIT-V"), tools=tools)
    source = tmp_path / "virtual" / "virtual-scene.zip"
    broken = tmp_path / "virtual-broken.zip"
    _break_virtual(source, broken)
    original_descriptor = json.loads(_member(source.read_bytes(), "scene.json"))
    old_box = original_descriptor["explicit"]["box"]
    original_frame = _member(source.read_bytes(), "start_frame.png")
    client = _client(tmp_path)
    with client:
        inspected = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("virtual-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert inspected.status_code == 200, inspected.text
        report = inspected.json()
        assert report["conclusion"] == "noncompliant"
        assert report["explicit_preview"]["available"] is True

        blocked = client.post(
            "/api/v1/composite-repair-plans",
            files={"file": ("virtual-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert blocked.status_code == 201, blocked.text
        assert blocked.json()["repair_plan"]["executable"] is False
        assert any("预览" in item for item in blocked.json()["repair_plan"]["blocking_reasons"])
        refused = client.post("/api/v1/composite-repair-jobs", json={
            "plan_id": blocked.json()["plan_id"],
            "plan_hash": blocked.json()["plan_hash"],
            "confirmed": True,
        })
        assert refused.status_code == 422

        preview = client.post(
            "/api/v1/scene-explicit-previews",
            files={"file": ("virtual-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert preview.status_code == 201, preview.text
        body = preview.json()
        again = client.post(
            "/api/v1/scene-explicit-previews",
            files={"file": ("virtual-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert again.json()["preview_id"] == body["preview_id"]
        before = client.get(body["before_url"])
        after = client.get(body["after_url"])
        assert before.status_code == 200 and after.status_code == 200
        assert before.content != after.content
        still = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("virtual-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert still.json()["conclusion"] == "noncompliant"

        bogus = client.post("/api/v1/composite-repair-jobs", json={
            "confirm_preview_id": "expv_" + "0" * 20,
            "confirmed": True,
        })
        assert bogus.status_code == 422
        assert bogus.json()["error"]["code"] == "INVALID_PREVIEW_ID"

        record_path = (
            tmp_path / "storage" / "composite_repairs" / "explicit_previews"
            / body["preview_id"] / "record.json"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["expires_at"] = "2000-01-01T00:00:00Z"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        expired = client.post("/api/v1/composite-repair-jobs", json={
            "confirm_preview_id": body["preview_id"],
            "confirmed": True,
        })
        assert expired.status_code == 422
        assert expired.json()["error"]["code"] == "PREVIEW_EXPIRED"
        record["expires_at"] = body["expires_at"]
        record_path.write_text(json.dumps(record), encoding="utf-8")

        job = client.post("/api/v1/composite-repair-jobs", json={
            "confirm_preview_id": body["preview_id"],
            "confirmed": True,
        })
        assert job.status_code == 201, job.text
        assert job.json()["validation"]["post_repair_conclusion"] == "compliant"
        output = client.get(job.json()["links"]["output"])
        assert output.status_code == 200
        descriptor = json.loads(_member(output.content, "scene.json"))
        frame = _member(output.content, "start_frame.png")
        assert descriptor["previous_explicit"]["text"] == "未按标准提示"
        assert "人工智能" in descriptor["explicit"]["text"]
        assert descriptor["explicit"]["position"] != "center"
        assert _crop(frame, old_box) == _crop(original_frame, old_box)
        checked = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("virtual-scene.zip", output.content, "application/zip")},
        )
        assert checked.json()["conclusion"] == "compliant", checked.text


def test_interactive_preview_confirms_without_touching_content(tmp_path):
    tools = _tools(tmp_path)
    content = tmp_path / "reply.txt"
    content.write_text("这是一段交互内容。\n", encoding="utf-8")
    label_interactive_scene(
        tmp_path / "interactive", text="AI生成", placements=["near_content"],
        content=content, content_role="text", aigc_obj=_aigc("PID-EXPLICIT-I"), tools=tools,
    )
    source = tmp_path / "interactive" / "interactive-scene.zip"
    broken = tmp_path / "interactive-broken.zip"
    _break_interactive(source, broken)
    original_content = _member(broken.read_bytes(), "content.txt")
    client = _client(tmp_path)
    with client:
        inspected = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("interactive-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert inspected.json()["conclusion"] == "noncompliant"
        assert inspected.json()["explicit_preview"]["available"] is True
        preview = client.post(
            "/api/v1/scene-explicit-previews",
            files={"file": ("interactive-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert preview.status_code == 201, preview.text
        body = preview.json()
        assert any(item["field"] == "explicit.persistent" for item in body["changes"])
        assert "previous_explicit" in body["position_rationale"] or any(
            item["field"] == "previous_explicit" for item in body["changes"]
        )
        untouched = client.post(
            "/api/v1/compliance-inspect",
            data={"profile": "auto"},
            files={"file": ("interactive-scene.zip", broken.read_bytes(), "application/zip")},
        )
        assert untouched.json()["conclusion"] == "noncompliant"
        job = client.post("/api/v1/composite-repair-jobs", json={
            "confirm_preview_id": body["preview_id"],
            "confirmed": True,
        })
        assert job.status_code == 201, job.text
        assert job.json()["validation"]["post_repair_conclusion"] == "compliant"
        output = client.get(job.json()["links"]["output"]).content
        descriptor = json.loads(_member(output, "scene.json"))
        assert descriptor["previous_explicit"]["text"] == "你好"
        assert descriptor["explicit"]["persistent"] is True
        assert descriptor["explicit"]["placements"][0] in {
            "near_content", "interface_top", "interface_bottom", "interface_background",
        }
        assert _member(output, "content.txt") == original_content


def test_busy_corner_moves_to_a_quieter_edge():
    font = find_font(None)
    image = Image.new("RGB", (640, 360), (240, 240, 240))
    rng = random.Random(0)
    pixels = image.load()
    for y in range(180, 360):
        for x in range(320, 640):
            pixels[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    _labeled, spec, rationale = choose_overlay_position(
        image, text="人工智能生成合成", color="#FFFFFF", background="#102033",
        font=font, preferred="bottom_right", old_box=None,
    )
    assert spec["position"] != "bottom_right"
    assert spec["position"] in rationale or "最不遮挡" in rationale
    assert "起伏" in rationale
