"""多模态与场景标识接口。

单文件打标仍走 ``/api/v1/metadata-label-jobs``。这里处理一次操作要产出
多个内容文件的情况，但写入、解析、校验都调用既有适配器，不另做一套元数据格式。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..config import Settings
from ..core.errors import (AIGC_SCHEMA_INVALID, FILE_TOO_LARGE, INTERNAL_ERROR,
                           INVALID_MULTIPART, UNSUPPORTED_MEDIA_TYPE, ApiError)
from ..core.storage import FileStorage
from ..labeling_error import LabelingError
from ..multimodal.service import label_multimodal, parse_multimodal_path, verify_multimodal_path
from ..scenes.interactive_scene import (
    label_interactive_scene, parse_interactive_scene, verify_interactive_scene,
)
from ..scenes.virtual_scene import label_virtual_scene, parse_virtual_scene, verify_virtual_scene
from ..tools import tools_from_settings
from .deps import get_settings, get_storage

router = APIRouter(prefix="/api/v1", tags=["multimodal-scenes"])

_STATUS = {
    "AIGC_SCHEMA_INVALID": 422,
    "AIGC_CHARACTER_INVALID": 422,
    "AIGC_INITIAL_RELATION_INVALID": 422,
    "AIGC_IDENTIFIER_DUPLICATE": 422,
    "EXPLICIT_LABEL_INVALID": 422,
    "EXPLICIT_LAYOUT_FAILED": 422,
    "NOT_MULTIMODAL": 422,
    "AMBIGUOUS_INPUT": 400,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "TEXT_MARKER_CONFLICT": 422,
    "AIGC_METADATA_EXISTS": 409,
    "AIGC_DUPLICATE_RECORDS": 409,
    "FILE_TOO_LARGE": 413,
    "MEDIA_TOOL_FAILED": 500,
    "METADATA_WRITE_FAILED": 500,
    "METADATA_READBACK_FAILED": 500,
    "VERIFY_FAILED": 500,
}


def _http_error(request_id: str, exc: LabelingError) -> ApiError:
    status = _STATUS.get(exc.code, 500)
    code = exc.code if exc.code in _STATUS else INTERNAL_ERROR
    return ApiError(status, code, exc.message, exc.details if isinstance(exc.details, list) else None)


async def _save_upload(upload: UploadFile | None, dest: Path, limit: int) -> Path | None:
    if upload is None or not upload.filename:
        return None
    size = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        while chunk := await upload.read(1 << 20):
            size += len(chunk)
            if size > limit:
                dest.unlink(missing_ok=True)
                raise ApiError(413, FILE_TOO_LARGE, "文件超过大小上限。")
            handle.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        return None
    return dest


def _load_request(raw: str) -> dict:
    import json
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, INVALID_MULTIPART, "request 字段不是合法 JSON。") from exc
    if not isinstance(payload, dict):
        raise ApiError(400, INVALID_MULTIPART, "request 必须是 JSON 对象。")
    if payload.get("standard") not in (None, "GB45438-2025"):
        raise ApiError(422, AIGC_SCHEMA_INVALID, "standard 必须为 GB45438-2025。")
    payload["standard"] = "GB45438-2025"
    return payload


def _publish(storage: FileStorage, package: Path, prefix: str) -> tuple[str, str]:
    bundle_id = prefix + uuid.uuid4().hex
    stored = storage.output_path(bundle_id + ".zip")
    shutil.copyfile(package, stored)
    return bundle_id, f"/api/v1/composite-bundles/{bundle_id}"


def _bundle_path(storage: FileStorage, bundle_id: str) -> Path:
    if "/" in bundle_id or "\\" in bundle_id or ".." in bundle_id:
        raise ApiError(404, "BUNDLE_NOT_FOUND", "找不到结果包。")
    path = storage.output_path(bundle_id + ".zip")
    if not path.is_file():
        raise ApiError(404, "BUNDLE_NOT_FOUND", "找不到结果包。")
    return path


@router.get("/composite-bundles/{bundle_id}")
async def download_bundle(bundle_id: str, storage: FileStorage = Depends(get_storage)):
    path = _bundle_path(storage, bundle_id)
    return FileResponse(path, media_type="application/zip", filename=f"{bundle_id}.zip")


@router.post("/multimodal/label")
async def multimodal_label(
        req: Request,
        request_json: Annotated[str, Form(alias="request")],
        file: Annotated[UploadFile | None, File()] = None,
        file_text: Annotated[UploadFile | None, File()] = None,
        file_image: Annotated[UploadFile | None, File()] = None,
        file_audio: Annotated[UploadFile | None, File()] = None,
        file_video: Annotated[UploadFile | None, File()] = None,
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    payload = _load_request(request_json)
    if "AIGC" not in payload:
        raise ApiError(422, AIGC_SCHEMA_INVALID, "request 缺少 AIGC。")
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)
    limit = settings.storage.max_file_bytes
    composite = await _save_upload(file, work / "input" / "composite.bin", limit)
    parts = {}
    mapping = {
        "text": file_text, "image": file_image, "audio": file_audio, "video": file_video,
    }
    for role, upload in mapping.items():
        saved = await _save_upload(upload, work / "input" / role, limit)
        if saved is not None:
            parts[role] = saved
    tools = tools_from_settings(settings)

    def _run():
        return label_multimodal(
            work / "out", payload["AIGC"],
            policy=payload.get("existing_metadata_policy", "reject"),
            tools=tools,
            composite_video=composite,
            parts=parts or None,
        )

    try:
        report = await run_in_threadpool(_run)
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    bundle_id, url = _publish(storage, Path(work / "out" / "bundle.zip"), "mm_")
    report["bundle_id"] = bundle_id
    report["download_url"] = url
    report["request_id"] = req.state.request_id
    return report


@router.post("/multimodal/parse")
async def multimodal_parse(
        req: Request,
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    saved = await _save_upload(file, work / "input.bin", settings.storage.max_file_bytes)
    if saved is None:
        raise ApiError(400, INVALID_MULTIPART, "缺少待解析文件。")
    try:
        parsed = await run_in_threadpool(parse_multimodal_path, saved, tools_from_settings(settings))
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    parsed["request_id"] = req.state.request_id
    return parsed


@router.post("/multimodal/verify")
async def multimodal_verify(
        req: Request,
        file: UploadFile = File(...),
        request_json: Annotated[str | None, Form(alias="request")] = None,
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    payload = _load_request(request_json) if request_json else {}
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    saved = await _save_upload(file, work / "input.bin", settings.storage.max_file_bytes)
    if saved is None:
        raise ApiError(400, INVALID_MULTIPART, "缺少待校验文件。")
    expected = payload.get("AIGC")
    try:
        report = await run_in_threadpool(
            verify_multimodal_path, saved, tools_from_settings(settings), expected)
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    report["request_id"] = req.state.request_id
    return report


@router.post("/scenes/virtual/label")
async def virtual_label(
        req: Request,
        request_json: Annotated[str, Form(alias="request")],
        file: Annotated[UploadFile | None, File()] = None,
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    payload = _load_request(request_json)
    if "AIGC" not in payload:
        raise ApiError(422, AIGC_SCHEMA_INVALID, "request 缺少 AIGC。")
    explicit = payload.get("explicit") or {}
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    source = await _save_upload(
        file, work / "input" / _safe_filename(file, "frame.bin"),
        settings.storage.max_file_bytes)
    frame_size = payload.get("frame_size") or [960, 540]
    if (not isinstance(frame_size, list) or len(frame_size) != 2
            or not all(isinstance(item, int) and item > 0 for item in frame_size)):
        raise ApiError(422, AIGC_SCHEMA_INVALID, "frame_size 必须是两个正整数。")
    tools = tools_from_settings(settings)

    def _run():
        return label_virtual_scene(
            work / "out", payload["AIGC"],
            text=explicit.get("text") or "人工智能生成合成",
            position=explicit.get("position") or "bottom_right",
            color=explicit.get("color") or "#FFFFFF",
            background=explicit.get("background") or "#102033",
            during_service=bool(explicit.get("during_service")),
            service_position=explicit.get("service_position"),
            source=source,
            frame_size=(int(frame_size[0]), int(frame_size[1])),
            policy=payload.get("existing_metadata_policy", "reject"),
            tools=tools,
        )

    try:
        report = await run_in_threadpool(_run)
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    bundle_id, url = _publish(storage, Path(work / "out" / "virtual-scene.zip"), "vs_")
    report["bundle_id"] = bundle_id
    report["download_url"] = url
    report["request_id"] = req.state.request_id
    return report


@router.post("/scenes/virtual/parse")
async def virtual_parse(
        req: Request,
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    saved = await _save_upload(file, work / "scene.zip", settings.storage.max_file_bytes)
    if saved is None:
        raise ApiError(400, INVALID_MULTIPART, "缺少虚拟场景包。")
    try:
        parsed = await run_in_threadpool(parse_virtual_scene, saved, tools_from_settings(settings))
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    parsed["request_id"] = req.state.request_id
    return parsed


@router.post("/scenes/virtual/verify")
async def virtual_verify(
        req: Request,
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    saved = await _save_upload(file, work / "scene.zip", settings.storage.max_file_bytes)
    if saved is None:
        raise ApiError(400, INVALID_MULTIPART, "缺少虚拟场景包。")
    try:
        report = await run_in_threadpool(verify_virtual_scene, saved, tools_from_settings(settings))
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    report["request_id"] = req.state.request_id
    return report


@router.post("/scenes/interactive/label")
async def interactive_label(
        req: Request,
        request_json: Annotated[str, Form(alias="request")],
        file: Annotated[UploadFile | None, File()] = None,
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    payload = _load_request(request_json)
    explicit = payload.get("explicit") or {}
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    source = await _save_upload(
        file, work / "input" / _safe_filename(file, "content.bin"),
        settings.storage.max_file_bytes,
    )
    tools = tools_from_settings(settings)

    def _run():
        return label_interactive_scene(
            work / "out",
            text=explicit.get("text") or "AI生成",
            placements=explicit.get("placements") or ["near_content"],
            persistent=explicit.get("persistent", True),
            color=explicit.get("color") or "#102033",
            background=explicit.get("background") or "#F4EBD4",
            content=source,
            content_role=payload.get("content_role"),
            aigc_obj=payload.get("AIGC"),
            policy=payload.get("existing_metadata_policy", "reject"),
            tools=tools,
        )

    try:
        report = await run_in_threadpool(_run)
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    bundle_id, url = _publish(storage, Path(work / "out" / "interactive-scene.zip"), "is_")
    report["bundle_id"] = bundle_id
    report["download_url"] = url
    report["request_id"] = req.state.request_id
    return report


@router.post("/scenes/interactive/parse")
async def interactive_parse(
        req: Request,
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    saved = await _save_upload(file, work / _safe_filename(file, "scene.bin"), settings.storage.max_file_bytes)
    if saved is None:
        raise ApiError(400, INVALID_MULTIPART, "缺少交互场景描述。")
    try:
        parsed = await run_in_threadpool(parse_interactive_scene, saved, tools_from_settings(settings))
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    parsed["request_id"] = req.state.request_id
    return parsed


@router.post("/scenes/interactive/verify")
async def interactive_verify(
        req: Request,
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        storage: FileStorage = Depends(get_storage),
):
    work = Path(storage.root) / "work" / uuid.uuid4().hex
    saved = await _save_upload(file, work / _safe_filename(file, "scene.bin"), settings.storage.max_file_bytes)
    if saved is None:
        raise ApiError(400, INVALID_MULTIPART, "缺少交互场景描述。")
    try:
        report = await run_in_threadpool(verify_interactive_scene, saved, tools_from_settings(settings))
    except LabelingError as exc:
        raise _http_error(req.state.request_id, exc) from None
    report["request_id"] = req.state.request_id
    return report


def _safe_filename(upload: UploadFile | None, fallback: str) -> str:
    if upload is None or not upload.filename:
        return fallback
    return Path(upload.filename).name or fallback
