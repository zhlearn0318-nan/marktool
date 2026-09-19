"""图片（JPEG/PNG）元数据适配器（开发手册 §6.3）。

基类已实现格式无关的 XMP-aigc:AIGC 写入/移除（ExifTool 会自行按容器选择
落点：JPEG 写 APP1 标准 XMP，PNG 写 iTXt 文本块），本模块只补两处图片特有的
行为：

1. `media_integrity_check` —— §6.3 要求"不改变宽高、不主动重压像素、不删除
   与 AIGC 无关的原有元数据"，所以按"尺寸 + 像素数据哈希"比对写入前后。
2. `detect_existing` —— ExifTool 会把同一属性的多份物理副本折叠成一份，
   而 PNG 允许多份同名 XMP 文本块。这里叠加 image_reader 的逐份扫描，
   让检测器能看见"多份 / 冲突"。
"""
from __future__ import annotations

import hashlib

from ..core import image_reader
from ..core.mimetype import detect_mime
from ..core.reader import AIGCRecord
from .base import AdapterError, BaseAdapter, MediaReport

# 图片里 XMP 的实际落点文案（reader.carrier_location 的文案是 MP4 专用的）
_IMAGE_LOCATIONS = {
    "image/jpeg": "JPEG APP1 段（标准 XMP）",
    "image/png": "PNG 文本块（XML:com.adobe.xmp）",
}


def _content_mime(path: str) -> str | None:
    """按文件头判断真实格式（§9.1：不信任扩展名）。"""
    with open(path, "rb") as stream:
        return detect_mime(stream.read(32))


def _image_signature(path: str) -> dict:
    """图片媒体签名：格式、模式、尺寸，以及解码后像素数据的哈希。

    像素哈希是关键 —— 只看宽高无法发现"重新压缩过"，而那正是 §6.3 要禁止的。
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - 依赖缺失时给出明确指引
        raise AdapterError("缺少 Pillow 依赖，无法校验图片完整性") from None
    try:
        with Image.open(path) as im:
            im.load()
            return {
                "format": im.format,
                "mode": im.mode,
                "width": im.width,
                "height": im.height,
                "pixels_sha256": hashlib.sha256(im.tobytes()).hexdigest(),
            }
    except AdapterError:
        raise
    except Exception as exc:
        raise AdapterError(f"无法解码图片: {exc}") from None


class ImageAdapter(BaseAdapter):
    carrier_id = "image-xmp-aigc-v1"
    modality = "image"
    supported_mimes = ("image/jpeg", "image/png")

    # ---- §6.3 媒体完整性 ----
    def media_integrity_check(self, src, dst,
                              duration_tolerance: float = 0.1) -> MediaReport:
        before = _image_signature(str(src))
        after = _image_signature(str(dst))

        for key, label in (("width", "宽度"), ("height", "高度")):
            if before[key] != after[key]:
                return MediaReport(
                    passed=False,
                    reason=f"图片{label}发生变化: {before[key]} → {after[key]}",
                    before=before, after=after)
        if before["mode"] != after["mode"]:
            return MediaReport(
                passed=False,
                reason=f"图片色彩模式发生变化: {before['mode']} → {after['mode']}",
                before=before, after=after)
        if before["pixels_sha256"] != after["pixels_sha256"]:
            return MediaReport(
                passed=False,
                reason="像素数据发生变化（疑似重新编码或重压）",
                before=before, after=after)

        return MediaReport(passed=True, before=before, after=after)

    # ---- 已有标识检测：ExifTool 全标签 + 原始 XMP 包逐份扫描 ----
    def detect_existing(self, path) -> list[AIGCRecord]:
        path = str(path)
        mime = _content_mime(path)
        try:
            packet_records = image_reader.read_image_aigc_records(path, mime or "")
        except image_reader.ImageReaderError:
            # 容器损坏时不因此中断检测：退回纯 ExifTool 结果，
            # 损坏信息由 inspector 的媒体/结构探针负责呈现。
            packet_records = []

        exif_records = super().detect_existing(path)

        if not packet_records:
            return [self._relocate(r, mime) for r in exif_records]

        # 原始包扫描已逐份还原了 XMP 载体，故只从 ExifTool 结果里补充
        # 其他载体（EXIF UserComment、IPTC 等），避免同一份 XMP 被算两次。
        others = [r for r in exif_records if not r.tag_key.startswith("XMP")]
        return packet_records + [self._relocate(r, mime) for r in others]

    @staticmethod
    def _relocate(record: AIGCRecord, mime: str | None) -> AIGCRecord:
        """把 XMP 记录的位置文案改成图片语境。

        reader.carrier_location 的文案（"顶层 Adobe-XMP uuid 盒"）是 MP4 专用，
        直接用在图片上会误导诊断结论。
        """
        if not record.tag_key.startswith("XMP"):
            return record
        label = _IMAGE_LOCATIONS.get(mime or "")
        if not label:
            return record
        return AIGCRecord(tag_key=record.tag_key, raw=record.raw,
                          aigc=record.aigc, location=label)
