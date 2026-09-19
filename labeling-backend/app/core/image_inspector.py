"""图片（JPEG/PNG）合规检测（开发手册 §6.3 / §9.4）。

与 MP4 检测器**同构**：复用同一套候选结构判定（`structural_issues`）、多份/冲突
判定（`_classify` / `_classify_many`）、问题码与报告组装（`_build`）。只替换三处
模态专有的探测：

  - 载体读取：走 `ImageAdapter.detect_existing`（ExifTool 全标签 + 原始 XMP 包逐份
    扫描），因此 PNG 里被 ExifTool 折叠掉的多份 XMP 文本块能参与多份/冲突判定；
  - 媒体状态：MP4 用 ffprobe + 解码冒烟；图片用 Pillow 解码 + 尺寸/像素哈希；
  - 结构探针：MP4 用 BMFF box 树；图片无对应概念，报告里标注 `applicable: false`。

结论取值、问题码、报告字段与 MP4 完全一致 —— 前端不需要为图片分叉渲染。
"""
from __future__ import annotations

import hashlib
import time
import zlib
from pathlib import Path
from typing import Callable

from ..adapters.image import ImageAdapter
from . import image_reader
from .image_reader import ImageReaderError
from .inspector import (C2PA_ABSENT, C2PA_INDETERMINATE, C2PA_PRESENT,
                        CONFIDENCE_LOW, CONCLUSION_INDETERMINATE,
                        CONCLUSION_NOT_FOUND, Issue, MEDIA_OK, MEDIA_UNREADABLE,
                        MetadataComplianceInspector, REPAIR_HUMAN,
                        _candidate_entry)
from .mimetype import detect_mime

IMAGE_DETECTOR_VERSION = "image-compliance-inspector/0.1.0"

# 图片没有 BMFF box 树。保留同名字段并给出 null + applicable=false，
# 前端据此隐藏该区块，而不必判断字段是否存在。
_BMFF_NOT_APPLICABLE = {
    "applicable": False,
    "note": "BMFF box 结构探针仅适用于 MP4；图片的结构可靠性由解码探测体现",
    "has_ftyp": None, "has_moov": None, "has_mdat": None,
    "truncated": None, "error": None, "c2pa_uuid": [],
}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_EXTENDED_XMP_PREFIX = b"http://ns.adobe.com/xmp/extension/\x00"
# C2PA 的 JUMBF 超盒 UUID（ISO/IEC 19566-5 中 c2pa 的标识）
_C2PA_JUMBF_UUID = bytes.fromhex("6332706100110010800000aa00389b71")
_MAX_SEGMENT_BYTES = 64 * 1024 * 1024


class ImageComplianceInspector(MetadataComplianceInspector):
    """JPEG/PNG 只读合规检测。文件内容问题一律落成结论，不抛异常。"""

    detector_version = IMAGE_DETECTOR_VERSION

    def __init__(self, exiftool: str = "exiftool", ffprobe: str = "ffprobe",
                 ffmpeg: str = "ffmpeg", exiftool_config: str | None = None):
        super().__init__(exiftool=exiftool, ffprobe=ffprobe, ffmpeg=ffmpeg,
                         exiftool_config=exiftool_config)
        self._adapter = ImageAdapter(exiftool=exiftool, ffprobe=ffprobe,
                                     ffmpeg=ffmpeg, exiftool_config=exiftool_config)

    # ---- 对外入口 ----
    def inspect(self, path: str | Path, *, file_name: str | None = None,
                size_bytes: int | None = None, sha256: str | None = None,
                request_id: str | None = None,
                registry: Callable[[str], bool] | None = None) -> dict:
        path = Path(path)
        started = time.monotonic()
        mime = self._detect_mime(path)

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        media = self._probe_image_media(path, mime)
        c2pa = self._c2pa_presence_image(path, mime)

        # 图片无法解码 → 载体整体不可靠，"没有标识"不是确定结论（§2.4）。
        # 与 MP4 缺 moov 的处理同构：升级 indeterminate，交人工复核。
        if media["media_status"] == MEDIA_UNREADABLE:
            return self._build(
                None, media, candidates=[], issues=[
                    Issue("UNREADABLE_CARRIER", "error",
                          "图片无法解码（结构损坏或数据截断），元数据读取结果不可作为"
                          "\"无标识\"依据")],
                conclusion=CONCLUSION_INDETERMINATE, reason_code="UNREADABLE_CARRIER",
                repairability=REPAIR_HUMAN, c2pa=c2pa, confidence=CONFIDENCE_LOW,
                registry=self._registry_block(registry, None),
                file_name=file_name, size_bytes=size_bytes, sha256=sha256,
                request_id=request_id, elapsed_ms=elapsed(),
                detected_mime=mime, bmff_block=_BMFF_NOT_APPLICABLE)

        records = self._adapter.detect_existing(path)
        candidates = [_candidate_entry(r) for r in records]
        classified = self._classify(records)

        # 标准 XMP 读不到、但存在本模块不组装的分段载体（Adobe Extended XMP）
        # → 藏有标识的可能性无法排除，不得当"无标识"定论（§2.4）。
        if classified["conclusion"] == CONCLUSION_NOT_FOUND:
            blocked = self._unreadable_carrier_issue(path, mime)
            if blocked is not None:
                return self._build(
                    None, media, candidates=[], issues=[blocked],
                    conclusion=CONCLUSION_INDETERMINATE,
                    reason_code=blocked.code, repairability=REPAIR_HUMAN,
                    c2pa=c2pa, confidence=CONFIDENCE_LOW,
                    registry=self._registry_block(registry, None),
                    file_name=file_name, size_bytes=size_bytes, sha256=sha256,
                    request_id=request_id, elapsed_ms=elapsed(),
                    detected_mime=mime, bmff_block=_BMFF_NOT_APPLICABLE)

        parsed_single = next((r.aigc for r in records
                              if len(records) == 1 and r.aigc is not None), None)
        produce_id = parsed_single.get("ProduceID") if parsed_single else None

        return self._build(
            None, media, candidates=candidates, issues=classified["issues"],
            conclusion=classified["conclusion"], reason_code=classified["reason_code"],
            repairability=classified["repairability"], c2pa=c2pa,
            confidence=classified["confidence"],
            registry=self._registry_block(registry, produce_id),
            file_name=file_name, size_bytes=size_bytes, sha256=sha256,
            request_id=request_id, elapsed_ms=elapsed(),
            detected_mime=mime, bmff_block=_BMFF_NOT_APPLICABLE)

    # ---- 模态专有探测 ----
    @staticmethod
    def _detect_mime(path: Path) -> str:
        """按文件头判断真实格式（§9.1：不信任扩展名）。"""
        with open(path, "rb") as stream:
            mime = detect_mime(stream.read(32))
        return mime or "application/octet-stream"

    @staticmethod
    def _probe_image_media(path: Path, mime: str) -> dict:
        """媒体状态：能否解码、格式、尺寸与像素数据哈希（§6.3 的完整性基准）。"""
        media: dict = {
            "media_status": None, "format_name": None, "mode": None,
            "width": None, "height": None, "pixels_sha256": None,
            "probe_error": None, "note": None,
        }
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - 依赖缺失时给出明确结论
            media.update({"media_status": MEDIA_UNREADABLE,
                          "probe_error": "缺少 Pillow 依赖"})
            return media
        try:
            with Image.open(path) as im:
                im.load()
                media.update({
                    "format_name": im.format, "mode": im.mode,
                    "width": im.width, "height": im.height,
                    "pixels_sha256": hashlib.sha256(im.tobytes()).hexdigest(),
                })
        except Exception as exc:
            media.update({"media_status": MEDIA_UNREADABLE,
                          "probe_error": str(exc)[:200]})
            return media
        media["media_status"] = MEDIA_OK
        return media

    @staticmethod
    def _unreadable_carrier_issue(path: Path, mime: str) -> Issue | None:
        """标准 XMP 读不到时，判断是否存在"读不完整"的载体。

        目前只有 JPEG 的 Adobe Extended XMP：它是分段承载的另一套编码，本模块
        不组装。若文件里存在该分段，则不能断言"没有标识"。
        """
        if mime != "image/jpeg":
            return None
        try:
            if not _has_extended_xmp(path):
                return None
        except (OSError, ImageReaderError):
            return Issue("UNREADABLE_CARRIER", "error",
                         "JPEG 数据段结构异常，无法确认是否存有标识")
        return Issue(
            "UNREADABLE_CARRIER", "error",
            "检测到 Adobe Extended XMP 分段载体，本服务不组装该编码，"
            "无法确认其中是否含 AIGC 标识，需人工复核（不得按\"无标识\"定论）")

    @staticmethod
    def _c2pa_presence_image(path: Path, mime: str) -> str:
        """§7 C2PA 存在性：只判存在，不验签。"""
        try:
            if mime == "image/png":
                return _png_c2pa_presence(path)
            if mime == "image/jpeg":
                return _jpeg_c2pa_presence(path)
        except (OSError, ImageReaderError, zlib.error):
            return C2PA_INDETERMINATE
        return C2PA_INDETERMINATE


# ---- 容器级探测工具 ----

def _has_extended_xmp(path: Path) -> bool:
    """JPEG 是否含 Adobe Extended XMP APP1 分段。"""
    with open(path, "rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            return False
        while True:
            lead = stream.read(1)
            if not lead:
                return False
            if lead != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker or marker in (b"\xd9", b"\xda"):
                return False
            if b"\xd0" <= marker <= b"\xd8" or marker == b"\x01":
                continue
            raw = stream.read(2)
            if len(raw) != 2:
                raise ImageReaderError("JPEG 数据段长度不完整")
            length = int.from_bytes(raw, "big")
            if length < 2 or length > _MAX_SEGMENT_BYTES:
                raise ImageReaderError("JPEG 数据段长度异常")
            payload = stream.read(length - 2)
            if len(payload) != length - 2:
                raise ImageReaderError("JPEG 数据段不完整")
            if marker == b"\xe1" and payload.startswith(_JPEG_EXTENDED_XMP_PREFIX):
                return True


def _jpeg_c2pa_presence(path: Path) -> str:
    """JPEG 的 C2PA 存在性：APP11(JUMBF) 段内含 c2pa 超盒 UUID。"""
    present = False
    with open(path, "rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            return C2PA_INDETERMINATE
        while True:
            lead = stream.read(1)
            if not lead:
                return C2PA_PRESENT if present else C2PA_ABSENT
            if lead != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker or marker in (b"\xd9", b"\xda"):
                return C2PA_PRESENT if present else C2PA_ABSENT
            if b"\xd0" <= marker <= b"\xd8" or marker == b"\x01":
                continue
            raw = stream.read(2)
            if len(raw) != 2:
                raise ImageReaderError("JPEG 数据段长度不完整")
            length = int.from_bytes(raw, "big")
            if length < 2 or length > _MAX_SEGMENT_BYTES:
                raise ImageReaderError("JPEG 数据段长度异常")
            payload = stream.read(length - 2)
            if len(payload) != length - 2:
                raise ImageReaderError("JPEG 数据段不完整")
            if marker == b"\xeb" and _C2PA_JUMBF_UUID in payload:
                present = True


def _png_c2pa_presence(path: Path) -> str:
    """PNG 的 C2PA 存在性：`caBX` 数据块（C2PA 规范定义的 PNG 承载位）。"""
    present = False
    with open(path, "rb") as stream:
        if stream.read(8) != _PNG_SIGNATURE:
            return C2PA_INDETERMINATE
        while True:
            header = stream.read(8)
            if not header:
                return C2PA_PRESENT if present else C2PA_ABSENT
            if len(header) != 8:
                raise ImageReaderError("PNG 数据块头不完整")
            length = int.from_bytes(header[:4], "big")
            chunk_type = header[4:]
            if length > _MAX_SEGMENT_BYTES:
                raise ImageReaderError("PNG 数据块长度异常")
            if chunk_type == b"caBX":
                present = True
            stream.seek(length, 1)
            if len(stream.read(4)) != 4:
                raise ImageReaderError("PNG 数据块校验信息不完整")
            if chunk_type == b"IEND":
                return C2PA_PRESENT if present else C2PA_ABSENT
