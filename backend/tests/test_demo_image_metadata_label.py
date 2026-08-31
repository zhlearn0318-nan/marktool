import hashlib
from pathlib import Path

import httpx

from scripts.demo_image_metadata_label import DemoConfig, run_demo


def test_demo_script_runs_detection_label_replace_and_repair_flow(tmp_path, capsys):
    source = tmp_path / "demo.jpg"
    source_bytes = b"original-jpeg-for-demo"
    source.write_bytes(source_bytes)
    existing = tmp_path / "existing.jpg"
    existing.write_bytes(b"existing-labeled-jpeg-for-demo")
    labeled_bytes = b"labeled-jpeg-for-demo"
    replaced_bytes = b"replaced-jpeg-for-demo"
    invalid_bytes = b"noncompliant-jpeg-for-demo"
    broken_bytes = b"broken-json-jpeg-for-demo"
    repaired_bytes = b"repaired-jpeg-for-demo"
    first_id = "11111111-2222-3333-4444-555555555555"
    replacement_id = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    label_job_id = "job_label_001"
    replace_job_id = "job_replace_001"
    repair_plan_id = "plan_demo_001"
    repair_job_id = "repair_job_demo_001"
    plan_hash = "a" * 64

    first_aigc = {
        "Label": "1",
        "ContentProducer": "ORG_DEMO_001",
        "ProduceID": first_id,
        "ReservedCode1": "",
        "ContentPropagator": "ORG_DEMO_001",
        "PropagateID": first_id,
        "ReservedCode2": "",
    }
    replacement_aigc = {
        **first_aigc,
        "Label": "2",
        "ProduceID": replacement_id,
        "PropagateID": replacement_id,
    }
    calls: list[tuple[str, str]] = []
    identifier_count = 0
    label_post_count = 0
    detect_count = 0
    repair_plan_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal identifier_count, label_post_count, detect_count, repair_plan_count
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v1/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "capabilities": {"image/jpeg": True, "image/png": True},
                },
            )
        if request.url.path == "/api/detect":
            detect_count += 1
            if detect_count == 1:
                compliance = {"conclusion": "compliant", "record_count": 1}
                metadata = first_aigc
            elif detect_count == 2:
                compliance = {
                    "conclusion": "not_found",
                    "record_count": 0,
                    "reason_codes": ["AIGC_NOT_FOUND"],
                }
                metadata = None
            elif detect_count == 3:
                compliance = {"conclusion": "compliant", "record_count": 1}
                metadata = first_aigc
            elif detect_count == 4:
                compliance = {"conclusion": "compliant", "record_count": 1}
                metadata = replacement_aigc
            elif detect_count == 5:
                compliance = {
                    "conclusion": "noncompliant",
                    "record_count": 1,
                    "reason_codes": ["AIGC_SCHEMA_INVALID"],
                }
                metadata = None
            elif detect_count == 6:
                compliance = {"conclusion": "compliant", "record_count": 1}
                metadata = replacement_aigc
            else:
                compliance = {
                    "conclusion": "noncompliant",
                    "record_count": 1,
                    "reason_codes": ["AIGC_JSON_INVALID"],
                }
                metadata = None
            return httpx.Response(
                200,
                json={
                    "detection": {
                        "aigc_metadata": metadata,
                        "metadata_compliance": compliance,
                    }
                },
            )
        if request.url.path == "/api/v1/metadata-label-identifiers":
            identifier_count += 1
            value = first_id if identifier_count == 1 else replacement_id
            return httpx.Response(201, json={"produce_id": value})
        if request.url.path == "/api/v1/metadata-label-jobs" and request.method == "POST":
            label_post_count += 1
            if label_post_count == 1:
                return httpx.Response(202, json={"job_id": label_job_id, "status": "queued"})
            if label_post_count == 2:
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "code": "AIGC_METADATA_EXISTS",
                            "message": "已有标识",
                        }
                    },
                )
            return httpx.Response(202, json={"job_id": replace_job_id, "status": "queued"})
        if request.url.path == f"/api/v1/metadata-label-jobs/{label_job_id}":
            return httpx.Response(
                200,
                json=_label_job(
                    label_job_id,
                    source_bytes,
                    labeled_bytes,
                    first_aigc,
                    "demo_labeled.jpg",
                ),
            )
        if request.url.path == f"/api/v1/metadata-label-jobs/{replace_job_id}":
            return httpx.Response(
                200,
                json=_label_job(
                    replace_job_id,
                    labeled_bytes,
                    replaced_bytes,
                    replacement_aigc,
                    "demo_replaced.jpg",
                ),
            )
        if request.url.path == f"/api/v1/metadata-label-jobs/{label_job_id}/output":
            return httpx.Response(200, content=labeled_bytes)
        if request.url.path == f"/api/v1/metadata-label-jobs/{replace_job_id}/output":
            return httpx.Response(200, content=replaced_bytes)
        if request.url.path == "/api/v1/metadata-repair-plans":
            repair_plan_count += 1
            if repair_plan_count == 2:
                return httpx.Response(
                    201,
                    json={
                        "plan_id": "plan_blocked_001",
                        "plan_hash": "b" * 64,
                        "inspection": {"conclusion": "noncompliant"},
                        "repair_plan": {
                            "repairability": "manual_review",
                            "executable": False,
                            "blocking_reasons": ["无法可靠恢复七字段"],
                        },
                    },
                )
            return httpx.Response(
                201,
                json={
                    "plan_id": repair_plan_id,
                    "plan_hash": plan_hash,
                    "inspection": {"conclusion": "noncompliant"},
                    "repair_plan": {
                        "repairability": "confirmable",
                        "executable": True,
                        "actions": ["补为空字符串"],
                    },
                },
            )
        if request.url.path == "/api/v1/metadata-repair-jobs" and request.method == "POST":
            return httpx.Response(202, json={"job_id": repair_job_id, "status": "queued"})
        if request.url.path == f"/api/v1/metadata-repair-jobs/{repair_job_id}":
            return httpx.Response(
                200,
                json={
                    "job_id": repair_job_id,
                    "status": "succeeded",
                    "stage": "completed",
                    "validation": {
                        "post_repair_conclusion": "compliant",
                        "pixel_sha256_unchanged": True,
                    },
                    "output": {
                        "file_name": "demo_repaired.jpg",
                        "download_url": f"/api/v1/metadata-repair-jobs/{repair_job_id}/output",
                        "sha256": hashlib.sha256(repaired_bytes).hexdigest(),
                    },
                },
            )
        if request.url.path == f"/api/v1/metadata-repair-jobs/{repair_job_id}/output":
            return httpx.Response(200, content=repaired_bytes)
        return httpx.Response(404, json={"error": {"message": "not found"}})

    def build_fixture(path: Path, value) -> None:
        path.write_bytes(invalid_bytes if isinstance(value, dict) else broken_bytes)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = run_demo(
        DemoConfig(
            image_path=source,
            output_dir=tmp_path / "outputs",
            existing_image_path=existing,
            base_url="http://testserver",
        ),
        client=client,
        fixture_builder=build_fixture,
    )
    client.close()

    assert result.produce_id == first_id
    assert result.label_job_id == label_job_id
    assert result.labeled_path.read_bytes() == labeled_bytes
    assert result.replacement_produce_id == replacement_id
    assert result.replace_job_id == replace_job_id
    assert result.replaced_path.read_bytes() == replaced_bytes
    assert result.repair_plan_id == repair_plan_id
    assert result.repair_job_id == repair_job_id
    assert result.repaired_path.read_bytes() == repaired_bytes
    assert result.aigc_metadata == replacement_aigc
    assert source.read_bytes() == source_bytes
    assert calls.count(("POST", "/api/detect")) == 7
    assert calls.count(("POST", "/api/v1/metadata-label-jobs")) == 3
    assert calls.count(("POST", "/api/v1/metadata-repair-plans")) == 2
    output = capsys.readouterr().out
    assert "--- 已打标图片对照读取 ---" in output
    assert "[2/19] 打标前检测：not_found" in output
    assert "[7/19] 重复写入已安全拦截" in output
    assert "[12/19] 不合规检测通过" in output
    assert "[16/19] 修复后复检通过" in output
    assert "[19/19] 安全边界通过" in output
    assert "=== 完整演示完成 ===" in output


def _label_job(job_id, input_bytes, output_bytes, aigc, filename):
    return {
        "job_id": job_id,
        "status": "succeeded",
        "stage": "completed",
        "input": {"sha256": hashlib.sha256(input_bytes).hexdigest()},
        "output": {
            "file_name": filename,
            "download_url": f"/api/v1/metadata-label-jobs/{job_id}/output",
            "sha256": hashlib.sha256(output_bytes).hexdigest(),
        },
        "embedded_metadata": {"AIGC": aigc},
        "validation": {
            "read_back_succeeded": True,
            "schema_valid": True,
            "single_aigc_record": True,
            "media_integrity_valid": True,
        },
    }
