import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.metadata.exiftool_client import ExifToolClient, ExifToolNotFoundError
from app.metadata.job_service import (
    MetadataJobRequestError,
    MetadataLabelJobService,
    iso_utc,
)
from app.schemas.metadata_jobs import (
    ApiErrorResponse,
    HealthResponse,
    IdentifierResponse,
    JobAcceptedResponse,
    JobStatusResponse,
    MetadataLabelRequest,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1", tags=["metadata-labeling"])

_default_service: Optional[MetadataLabelJobService] = None
_service_lock = threading.Lock()


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def get_metadata_job_service() -> MetadataLabelJobService:
    global _default_service
    if _default_service is None:
        with _service_lock:
            if _default_service is None:
                _default_service = MetadataLabelJobService()
    return _default_service


def _problem(
    status_code: int,
    request_id: str,
    code: str,
    message: str,
    field_errors: Optional[list[dict[str, str]]] = None,
) -> JSONResponse:
    error = {"code": code, "message": message}
    if field_errors:
        error["field_errors"] = field_errors
    return JSONResponse(
        status_code=status_code,
        content={"request_id": request_id, "error": error},
        headers={"X-Request-ID": request_id},
    )


def _validation_field_errors(exc: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "reason": error["msg"],
        }
        for error in exc.errors()
    ]


@router.post(
    "/metadata-label-identifiers",
    response_model=IdentifierResponse,
    status_code=201,
)
def create_metadata_label_identifier(response: Response):
    """由后端生成 ProduceID；前端再把它放进最终七字段请求。"""
    request_id = new_request_id()
    response.headers["X-Request-ID"] = request_id
    return IdentifierResponse(
        request_id=request_id,
        produce_id=str(uuid.uuid4()).upper(),
        generated_at=iso_utc(datetime.now(timezone.utc)),
    )


@router.post(
    "/metadata-label-jobs",
    status_code=202,
    response_model=JobAcceptedResponse,
    responses={
        400: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        413: {"model": ApiErrorResponse},
        415: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file", "request"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "request": {
                                "type": "string",
                                "description": "MetadataLabelRequest 的 UTF-8 JSON",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def create_metadata_label_job(
    http_request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    service: MetadataLabelJobService = Depends(get_metadata_job_service),
):
    request_id = new_request_id()
    try:
        form = await http_request.form()
    except Exception:
        logger.exception("metadata job creation failed: request_id=%s", request_id)
        return _problem(
            400,
            request_id,
            "INVALID_MULTIPART",
            "请求必须使用 multipart/form-data，并包含 file 与 request。",
        )

    file_part = form.get("file")
    json_part = form.get("request")
    if not isinstance(file_part, UploadFile) or json_part is None:
        return _problem(
            400,
            request_id,
            "INVALID_MULTIPART",
            "multipart 请求必须同时包含 file 和 request。",
        )

    try:
        data = await file_part.read(service.config.max_upload_bytes + 1)
        if isinstance(json_part, UploadFile):
            if json_part.content_type not in {
                "application/json",
                "application/ld+json",
            }:
                return _problem(
                    400,
                    request_id,
                    "INVALID_MULTIPART",
                    "request 文件部分的 Content-Type 必须是 application/json。",
                )
            raw_request = (await json_part.read(1024 * 1024 + 1)).decode("utf-8")
            if len(raw_request.encode("utf-8")) > 1024 * 1024:
                return _problem(
                    400,
                    request_id,
                    "INVALID_MULTIPART",
                    "request JSON 过大。",
                )
        elif isinstance(json_part, str):
            # 浏览器 FormData 直接追加字符串时不会携带每部分 Content-Type；
            # 兼容这种常见写法，但仍按 JSON 严格解析。
            raw_request = json_part
        else:
            return _problem(
                400,
                request_id,
                "INVALID_MULTIPART",
                "request 必须是 JSON 字符串或 application/json 文件部分。",
            )
        decoded = json.loads(raw_request)
        if not isinstance(decoded, dict):
            raise ValueError("request 顶层必须是对象")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _problem(
            400,
            request_id,
            "INVALID_MULTIPART",
            "request 不是合法的 UTF-8 JSON 对象。",
        )

    try:
        metadata_request = MetadataLabelRequest.model_validate(decoded)
    except ValidationError as exc:
        return _problem(
            422,
            request_id,
            "AIGC_SCHEMA_INVALID",
            "标注参数的字段、类型或枚举值不符合接口约定。",
            _validation_field_errors(exc),
        )

    try:
        created = service.create_job(
            filename=file_part.filename,
            data=data,
            request=metadata_request,
            idempotency_key=idempotency_key,
        )
    except MetadataJobRequestError as exc:
        return _problem(
            exc.status_code,
            request_id,
            exc.code,
            exc.message,
            exc.field_errors,
        )
    except Exception:
        return _problem(
            500,
            request_id,
            "INTERNAL_ERROR",
            "服务器未能安全接收任务，请根据 request_id 排查日志。",
        )

    payload = service.accepted_payload(created.record)
    return JSONResponse(
        status_code=202,
        content=payload,
        headers={"X-Request-ID": payload["request_id"]},
    )


@router.get(
    "/metadata-label-jobs/{job_id}",
    response_model=JobStatusResponse,
    responses={404: {"model": ApiErrorResponse}},
)
def get_metadata_label_job(
    job_id: str,
    service: MetadataLabelJobService = Depends(get_metadata_job_service),
):
    payload = service.get_job(job_id)
    if payload is None:
        request_id = new_request_id()
        return _problem(
            404,
            request_id,
            "JOB_NOT_FOUND",
            "任务不存在或结果已过期。",
        )
    return JSONResponse(
        content=payload,
        headers={"X-Request-ID": payload["request_id"]},
    )


@router.get(
    "/metadata-label-jobs/{job_id}/output",
    responses={
        404: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        500: {"model": ApiErrorResponse},
    },
)
def download_metadata_label_output(
    job_id: str,
    service: MetadataLabelJobService = Depends(get_metadata_job_service),
):
    record = service.download_record(job_id)
    if record is None:
        request_id = new_request_id()
        return _problem(
            404,
            request_id,
            "JOB_NOT_FOUND",
            "任务不存在或结果已过期。",
        )
    if record["status"] != "succeeded":
        return _problem(
            409,
            record["request_id"],
            "OUTPUT_NOT_READY",
            "只有 succeeded 状态的任务可以下载结果。",
        )
    output_path = Path(record["output_path"])
    try:
        output_path.resolve().relative_to(service.storage_root)
    except ValueError:
        return _problem(
            500,
            record["request_id"],
            "INTERNAL_ERROR",
            "结果文件引用无效。",
        )
    if not output_path.is_file():
        return _problem(
            500,
            record["request_id"],
            "INTERNAL_ERROR",
            "任务记录存在，但结果文件不可用。",
        )
    return FileResponse(
        path=output_path,
        media_type=record["output_mime_type"],
        filename=record["output_file_name"],
        headers={"X-Request-ID": record["request_id"]},
    )


@router.get("/health", response_model=HealthResponse)
def versioned_health(response: Response):
    request_id = new_request_id()
    response.headers["X-Request-ID"] = request_id
    try:
        ExifToolClient()
        image_available = True
    except ExifToolNotFoundError:
        image_available = False
    return {
        "status": "ok",
        "capabilities": {
            "image/jpeg": image_available,
            "image/png": image_available,
            "video/mp4": False,
        },
    }
