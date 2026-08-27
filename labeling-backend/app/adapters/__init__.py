"""载体适配器：把文件容器差异封装在后端（开发手册 §4.5）。

图片与视频共用同一套 API 与业务操作；XMP 读写逻辑在 BaseAdapter 复用，
媒体完整性校验等容器差异在各子类实现。新增格式时注册到这里即可。
"""
from __future__ import annotations

from .base import AdapterError, BaseAdapter, MediaReport
from .video import Mp4Adapter
from .image import ImageAdapter  # 扩展点：图片适配器由队友实现

_ADAPTERS = {
    "video/mp4": Mp4Adapter,
    "image/jpeg": ImageAdapter,
    "image/png": ImageAdapter,
}


def get_adapter(mime: str, exiftool: str = "exiftool",
                ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg",
                exiftool_config: str | None = None) -> BaseAdapter:
    cls = _ADAPTERS.get(mime)
    if cls is None:
        raise AdapterError(f"不支持的媒体类型: {mime}")
    return cls(exiftool=exiftool, ffprobe=ffprobe, ffmpeg=ffmpeg,
               exiftool_config=exiftool_config)


__all__ = ["AdapterError", "BaseAdapter", "MediaReport", "get_adapter",
           "Mp4Adapter", "ImageAdapter"]
