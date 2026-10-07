"""按文件内容识别格式（开发手册 §9.1 / §13：不能只信任扩展名或浏览器 MIME）。

二进制格式有魔数可认（JPEG/PNG/ISO-BMFF/PDF）；**纯文本格式没有魔数**，
Markdown 因此只能采用"扩展名作为必要前提 + 内容健全性校验"的组合判定——
绝不单凭扩展名放行（详见桌面文档 markdown-pdf-carrier-validation.md 的残留边界一节）。
"""
from __future__ import annotations

import codecs

# 上传时预读的字节数。JPEG/PNG/BMFF 只需前 16 字节，但 PDF 的 %PDF- 可能被
# 前导垃圾/增量更新推到稍后，文本格式也要足够样本才能做 UTF-8 健全性判断。
SNIFF_BYTES = 4096

# PDF 签名允许出现的最大偏移（规范要求首行，但容忍少量前导字节）
_PDF_SIGNATURE_WINDOW = 1024

_MARKDOWN_SUFFIXES = (".md", ".markdown")


def looks_like_utf8_text(sample: bytes) -> bool:
    """样本是否像 UTF-8 文本：无 NUL 字节，且能按 UTF-8 增量解码。

    不带 ``final=True`` 解码——调用方只拿到前若干字节，样本末尾正好切断一个
    多字节字符是正常现象，不能因此判为二进制。该 helper 供 Markdown 与本轮
    其他文本格式（HTML 等）共用。
    """
    if b"\x00" in sample:
        return False
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(sample)
    except UnicodeDecodeError:
        return False
    return True


def _suffix_of(filename: str | None) -> str:
    if not filename:
        return ""
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def detect_mime(head: bytes, filename: str | None = None) -> str | None:
    """读取文件头若干字节，返回 MIME 类型；无法识别返回 None。

    ``filename`` 仅供**无魔数格式**（Markdown）作为必要前提参与判定；二进制
    格式一律只看内容，保证"改扩展名"骗不过格式识别（§13）。
    """
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # ISO BMFF：偏移 4 处为 'ftyp'
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/mp4"
    # PDF：以 %PDF- 开头；容忍前导垃圾（增量更新/传输噪声）
    if head[:_PDF_SIGNATURE_WINDOW].find(b"%PDF-") >= 0:
        return "application/pdf"
    # Markdown：无魔数 —— 扩展名必要前提 + UTF-8 文本健全性
    if _suffix_of(filename) in _MARKDOWN_SUFFIXES and looks_like_utf8_text(head):
        return "text/markdown"
    return None


SUPPORTED_MIMES = ("image/jpeg", "image/png", "video/mp4",
                   "text/markdown", "application/pdf")

# §12.2 文件命名：结果文件名沿用原格式后缀，各模态共用同一条流水线
SUFFIX_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "video/mp4": ".mp4",
    "text/markdown": ".md",
    "application/pdf": ".pdf",
}


def suffix_for_mime(mime: str) -> str:
    """按真实 MIME 取结果文件后缀。

    取不到时返回 `.bin`：宁可用中性后缀，也不要给图片错标成 `.mp4`
    （下游按扩展名打开会直接失败）。
    """
    return SUFFIX_BY_MIME.get(mime, ".bin")
