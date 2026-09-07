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
    assert d["registry"]["mode"] == "skipped"       # 未接登记库，不报错


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


def test_inspect_image_not_enabled_415(client):
    """图片检测属于另一套 /api/detect MVP，此接口只做 MP4（能力开关）。"""
    r = _post(client, PNG_BYTES, fname="x.png", content_type="image/png")
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
