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
    # MP3 既可能以 ID3v2 标签开头，也可能直接以 MPEG 音频帧开头。
    # 只接受 ID3 后紧接有效 MPEG 帧的内容特征，扩展名不能参与判定。
    if _looks_like_mp3(head):
        return "audio/mpeg"
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


def _looks_like_mp3(head: bytes) -> bool:
    """识别 ID3v2 + MPEG frame 或裸 MPEG Layer 音频帧。"""
    offset = 0
    if len(head) >= 10 and head[:3] == b"ID3":
        # ID3v2 tag size 使用四字节 synchsafe integer，标签之后必须有音频帧。
        if head[3] not in (2, 3, 4) or any(byte & 0x80 for byte in head[6:10]):
            return False
        offset = 10 + sum((byte & 0x7f) << shift
                          for byte, shift in zip(head[6:10], (21, 14, 7, 0)))
        # ID3v2.4 的 footer 标志表示 tag 后还有 10 字节 footer；其不计入
        # header 的 synchsafe size，必须跳过才能落到真正的 MPEG 帧。
        if head[3] == 4 and (head[5] & 0x10):
            offset += 10
    if len(head) < offset + 4:
        return False
    b0, b1, b2 = head[offset:offset + 3]
    # MPEG audio sync + 合法 version/layer/bitrate/sample-rate 组合。
    return (b0 == 0xff and (b1 & 0xe0) == 0xe0 and (b1 & 0x18) != 0x08
            and (b1 & 0x06) != 0x00 and (b2 & 0xf0) not in (0x00, 0xf0)
            and (b2 & 0x0c) != 0x0c)


SUPPORTED_MIMES = ("image/jpeg", "image/png", "video/mp4", "audio/mp4", "audio/mpeg")

# §12.2 文件命名：结果文件名沿用原格式后缀，图片与视频共用同一条流水线
SUFFIX_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "video/mp4": ".mp4",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
}


def suffix_for_mime(mime: str) -> str:
    """按真实 MIME 取结果文件后缀。

    取不到时返回 `.bin`：宁可用中性后缀，也不要给图片错标成 `.mp4`
    （下游按扩展名打开会直接失败）。
    """
    return SUFFIX_BY_MIME.get(mime, ".bin")
