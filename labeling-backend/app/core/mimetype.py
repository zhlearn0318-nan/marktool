"""按文件内容识别格式（开发手册 §9.1 / §13：不能只信任扩展名或浏览器 MIME）。"""
from __future__ import annotations


def detect_mime(head: bytes) -> str | None:
    """读取文件头若干字节，返回 MIME 类型；无法识别返回 None。"""
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # ISO BMFF：偏移 4 处为 'ftyp'
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/mp4"
    return None


SUPPORTED_MIMES = ("image/jpeg", "image/png", "video/mp4")
