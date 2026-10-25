"""多模态与场景的修复计划。单模态修复仍走 /metadata-repair-plans。"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile

from ..composite.compliance import (
    apply_repair_plan,
    build_repair_plan,
    classify_upload,
    inspect_composite,
    plan_hash,
)
from ..config import Settings
from ..core.errors import FILE_TOO_LARGE, UNSUPPORTED_MEDIA_TYPE, ApiError
from ..tools import tools_from_settings
from .deps import get_settings

router = APIRouter(prefix="/api/v1", tags=["composite-repair"])
_lock = threading.Lock()


def _root(settings: Settings) -> Path:
    path = Path(settings.storage.root) / "composite_repairs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@router.post("/composite-repair-plans", status_code=201)
async def create_composite_repair_plan(
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    form = await http_request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise ApiError(400, "INVALID_MULTIPART", "multipart 请求必须包含 file")
    data = await upload.read()
    if not data:
        raise ApiError(400, "EMPTY_FILE", "上传文件不能为空")
    if len(data) > settings.storage.max_file_bytes:
        raise ApiError(413, FILE_TOO_LARGE, "文件超过大小上限。")
    plan_id = f"cplan_{uuid.uuid4().hex}"
    root = _root(settings)
    stored = root / "inputs" / plan_id
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(data)
    if classify_upload(stored) is None:
        stored.unlink(missing_ok=True)
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE, "该修复入口只接受多模态文件、多模态结果包或场景包。")
    digest = hashlib.sha256(data).hexdigest()
    tools = tools_from_settings(settings)
    report = await run_in_threadpool(
        inspect_composite, stored,
        file_name=upload.filename or "upload",
        size_bytes=len(data), sha256=digest,
        request_id=getattr(http_request.state, "request_id", plan_id),
        tools=tools,
    )
    plan = build_repair_plan(report)
    digest_plan = plan_hash(digest, plan)
    record = {
        "plan_id": plan_id,
        "plan_hash": digest_plan,
        "file_name": upload.filename or "upload",
        "input_path": str(stored),
        "input_sha256": digest,
        "inspection": report,
        "repair_plan": plan,
    }
    _write_json(root / "plans" / f"{plan_id}.json", record)
    return JSONResponse(status_code=201, content={
        "plan_id": plan_id,
        "plan_hash": digest_plan,
        "status": "pending",
        "inspection": report,
        "repair_plan": plan,
    })


@router.post("/composite-repair-jobs", status_code=201)
async def create_composite_repair_job(
    http_request: Request,
    settings: Settings = Depends(get_settings),
):
    body = await http_request.json()
    plan_id = str(body.get("plan_id") or "")
    submitted = str(body.get("plan_hash") or "")
    if body.get("confirmed") is not True:
        raise ApiError(400, "CONFIRMATION_REQUIRED", "执行修复前需要明确确认。")
    root = _root(settings)
    record_path = root / "plans" / f"{plan_id}.json"
    if not record_path.is_file():
        raise ApiError(404, "PLAN_NOT_FOUND", "修复方案不存在或已过期。")
    record = _read_json(record_path)
    if submitted != record["plan_hash"]:
        raise ApiError(409, "PLAN_HASH_MISMATCH", "方案已被改动，请重新生成。")
    plan = record["repair_plan"]
    if not plan.get("executable"):
        raise ApiError(422, "PLAN_NOT_EXECUTABLE", "该方案不可执行。")
    job_id = f"cjob_{uuid.uuid4().hex}"
    output = root / "outputs" / f"{job_id}{Path(record['file_name']).suffix or '.bin'}"
    tools = tools_from_settings(settings)

    def _run() -> dict:
        apply_repair_plan(Path(record["input_path"]), output, plan, tools)
        data = output.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        report = inspect_composite(
            output, file_name=output.name, size_bytes=len(data), sha256=digest,
            request_id=job_id, tools=tools,
        )
        return report

    with _lock:
        report = await run_in_threadpool(_run)
    passed = report.get("conclusion") == "compliant"
    job = {
        "job_id": job_id,
        "plan_id": plan_id,
        "status": "succeeded" if passed else "failed",
        "stage": "completed" if passed else "failed",
        "progress": 100,
        "output_name": output.name,
        "output_path": str(output),
        "validation": {"post_repair_conclusion": report.get("conclusion")},
        "inspection": report,
    }
    _write_json(root / "jobs" / f"{job_id}.json", job)
    return JSONResponse(status_code=201, content={
        "job_id": job_id,
        "plan_id": plan_id,
        "status": job["status"],
        "stage": job["stage"],
        "progress": 100,
        "validation": job["validation"],
        "links": {"output": f"/api/v1/composite-repair-jobs/{job_id}/output"},
    })


@router.get("/composite-repair-jobs/{job_id}")
async def get_composite_repair_job(job_id: str, settings: Settings = Depends(get_settings)):
    path = _root(settings) / "jobs" / f"{job_id}.json"
    if not path.is_file():
        raise ApiError(404, "JOB_NOT_FOUND", "修复任务不存在。")
    job = _read_json(path)
    return {
        "job_id": job["job_id"],
        "plan_id": job["plan_id"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": job["progress"],
        "validation": job["validation"],
        "links": {"output": f"/api/v1/composite-repair-jobs/{job_id}/output"},
    }


@router.get("/composite-repair-jobs/{job_id}/output")
async def download_composite_repair_output(job_id: str, settings: Settings = Depends(get_settings)):
    path = _root(settings) / "jobs" / f"{job_id}.json"
    if not path.is_file():
        raise ApiError(404, "JOB_NOT_FOUND", "修复任务不存在。")
    job = _read_json(path)
    output = Path(job["output_path"])
    if not output.is_file():
        raise ApiError(404, "OUTPUT_NOT_FOUND", "修复结果尚未生成。")
    return FileResponse(output, filename=job.get("output_name") or output.name)
