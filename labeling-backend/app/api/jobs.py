"""任务接口（开发手册 §7.2 / §7.3 / §7.4）。

POST   /api/v1/metadata-label-jobs       创建标注任务（multipart: file + request）
GET    /api/v1/metadata-label-jobs/{id}  查询任务状态
GET    /api/v1/metadata-label-jobs/{id}/output  下载结果文件
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse

from ..adapters import AdapterError, get_adapter
from ..config import Settings
from ..core import aigc, jobs, util
from ..core.errors import (AIGC_CHARACTER_INVALID, AIGC_METADATA_EXISTS,
                           AIGC_SCHEMA_INVALID, FILE_TOO_LARGE, INTERNAL_ERROR,
                           INVALID_MULTIPART, JOB_NOT_FOUND,
                           MODALITY_MISMATCH, UNSUPPORTED_MEDIA_TYPE, ApiError)
from ..core.mimetype import detect_mime
from ..core.storage import FileStorage
from ..core.store import JobStore
from ..core.worker import JobWorker
from . import schema as api_schema
from .deps import get_settings, get_storage, get_store, get_worker

router = APIRouter(prefix="/api/v1", tags=["metadata-label-jobs"])


@router.post("/metadata-label-jobs", status_code=202)
async def create_job(
        req_request: Request,
        file: Annotated[UploadFile, File(description="JPEG、PNG 或 MP4")],
        request_json: Annotated[str, Form(alias="request", description="application/json 标注参数")],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        settings: Settings = Depends(get_settings),
        store: JobStore = Depends(get_store),
        storage: FileStorage = Depends(get_storage),
        worker: JobWorker = Depends(get_worker),
):
    request_id = req_request.state.request_id

    # 幂等（§7.2）：可选，防止重复提交创建两个任务
    if idempotency_key:
        existing = store.find_by_idempotency_key(idempotency_key)
        if existing:
            return api_schema.job_to_response(existing, request_id)

    # ---- 解析并校验 request（§9.1 第 2/7 步）----
    try:
        req = json.loads(request_json)
    except ValueError:
        raise ApiError(400, INVALID_MULTIPART, "request 字段不是合法 JSON。")
    if not isinstance(req, dict):
        raise ApiError(400, INVALID_MULTIPART, "request 必须是 JSON 对象。")

    if req.get("standard") != jobs.STANDARD:
        raise ApiError(422, AIGC_SCHEMA_INVALID, "standard 必须为 GB45438-2025。",
                       [{"field": "standard", "reason": "固定为 GB45438-2025"}])
    modality = req.get("modality")
    if modality not in (jobs.MODALITY_IMAGE, jobs.MODALITY_VIDEO):
        raise ApiError(422, AIGC_SCHEMA_INVALID, "modality 必须为 image 或 video。",
                       [{"field": "modality", "reason": "仅支持 image/video"}])
    policy = req.get("existing_metadata_policy", jobs.POLICY_REJECT)
    if policy not in jobs.POLICIES:
        raise ApiError(422, AIGC_SCHEMA_INVALID,
                       "existing_metadata_policy 必须为 reject 或 replace。",
                       [{"field": "existing_metadata_policy", "reason": "首期不支持 merge"}])

    schema_errors = aigc.validate_aigc(req.get("AIGC"))
    if schema_errors:
        has_char = any("不允许字符" in e["reason"] for e in schema_errors)
        raise ApiError(422,
                       AIGC_CHARACTER_INVALID if has_char else AIGC_SCHEMA_INVALID,
                       "AIGC 元数据不符合 GB 45438-2025 附录 E 结构。",
                       schema_errors)
    aigc_norm = aigc.normalize_first_write(req["AIGC"])

    # ProduceID 唯一性（§5.6）
    if store.find_produce_id_used(aigc_norm["ProduceID"]):
        raise ApiError(422, AIGC_SCHEMA_INVALID, "AIGC 元数据不符合 GB 45438-2025 附录 E 结构。",
                       [{"field": "AIGC.ProduceID", "reason": "该编号已在本系统使用过"}])

    # ---- 保存原文件，边写边算 SHA-256（§9.1 第 5/6 步）----
    head = await file.read(16)
    h = hashlib.sha256(head)
    size = len(head)
    stored = storage.new_stored_name("")
    path = storage.original_dir / stored
    try:
        with open(path, "wb") as f:
            f.write(head)
            while chunk := await file.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
    except OSError:
        storage.discard(path)
        raise ApiError(500, INTERNAL_ERROR, "保存上传文件失败。") from None

    # ---- 大小上限（§9.1 第 5 步 / §13）----
    if size > settings.storage.max_file_bytes:
        storage.discard(path)
        raise ApiError(413, FILE_TOO_LARGE,
                       f"文件超过大小上限（{settings.storage.max_file_bytes // (1024*1024)} MB）。")

    # ---- 按文件内容识别真实格式（§9.1 第 3 步）----
    mime = detect_mime(head)
    if mime is None:
        storage.discard(path)
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE, "无法识别的文件格式，仅支持 JPEG、PNG、MP4。")
    if not settings.capabilities.get(mime, False):
        storage.discard(path)
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE,
                       f"格式 {mime} 的适配器尚未实现。")

    # ---- 声明模态 vs 真实类型（§9.1 第 4 步）----
    real_modality = jobs.MODALITY_VIDEO if mime == "video/mp4" else jobs.MODALITY_IMAGE
    if modality != real_modality:
        storage.discard(path)
        raise ApiError(422, MODALITY_MISMATCH,
                       "声明模态与文件真实类型不一致，请重新选择模态。")

    # ---- 已有标识检查（§9.1 第 8 步 / §9.3）----
    adapter = get_adapter(mime, exiftool=settings.paths.exiftool,
                          ffprobe=settings.paths.ffprobe,
                          ffmpeg=settings.paths.ffmpeg,
                          exiftool_config=settings.paths.exiftool_config)
    try:
        records = adapter.detect_existing(path)
    except AdapterError as e:
        storage.discard(path)
        raise ApiError(500, INTERNAL_ERROR, f"元数据读取失败: {e}")
    if records and policy == jobs.POLICY_REJECT:
        storage.discard(path)
        raise ApiError(409, AIGC_METADATA_EXISTS,
                       "文件已存在 AIGC 隐式标识，且策略为拒绝。",
                       [{"field": "existing_metadata_policy",
                         "reason": f"在 {len(records)} 处标签中发现已有标识："
                                   f"{[r.tag_key for r in records]}"}])

    # ---- 创建任务并提交执行（§8.2）----
    job_id = "job_" + uuid.uuid4().hex
    created = util.now_iso()
    job = {
        "job_id": job_id,
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "status": jobs.QUEUED,
        "stage": jobs.STAGE_QUEUED,
        "progress": None,
        "created_at": created,
        "updated_at": created,
        "input": {
            "original_file_name": file.filename or "upload",
            "detected_mime_type": mime,
            "size_bytes": size,
            "sha256": h.hexdigest(),
        },
        "original_stored_name": stored,
        "existing_metadata_policy": policy,
        "submitted_aigc": aigc_norm,
        "audit": {
            "standard": req.get("standard"),
            "modality": modality,
            "existing_metadata_policy": policy,
            "original_file_name": file.filename or "upload",
            "detected_mime_type": mime,
            "size_bytes": size,
            "sha256": h.hexdigest(),
            "submitted_aigc": aigc_norm,
        },
    }
    store.create(job)
    # 202 响应必须反映"任务已接收、待执行"的创建时快照（§7.2 status=queued），
    # 不能等 Worker 启动后再查库——进程内 Worker 可能已把状态推进到 running。
    resp = api_schema.job_to_response(job, request_id)
    worker.submit(job_id)
    return resp


@router.get("/metadata-label-jobs/{job_id}")
async def get_job(job_id: str,
                  req_request: Request,
                  store: JobStore = Depends(get_store)):
    row = store.get(job_id)
    if row is None:
        raise ApiError(404, JOB_NOT_FOUND, "任务不存在或已过期。")
    return api_schema.job_to_response(row, req_request.state.request_id)


@router.get("/metadata-label-jobs/{job_id}/output")
async def download_output(job_id: str,
                          req_request: Request,
                          store: JobStore = Depends(get_store),
                          storage: FileStorage = Depends(get_storage)):
    row = store.get(job_id)
    if row is None:
        raise ApiError(404, JOB_NOT_FOUND, "任务不存在或已过期。")
    if row["status"] != jobs.SUCCEEDED or not row.get("output_stored_name"):
        raise ApiError(404, JOB_NOT_FOUND, "任务尚未成功完成，结果文件不可下载。")
    path = storage.output_path(row["output_stored_name"])
    if not path.is_file():
        raise ApiError(404, JOB_NOT_FOUND, "结果文件已过期或不存在。")
    return FileResponse(path, media_type=row["output_mime_type"],
                        filename=row["output_file_name"])
