"""外部工具路径。多模态、音频与场景模块共用，避免各处再读一遍配置。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolPaths:
    exiftool: str = "exiftool"
    ffprobe: str = "ffprobe"
    ffmpeg: str = "ffmpeg"
    exiftool_config: str | None = None
    registry_path: str | None = None
    font_path: str | None = None


def tools_from_settings(settings) -> ToolPaths:
    from .core.image_bridge import resolve_registry_path

    return ToolPaths(
        exiftool=settings.paths.exiftool,
        ffprobe=settings.paths.ffprobe,
        ffmpeg=settings.paths.ffmpeg,
        exiftool_config=settings.paths.exiftool_config,
        registry_path=resolve_registry_path(settings),
    )
