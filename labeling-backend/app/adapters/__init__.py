"""载体适配器：把文件容器差异封装在后端（开发手册 §4.5）。

图片与视频共用同一套 API 与业务操作；XMP 读写逻辑在 BaseAdapter 复用，
媒体完整性校验等容器差异在各子类实现。新增格式时注册到这里即可。
"""
from __future__ import annotations

from .base import AdapterError, BaseAdapter, MediaReport
from .video import Mp4Adapter

# 图片不在此注册：其读写由 app.metadata.image_adapter 承担（与合规检测同源，§14），
# 走 app/core/pipeline.py::_execute_image 分支。留在这里会形成第二套图片实现。
_ADAPTERS = {
    "video/mp4": Mp4Adapter,
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
           "Mp4Adapter"]
