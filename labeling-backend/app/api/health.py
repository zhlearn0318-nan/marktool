"""健康检查（开发手册 §7.5）。

返回能力列表，前端据此在适配器不可用时隐藏/禁用对应入口。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings
from .deps import get_settings

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)):
    return {"status": "ok", "capabilities": settings.capabilities}
