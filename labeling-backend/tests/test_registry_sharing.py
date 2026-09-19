"""编号登记库必须是**一个**库，被打标流水线、合规检测、修复工作台共用。

背景（融合时真实踩到的坑）：修复工作台用自己的默认配置构造，登记库落在
``<repo>/data/aigc_identifiers.sqlite3``；打标流水线与 ``/compliance-inspect``
则用 ``<storage.root>/aigc_identifiers.sqlite3``。两个库互不可见，后果是双向的：

* 修复写出的编号，在随后对结果文件做合规检测时报 ``unverified``；
* 打标登记的编号，修复工作台看不见——同一个 ProduceID 可以被两条流程
  各自用在不同的图片上，编号复用检查静默失效。

只断言路径相等是不够的（路径可能相等而库对象仍各自为政），所以下面既有
路径断言，也有一次真实的跨流程行为断言。
"""
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.core.image_bridge import build_repair_service, resolve_registry_path
from app.main import create_app
from tests.fixtures import VALID_AIGC, broken_json_xmp, make_png


def _find_exiftool() -> str | None:
    configured = os.getenv("EXIFTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("exiftool") or shutil.which("exiftool.exe")


@pytest.fixture
def client(tmp_path):
    if not _find_exiftool():
        pytest.skip("需要 ExifTool；请设置 EXIFTOOL_PATH")
    app = create_app(storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    with TestClient(app) as c:
        yield c, tmp_path


def test_repair_service_uses_the_shared_registry_path():
    settings = load_settings()
    assert str(build_repair_service(settings).config.identifier_database_path) == str(
        resolve_registry_path(settings)
    )


def test_storage_root_override_also_moves_the_registry(client, ):
    c, tmp_path = client
    from app.core.image_bridge import resolve_registry_path

    settings = c.app.state.settings
    registry = Path(c.app.state.repair_service.config.identifier_database_path)
    # 覆盖 storage_root 时，登记库必须跟着进临时目录，否则测试之间会串库
    assert registry == tmp_path / "storage" / "aigc_identifiers.sqlite3"
    assert str(registry) == str(resolve_registry_path(settings))


def _wait(client, url, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(url).json()
        if body.get("status") in {"succeeded", "failed"}:
            return body
        time.sleep(0.1)
    pytest.fail("任务未在时限内结束")


def test_identifier_registered_by_labeling_blocks_repair(client, tmp_path):
    """打标登记过的编号，修复工作台必须看得见并拒绝复用。"""
    c, _ = client
    producer = VALID_AIGC["ContentProducer"]
    shared_produce_id = "PRD-SHARED-REGISTRY-0001"

    source = make_png(tmp_path / "labeled.png")
    request = {
        "standard": "GB45438-2025",
        "modality": "image",
        "existing_metadata_policy": "reject",
        "AIGC": {**VALID_AIGC, "ProduceID": shared_produce_id,
                 "PropagateID": shared_produce_id},
    }
    accepted = c.post(
        "/api/v1/metadata-label-jobs",
        files={"file": ("labeled.png", Path(source).read_bytes(), "image/png"),
               "request": (None, json.dumps(request, ensure_ascii=False), "application/json")},
    )
    assert accepted.status_code in (200, 201, 202), accepted.text
    labeled = _wait(c, f"/api/v1/metadata-label-jobs/{accepted.json()['job_id']}")
    assert labeled["status"] == "succeeded", labeled

    # 打标结果文件回读，登记库应已认得这个编号
    output = c.get(labeled["output"]["download_url"]).content
    report = c.post("/api/v1/compliance-inspect",
                    files={"file": ("labeled.png", output, "image/png")}).json()
    assert report["registry"]["status"] in {"verified", "partially_verified"}, report["registry"]

    # 另一张图（标识损坏，因此修复计划可执行）复用同一编号 → 写入时必须被拦。
    # 尺寸必须与上一张不同：登记库判的是「同一编号是否落到了**另一个像素指纹**」，
    # 像素相同的文件复用同一编号是合法的（那正是同内容传播），不会被拦。
    other = make_png(tmp_path / "other.png", raw_xmp=broken_json_xmp(),
                     size=(401, 301))
    options = {
        "trusted_input": {
            "AIGC": {**VALID_AIGC, "ContentProducer": producer,
                     "ProduceID": shared_produce_id,
                     "PropagateID": shared_produce_id,
                     "ContentPropagator": producer},
            "source_type": "authorized_manual",
            "source_reference": "TEST-REGISTRY-SHARING",
            "write_context": "initial_generation",
        }
    }
    planned = c.post(
        "/api/v1/metadata-repair-plans",
        files={"file": ("other.png", Path(other).read_bytes(), "image/png"),
               "request": (None, json.dumps(options, ensure_ascii=False), "application/json")},
    )
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    assert plan["repair_plan"]["executable"] is True, plan["repair_plan"]["blocking_reasons"]

    confirmed = c.post("/api/v1/metadata-repair-jobs", json={
        "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"],
        "confirmed": True, "operator_label": "回归测试操作人"})
    assert confirmed.status_code == 202, confirmed.text
    finished = _wait(c, f"/api/v1/metadata-repair-jobs/{confirmed.json()['job_id']}")

    assert finished["status"] == "failed", finished
    assert finished["error"]["code"] == "AIGC_IDENTIFIER_DUPLICATE", finished["error"]
    assert not finished["output"], "失败任务不应产出结果文件"


def test_identifier_written_by_repair_is_visible_to_inspection(client, tmp_path):
    """反向：修复写出的编号，随后的合规检测要能认出来。"""
    c, _ = client
    source = make_png(tmp_path / "legacy.png", raw_xmp=broken_json_xmp())
    produce_id = "PRD-REPAIR-REGISTRY-0001"
    options = {
        "trusted_input": {
            "AIGC": {**VALID_AIGC, "ProduceID": produce_id,
                     "PropagateID": produce_id},
            "source_type": "authorized_manual",
            "source_reference": "TEST-REGISTRY-SHARING",
            "write_context": "initial_generation",
        }
    }
    plan = c.post(
        "/api/v1/metadata-repair-plans",
        files={"file": ("legacy.png", Path(source).read_bytes(), "image/png"),
               "request": (None, json.dumps(options, ensure_ascii=False), "application/json")},
    ).json()
    assert plan["repair_plan"]["executable"] is True, plan["repair_plan"]["blocking_reasons"]

    confirmed = c.post("/api/v1/metadata-repair-jobs", json={
        "plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"],
        "confirmed": True, "operator_label": "回归测试操作人"})
    finished = _wait(c, f"/api/v1/metadata-repair-jobs/{confirmed.json()['job_id']}")
    assert finished["status"] == "succeeded", finished

    repaired = c.get(finished["output"]["download_url"]).content
    report = c.post("/api/v1/compliance-inspect",
                    files={"file": ("repaired.png", repaired, "image/png")}).json()
    assert report["conclusion"] == "compliant", report
    assert report["registry"]["status"] == "verified", report["registry"]
