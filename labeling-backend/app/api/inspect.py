"""合规检测接口（合规方案 §8.3，样式对齐开发手册 §7 标注接口）。

POST /api/v1/compliance-inspect —— 只读：上传 MP4 → 同步返回 GB45438-2025
元数据隐式标识合规检测报告（conclusion / reason_code / repairability /
media_status / c2pa_presence …）。

与标注任务（/api/v1/metadata-label-jobs）分开成独立资源，防止"检测"意外改动
文件（§8.3）。检测用上传副本，报告返回后即删除副本，本接口无状态、不建任务；
审计存储（MetadataAuditStore / 登记库核对）属后续阶段，registry 暂按 skipped。
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ..adapters import AdapterError
from ..config import Settings
from ..core.errors import (FILE_TOO_LARGE, INTERNAL_ERROR,
                           UNSUPPORTED_MEDIA_TYPE, ApiError)
from ..core.inspector import MetadataComplianceInspector
from ..core.mimetype import detect_mime
from ..core.reader import ReaderError
from ..core.storage import FileStorage
from .deps import get_settings, get_storage

router = APIRouter(prefix="/api/v1", tags=["compliance-inspect"])


@router.post("/compliance-inspect", status_code=200)
async def inspect_video(
        req_request: Request,
        file: UploadFile = File(description="MP4 视频文件（只读检测，不改动原文件）"),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    request_id = req_request.state.request_id

    # ---- 头部探测真实类型（§9.1 第 3 步，不信任扩展名）----
    head = await file.read(16)
    mime = detect_mime(head)
    if mime is None:
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE,
                       "无法识别的文件格式，合规检测仅支持 MP4。")
    if mime != "video/mp4" or not settings.capabilities.get(mime, False):
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE,
                       f"合规检测暂仅支持 video/mp4（收到 {mime}）。")

    # ---- 保存上传副本，边写边算 SHA-256（§9.1 第 5/6 步）----
    stored = storage.new_stored_name(".mp4")
    path = storage.original_path(stored)
    h = hashlib.sha256(head)
    size = len(head)
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
                       f"文件超过大小上限（{settings.storage.max_file_bytes // (1024 * 1024)} MB）。")

    inspector = MetadataComplianceInspector(
        exiftool=settings.paths.exiftool, ffprobe=settings.paths.ffprobe,
        ffmpeg=settings.paths.ffmpeg, exiftool_config=settings.paths.exiftool_config)

    def _run() -> dict:
        return inspector.inspect(
            path, file_name=file.filename or "upload", size_bytes=size,
            sha256=h.hexdigest(), request_id=request_id)

    try:
        # 检测含 ffprobe/解码冒烟/ExifTool 子进程调用，放线程池避免阻塞事件循环
        return await run_in_threadpool(_run)
    except (ReaderError, AdapterError) as e:
        raise ApiError(500, INTERNAL_ERROR, f"合规检测执行失败: {e}") from None
    finally:
        storage.discard(path)      # 只读无状态：报告返回后不保留上传副本
