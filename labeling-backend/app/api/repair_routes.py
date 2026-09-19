import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.metadata.repair_service import (
    MetadataRepairRequestError,
    MetadataRepairService,
)
from app.request_limits import RequestBodyTooLarge
from app.schemas.metadata_jobs import ApiErrorResponse
from app.schemas.metadata_repairs import (
    RepairConfirmationRequest,
    RepairJobAcceptedResponse,
    RepairJobStatusResponse,
    RepairPlanOptions,
    RepairPlanResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["metadata-repair"])

_default_service: Optional[MetadataRepairService] = None
_service_lock = threading.Lock()


def _request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def get_metadata_repair_service() -> MetadataRepairService:
    global _default_service
    if _default_service is None:
        with _service_lock:
            if _default_service is None:
                _default_service = MetadataRepairService()
    return _default_service


def shutdown_metadata_repair_service() -> None:
    global _default_service
    with _service_lock:
        service = _default_service
        _default_service = None
    if service is not None:
        service.close()


def _problem(
    status_code: int,
    request_id: str,
    code: str,
    message: str,
    details: Optional[list] = None,
) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"request_id": request_id, "error": error},
        headers={"X-Request-ID": request_id},
    )


def _validation_details(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "reason": item["msg"],
        }
        for item in exc.errors()
    ]


@router.post(
    "/metadata-repair-plans",
    status_code=201,
    response_model=RepairPlanResponse,
    responses={
        400: {"model": ApiErrorResponse},
        413: {"model": ApiErrorResponse},
        415: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
        503: {"model": ApiErrorResponse},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "request": {
                                "type": "string",
                                "description": (
                                    "可选的 RepairPlanOptions UTF-8 JSON；仅在有可信来源时提供"
                                ),
                            },
                        },
                    }
                }
            },
        }
    },
)
async def create_metadata_repair_plan(
    http_request: Request,
    service: MetadataRepairService = Depends(get_metadata_repair_service),
):
    request_id = _request_id()
    try:
        form = await http_request.form()
    except RequestBodyTooLarge:
        raise
    except Exception:
        logger.exception("repair plan multipart parse failed: request_id=%s", request_id)
        return _problem(400, request_id, "INVALID_MULTIPART", "请求必须使用 multipart/form-data")

    file_part = form.get("file")
    request_part = form.get("request")
    if not isinstance(file_part, UploadFile):
        return _problem(400, request_id, "INVALID_MULTIPART", "multipart 请求必须包含 file")
    try:
        data = await file_part.read(service.config.max_upload_bytes + 1)
        if request_part is None or request_part == "":
            options = RepairPlanOptions()
        else:
            if isinstance(request_part, UploadFile):
                raw = (await request_part.read(1024 * 1024 + 1)).decode("utf-8")
            elif isinstance(request_part, str):
                raw = request_part
            else:
                raise ValueError("request 类型无效")
            if len(raw.encode("utf-8")) > 1024 * 1024:
                raise ValueError("request JSON 过大")
            decoded = json.loads(raw)
            if not isinstance(decoded, dict):
                raise ValueError("request 顶层必须是对象")
            options = RepairPlanOptions.model_validate(decoded)
    except ValidationError as exc:
        return _problem(
            422,
            request_id,
            "REPAIR_REQUEST_SCHEMA_INVALID",
            "修复计划参数不符合接口约定",
            _validation_details(exc),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _problem(400, request_id, "INVALID_REPAIR_REQUEST", str(exc))

    try:
        payload = await run_in_threadpool(
            service.create_plan,
            filename=file_part.filename,
            data=data,
            trusted_input=options.trusted_input,
        )
    except MetadataRepairRequestError as exc:
        return _problem(exc.status_code, request_id, exc.code, exc.message, exc.details)
    except Exception:
        logger.exception("repair plan creation failed: request_id=%s", request_id)
        return _problem(500, request_id, "INTERNAL_ERROR", "服务器未能安全生成修复计划")
    return JSONResponse(
        status_code=201,
        content=payload,
        headers={"X-Request-ID": payload["request_id"]},
    )


@router.get(
    "/metadata-repair-plans/{plan_id}",
    response_model=RepairPlanResponse,
    responses={404: {"model": ApiErrorResponse}},
)
def get_metadata_repair_plan(
    plan_id: str,
    service: MetadataRepairService = Depends(get_metadata_repair_service),
):
    payload = service.get_plan(plan_id)
    if payload is None:
        return _problem(404, _request_id(), "REPAIR_PLAN_NOT_FOUND", "修复计划不存在")
    return JSONResponse(content=payload, headers={"X-Request-ID": payload["request_id"]})


@router.post(
    "/metadata-repair-jobs",
    status_code=202,
    response_model=RepairJobAcceptedResponse,
    responses={404: {"model": ApiErrorResponse}, 409: {"model": ApiErrorResponse}},
)
def create_metadata_repair_job(
    request: RepairConfirmationRequest,
    service: MetadataRepairService = Depends(get_metadata_repair_service),
):
    try:
        payload = service.confirm_plan(**request.model_dump())
    except MetadataRepairRequestError as exc:
        return _problem(exc.status_code, _request_id(), exc.code, exc.message, exc.details)
    return JSONResponse(
        status_code=202,
        content=payload,
        headers={"X-Request-ID": payload["request_id"]},
    )


@router.get(
    "/metadata-repair-jobs/{job_id}",
    response_model=RepairJobStatusResponse,
    responses={404: {"model": ApiErrorResponse}},
)
def get_metadata_repair_job(
    job_id: str,
    service: MetadataRepairService = Depends(get_metadata_repair_service),
):
    payload = service.get_job(job_id)
    if payload is None:
        return _problem(404, _request_id(), "REPAIR_JOB_NOT_FOUND", "修复任务不存在")
    return JSONResponse(content=payload, headers={"X-Request-ID": payload["request_id"]})


@router.get(
    "/metadata-repair-jobs/{job_id}/output",
    responses={
        404: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        410: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
    },
)
def download_metadata_repair_output(
    job_id: str,
    service: MetadataRepairService = Depends(get_metadata_repair_service),
):
    record = service.download_record(job_id)
    if record is None:
        return _problem(404, _request_id(), "REPAIR_JOB_NOT_FOUND", "修复任务不存在")
    if record["status"] != "succeeded":
        return _problem(409, record["request_id"], "OUTPUT_NOT_READY", "修复结果尚不可下载")
    if record["stage"] == "files_purged":
        return _problem(410, record["request_id"], "OUTPUT_EXPIRED", "修复文件已超过保留期限")
    output_path = Path(record["output_path"]).resolve()
    try:
        output_path.relative_to(service.storage_root)
    except ValueError:
        return _problem(500, record["request_id"], "INTERNAL_ERROR", "修复结果文件引用无效")
    if not output_path.is_file():
        return _problem(500, record["request_id"], "INTERNAL_ERROR", "修复结果文件不可用")
    return FileResponse(
        path=output_path,
        media_type=record["output_mime_type"],
        filename=record["output_file_name"],
        headers={"X-Request-ID": record["request_id"]},
    )
