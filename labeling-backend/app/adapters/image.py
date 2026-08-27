"""图片（JPEG/PNG）元数据适配器 —— 扩展点。

按团队分工，图片适配器由队友负责（开发手册 §16.3 第 3 项）。
基类已实现统一的 XMP-aigc:AIGC 写入/移除逻辑，图片媒体完整性校验
（可解码、宽高不变，§6.3）在此补充即可。
当前未实现时，健康检查将返回 capabilities.image/* = false，前端据此禁用入口（§7.5）。
"""
from __future__ import annotations

from ..core.jobs import MODALITY_IMAGE
from .base import BaseAdapter


class ImageAdapter(BaseAdapter):
    carrier_id = "image-xmp-aigc-v1"
    modality = MODALITY_IMAGE
    supported_mimes = ("image/jpeg", "image/png")

    def media_integrity_check(self, src, dst, duration_tolerance: float = 0.1):
        raise NotImplementedError("图片适配器由队友实现，尚未接入")
