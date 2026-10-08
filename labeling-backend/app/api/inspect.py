"""合规检测接口（合规方案 §8.3，样式对齐开发手册 §7 标注接口）。

POST /api/v1/compliance-inspect —— 只读：上传 JPEG / PNG / MP4 → 同步返回
GB45438-2025 元数据隐式标识合规检测报告（conclusion / reason_code /
repairability / media_status / c2pa_presence …）。

按真实文件类型选择检测器（§4.5）：图片与视频产出**同构**报告，前端无需分叉。
与标注任务（/api/v1/metadata-label-jobs）分开成独立资源，防止"检测"意外改动
文件（§8.3）。检测用上传副本，报告返回后即删除副本，本接口无状态、不建任务。

报告里的 ``registry`` 块答的是"这个 ProduceID 本机见过吗"，三种模态接的是同一个
登记库；它**不**判断编号真伪，也不比对内容指纹——那是修复台 ``verify_source`` 的活。
"""
from __future__ import annotations

import hashlib
import time

from fastapi import APIRouter, Depends, File, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from ..adapters import AdapterError
from ..config import Settings
from ..core.errors import (FILE_TOO_LARGE, INTERNAL_ERROR,
                           UNSUPPORTED_MEDIA_TYPE, ApiError)
from ..core.image_bridge import build_inspector, resolve_registry_path, to_report
from ..core.inspector import MetadataComplianceInspector
from ..core.mimetype import SNIFF_BYTES, detect_mime, suffix_for_mime
from ..core.reader import ReaderError
from ..core.storage import FileStorage
from ..metadata.compliance import ComplianceInspectionError
from ..metadata.document_inspector import (DOCUMENT_MIMES,
                                           DocumentComplianceInspector,
                                           DocumentInspectError)
from ..metadata.identifier_registry import build_identifier_lookup
from .deps import get_settings, get_storage

router = APIRouter(prefix="/api/v1", tags=["compliance-inspect"])


@router.post("/compliance-inspect", status_code=200)
async def inspect_media(
        req_request: Request,
        file: UploadFile = File(description="JPEG / PNG / MP4 / Markdown / PDF / HTML / DOCX 文件"
                                            "（只读检测，不改动原文件）"),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    request_id = req_request.state.request_id

    # ---- 头部探测真实类型（§9.1 第 3 步，不信任扩展名）----
    head = await file.read(SNIFF_BYTES)
    mime = detect_mime(head, file.filename)
    if mime is None:
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE,
                       "无法识别的文件格式，合规检测支持 JPEG、PNG、MP4、"
                       "Markdown、PDF、HTML 与 DOCX。")
    if not settings.capabilities.get(mime, False):
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE,
                       f"合规检测不支持该格式（收到 {mime}）。")

    # ---- 保存上传副本，边写边算 SHA-256（§9.1 第 5/6 步）----
    stored = storage.new_stored_name(suffix_for_mime(mime))
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

    # §4.5：按真实文件类型选择检测器，三种模态产出同构报告。
    # 图片侧判定逻辑与图片写入侧同源（§14），复用 app.metadata.compliance；
    # 其富信息由 image_bridge 翻译为统一报告结构，前端无需为模态分叉。
    is_image = mime.startswith("image/")
    is_document = mime in DOCUMENT_MIMES
    # 登记库是模态无关的：图片侧的富结果由 image_bridge 翻译成统一报告，视频与
    # 文档侧则直接吃一个"这个编号见过吗"的回调。两边接的是同一个库，否则同一份
    # 编号在三种模态下会得到三种答案（§8.3）。
    identifier_lookup = build_identifier_lookup(resolve_registry_path(settings))
    if is_image:
        inspector = build_inspector(
            exiftool=settings.paths.exiftool,
            exiftool_config=settings.paths.exiftool_config,
            registry_path=resolve_registry_path(settings))
    elif is_document:
        inspector = DocumentComplianceInspector(
            mime, exiftool=settings.paths.exiftool,
            exiftool_config=settings.paths.exiftool_config)
    else:
        inspector = MetadataComplianceInspector(
            exiftool=settings.paths.exiftool, ffprobe=settings.paths.ffprobe,
            ffmpeg=settings.paths.ffmpeg, exiftool_config=settings.paths.exiftool_config)

    def _run() -> dict:
        if not is_image:
            return inspector.inspect(
                path, file_name=file.filename or "upload", size_bytes=size,
                sha256=h.hexdigest(), request_id=request_id,
                registry=identifier_lookup)
        started = time.monotonic()
        result = inspector.inspect(str(path))
        return to_report(
            str(path), result, file_name=file.filename or "upload",
            size_bytes=size, sha256=h.hexdigest(), request_id=request_id,
            elapsed_ms=int((time.monotonic() - started) * 1000))

    try:
        # 检测含 ffprobe/解码冒烟/ExifTool 子进程调用，放线程池避免阻塞事件循环
        return await run_in_threadpool(_run)
    except ComplianceInspectionError as e:
        # 图片侧对"非可解码的 JPEG/PNG"用 UNSUPPORTED_MEDIA_TYPE 表达
        status = 415 if e.code == "UNSUPPORTED_MEDIA_TYPE" else 500
        raise ApiError(status, e.code, str(e)) from None
    except DocumentInspectError as e:
        # 文档根本不是可读的文本/PDF（如非 UTF-8 的 .md）——文件本身的问题，
        # 与图片侧同一处理：415，不报成服务故障
        raise ApiError(415, UNSUPPORTED_MEDIA_TYPE, f"文件无法解析: {e}") from None
    except (ReaderError, AdapterError) as e:
        raise ApiError(500, INTERNAL_ERROR, f"合规检测执行失败: {e}") from None
    finally:
        storage.discard(path)      # 只读无状态：报告返回后不保留上传副本
