import io
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app import storage
from app.engine.base import DetectionContext
from app.engine.registry import build_default_registry
from app.report.builder import build_report
from app.schemas.models import DetectionResult

router = APIRouter(prefix="/api")
_registry = build_default_registry()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/detect")
async def detect(file: UploadFile = File(...),
                 target_regulation: str = Form("CN_GB45438")):
    data = await file.read()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="无法识别的图片文件")

    result_id, path = storage.save_upload(file.filename, data)
    ctx = DetectionContext(file_path=path, image=image,
                           target_regulation=target_regulation)
    items = _registry.run_all(ctx, modality="image")
    aigc = ctx.cache.get("aigc_metadata")
    report = build_report(items, aigc)
    result = DetectionResult(result_id=result_id, filename=file.filename or "image",
                             modality="image", items=items, aigc_metadata=aigc)
    payload = {
        "result_id": result_id,
        "detection": result.model_dump(),
        "report": report.model_dump(),
    }
    storage.store_result(result_id, payload)
    return payload


@router.get("/report/{result_id}")
def get_report(result_id: str):
    payload = storage.get_result(result_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="未找到该检测结果")
    return payload
