"""合规检测接口契约测试（合规方案 §8.3 / §9.2 验收经 API 侧）。

POST /api/v1/compliance-inspect：
- 200：无标识 → not_found；规范标识 → compliant；结构损坏 → indeterminate；
- 415：非媒体 / 图片（能力开关未开）→ UNSUPPORTED_MEDIA_TYPE；
- 413：超过大小上限；400：缺少 multipart 文件；
- 只读无状态：报告返回后上传副本被删除。
"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.adapters import Mp4Adapter
from app.config import Settings
from app.core import aigc
from app.main import create_app
from tests.common import CLEAN_MP4, EXIFTOOL_CONFIG, VALID_AIGC

PNG_BYTES = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _make_client(tmp_path, max_bytes: int = 10 * 1024 * 1024) -> TestClient:
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = max_bytes
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    app = create_app(settings=settings,
                     storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with _make_client(tmp_path) as c:
        yield c


def _post(client: TestClient, data: bytes, fname="in.mp4",
          content_type="video/mp4"):
    return client.post("/api/v1/compliance-inspect",
                       files={"file": (fname, data, content_type)})


def _labeled_bytes(tmp_path) -> bytes:
    """复制干净样本并写入一份规范 AIGC 标识，返回字节。"""
    p = tmp_path / "labeled.mp4"
    shutil.copyfile(CLEAN_MP4, p)
    Mp4Adapter(exiftool="exiftool",
               exiftool_config=str(EXIFTOOL_CONFIG))._exiftool_cmd(
        ["-overwrite_original",
         "-XMP-aigc:AIGC=" + aigc.serialize_aigc(VALID_AIGC)], str(p))
    return p.read_bytes()


def test_inspect_not_found_clean(client):
    r = _post(client, CLEAN_MP4.read_bytes())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["conclusion"] == "not_found"
    assert d["record_count"] == 0
    assert d["reason_code"] is None
    assert d["detected_mime_type"] == "video/mp4"
    assert d["media_status"] == "ok"
    assert d["c2pa_presence"] == "absent"
    assert d["request_id"]
    assert isinstance(d["elapsed_ms"], int)


def test_inspect_compliant(client, tmp_path):
    r = _post(client, _labeled_bytes(tmp_path))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["conclusion"] == "compliant"
    assert d["record_count"] == 1
    assert d["issues"] == []
    assert d["candidates"][0]["parseable"] is True
    # 登记库已接上：报告的是"这个编号本机没见过"，而不是"本服务没接登记库"。
    # 两者对使用者是两件事——后者是能力缺失，前者是关于这份文件的一条信息。
    assert d["registry"]["mode"] == "local_produceid"
    assert d["registry"]["produce_id"] == VALID_AIGC["ProduceID"]
    assert d["registry"]["known"] is False


def test_inspect_reports_known_produce_id_after_labeling(tmp_path):
    """打标登记过的 MP4，再检测时应报 known=True——三种模态共用一个登记库。"""
    import json
    import time

    with _make_client(tmp_path) as c:
        request = {"standard": "GB45438-2025", "modality": "video",
                   "existing_metadata_policy": "reject", "AIGC": VALID_AIGC}
        accepted = c.post(
            "/api/v1/metadata-label-jobs",
            files={"file": ("clip.mp4", CLEAN_MP4.read_bytes(), "video/mp4"),
                   "request": (None, json.dumps(request, ensure_ascii=False),
                               "application/json")})
        assert accepted.status_code in (200, 201, 202), accepted.text
        job_url = f"/api/v1/metadata-label-jobs/{accepted.json()['job_id']}"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = c.get(job_url).json()
            if job.get("status") in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job

        output = c.get(job["output"]["download_url"]).content
        report = _post(c, output).json()

    assert report["conclusion"] == "compliant", report
    assert report["registry"]["mode"] == "local_produceid"
    assert report["registry"]["known"] is True


def test_inspect_duplicate_via_endpoint(client, tmp_path):
    """新载体 + 旧载体共存 → DUPLICATE_RECORDS（§9.2 验收经 API）。"""
    p = tmp_path / "dup.mp4"
    shutil.copyfile(CLEAN_MP4, p)
    raw = aigc.serialize_aigc(VALID_AIGC)
    adapter = Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    adapter._exiftool_cmd(["-overwrite_original", "-XMP-aigc:AIGC=" + raw], str(p))
    adapter._exiftool_cmd(["-overwrite_original", "-QuickTime:Comment=" + raw], str(p))
    r = _post(client, p.read_bytes())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["conclusion"] == "noncompliant"
    assert d["reason_code"] == "DUPLICATE_RECORDS"
    assert d["record_count"] == 2
    assert d["repairability"] == "auto_fixable"


def test_inspect_broken_moov_indeterminate(client):
    """结构损坏（截掉 moov）→ indeterminate，不误判不合规。"""
    r = _post(client, CLEAN_MP4.read_bytes()[:200])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["conclusion"] == "indeterminate"
    assert d["reason_code"] == "UNREADABLE_CARRIER"
    assert d["bmff"]["has_moov"] is False
    assert d["c2pa_presence"] != "absent"


def test_inspect_unsupported_text_415(client):
    r = _post(client, b"plain text not media", fname="x.txt",
              content_type="text/plain")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_inspect_image_enabled_returns_report(client):
    """图片检测已接入：返回与 MP4 同构的报告（§4.5，前端无需分叉渲染）。"""
    r = _post(client, PNG_BYTES, fname="x.png", content_type="image/png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected_mime_type"] == "image/png"
    assert body["conclusion"] in ("not_found", "compliant", "noncompliant",
                                  "indeterminate")
    assert body["bmff"]["applicable"] is False


def test_inspect_unsupported_format_still_415(client):
    r = _post(client, b"GIF89a" + b"\x00" * 32, fname="x.gif",
              content_type="image/gif")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_inspect_too_large_413(tmp_path):
    with _make_client(tmp_path, max_bytes=100) as c:
        r = _post(c, CLEAN_MP4.read_bytes())
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_inspect_missing_file_400(client):
    r = client.post("/api/v1/compliance-inspect", files={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_MULTIPART"


def test_inspect_is_readonly_and_no_leak(client):
    """只读无状态：检测后上传副本被删除，存储区无残留。"""
    clean = CLEAN_MP4.read_bytes()
    before = bytes(CLEAN_MP4.read_bytes())              # 源 fixture 不得被改动
    r = _post(client, clean)
    assert r.status_code == 200
    assert CLEAN_MP4.read_bytes() == before             # 源文件未被触碰
    original_dir = client.app.state.storage.original_dir
    assert list(original_dir.iterdir()) == []           # 上传副本已删除，无泄漏
