"""按文件内容识别格式（开发手册 §9.1 / §13：不能只信任扩展名或浏览器 MIME）。"""
from __future__ import annotations


def detect_mime(head: bytes) -> str | None:
    """读取文件头若干字节，返回 MIME 类型；无法识别返回 None。"""
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # ISO BMFF：偏移 4 处为 'ftyp'。M4A 的 major/compatible brand 是
    # 文件内容的一部分；扩展名只能由调用方作为显示辅助，不能参与此判断。
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brands = _bmff_brands(head)
        if b"M4A " in brands:
            return "audio/mp4"
        return "video/mp4"
    return None


def _bmff_brands(head: bytes) -> set[bytes]:
    """返回已读取 ftyp box 中的 major 与 compatible brands。"""
    brands = {head[8:12]}
    if len(head) < 16:
        return brands
    size = int.from_bytes(head[:4], "big")
    end = min(len(head), size) if size >= 16 else len(head)
    for offset in range(16, end - 3, 4):
        brands.add(head[offset:offset + 4])
    return brands


SUPPORTED_MIMES = ("image/jpeg", "image/png", "video/mp4", "audio/mp4")

# §12.2 文件命名：结果文件名沿用原格式后缀，图片与视频共用同一条流水线
SUFFIX_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "video/mp4": ".mp4",
    "audio/mp4": ".m4a",
}


def suffix_for_mime(mime: str) -> str:
    """按真实 MIME 取结果文件后缀。

    取不到时返回 `.bin`：宁可用中性后缀，也不要给图片错标成 `.mp4`
    （下游按扩展名打开会直接失败）。
    """
    return SUFFIX_BY_MIME.get(mime, ".bin")
