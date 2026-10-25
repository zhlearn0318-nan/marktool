"""多模态与场景 HTTP 接口。"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from common import EXIFTOOL_CONFIG
from media_samples import make_av_subtitle_video

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("exiftool") is None,
    reason="需要 ffmpeg 与 exiftool",
)


@pytest.fixture
def client(tmp_path):
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = 20 * 1024 * 1024
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    app = create_app(settings=settings, storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    with TestClient(app) as test_client:
        yield test_client


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


def test_multimodal_label_parse_verify_and_download(client, tmp_path):
    video = make_av_subtitle_video(tmp_path)
    request = {"standard": "GB45438-2025", "existing_metadata_policy": "reject", "AIGC": _aigc("PID-API-MM")}
    response = client.post(
        "/api/v1/multimodal/label",
        files={"file": ("clip.mp4", video.read_bytes(), "video/mp4")},
        data={"request": json.dumps(request)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is True
    assert body["combination"] == "audio+text+video"
    downloaded = client.get(body["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content[:2] == b"PK"

    verified = client.post(
        "/api/v1/multimodal/verify",
        files={"file": ("bundle.zip", downloaded.content, "application/zip")},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["passed"] is True
    parsed = client.post(
        "/api/v1/multimodal/parse",
        files={"file": ("bundle.zip", downloaded.content, "application/zip")},
    )
    assert parsed.status_code == 200
    roles = {item["role"] for item in parsed.json()["files"]}
    assert roles == {"audio", "text", "video"}


def test_virtual_and_interactive_http(client, tmp_path):
    virtual = client.post(
        "/api/v1/scenes/virtual/label",
        data={"request": json.dumps({
            "standard": "GB45438-2025",
            "AIGC": _aigc("PID-API-VS"),
            "explicit": {"text": "人工智能生成合成", "position": "top_left"},
        })},
    )
    assert virtual.status_code == 200, virtual.text
    assert virtual.json()["passed"] is True
    package = client.get(virtual.json()["download_url"]).content
    checked = client.post(
        "/api/v1/scenes/virtual/verify",
        files={"file": ("virtual-scene.zip", package, "application/zip")},
    )
    assert checked.status_code == 200
    assert checked.json()["passed"] is True
    parsed = client.post(
        "/api/v1/scenes/virtual/parse",
        files={"file": ("virtual-scene.zip", package, "application/zip")},
    )
    assert parsed.json()["count"] == 1

    rejected = client.post(
        "/api/v1/scenes/virtual/label",
        data={"request": json.dumps({
            "standard": "GB45438-2025",
            "AIGC": _aigc("PID-API-VS-BAD"),
            "explicit": {"text": "你好"},
        })},
    )
    assert rejected.status_code == 422

    interactive = client.post(
        "/api/v1/scenes/interactive/label",
        files={"file": ("reply.txt", "一轮回答".encode("utf-8"), "text/plain")},
        data={"request": json.dumps({
            "standard": "GB45438-2025",
            "content_role": "text",
            "AIGC": _aigc("PID-API-IS"),
            "explicit": {
                "text": "AI生成",
                "placements": ["interface_bottom"],
                "persistent": True,
                "color": "#102033",
                "background": "#F4EBD4",
            },
        })},
    )
    assert interactive.status_code == 200, interactive.text
    assert interactive.json()["passed"] is True
    scene_zip = client.get(interactive.json()["download_url"]).content
    scene_check = client.post(
        "/api/v1/scenes/interactive/verify",
        files={"file": ("interactive-scene.zip", scene_zip, "application/zip")},
    )
    assert scene_check.status_code == 200
    assert scene_check.json()["passed"] is True


def test_cli_virtual_label(tmp_path, monkeypatch):
    from app.cli import main
    from app.config import Settings

    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    monkeypatch.setattr("app.cli.load_settings", lambda: settings)
    code = main([
        "scene", "virtual", "label",
        "--output", str(tmp_path / "cli-out"),
        "--producer", "ORG_1565201000000016",
        "--produce-id", "PID-CLI-VS",
        "--text", "AI生成",
        "--position", "bottom_edge",
    ])
    assert code == 0
    assert (tmp_path / "cli-out" / "virtual-scene.zip").is_file()
