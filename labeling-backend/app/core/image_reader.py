"""图片（JPEG/PNG）原始 XMP 数据包扫描（开发手册 §6.3 / §9.3）。

为什么不能只靠 ExifTool 全标签扫描（reader.py）：
  - PNG 允许存在多份同名 `XML:com.adobe.xmp` 文本块，ExifTool 只回读一份；
  - Pillow 的 `image.info` 同样会把同名多份 iTXt 合并成一份。
检测器需要看见"多份 / 内容冲突"（DUPLICATE_RECORDS / FIELDS_DISAGREE），
所以这里按容器结构逐块扫描**原始** XMP 数据包，并逐份产出候选记录。

与 reader.py 的分工：本模块只负责"物理上到底有几份 XMP 包、每份里有哪些
AIGC 属性"；解析、校验与判定仍复用 aigc.py 与 inspector.py，避免两套规则
（手册 §14：写入器与检测器共用同一套读取校验能力）。
"""
from __future__ import annotations

import html
import re
import zlib
import xml.etree.ElementTree as ET

from .aigc import parse_aigc
from .reader import AIGCRecord

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_XMP_KEYWORD = b"XML:com.adobe.xmp"
_JPEG_XMP_APP1_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"
# 单块上限，防御性：损坏文件里长度字段可能是个天文数字
_MAX_CHUNK_BYTES = 64 * 1024 * 1024

# XMP 包本身损坏（XML 不合法）时的兜底：名称或命名空间含 aigc 的元素/属性。
# 与 aigc_check.py「值里出现 AIGC 就当作已有标识」的保守取向一致。
#
# 前置的 (?<![\w:.-]) 是必需的：命名空间声明 `xmlns:aigc="http://…/aigc#"`
# 里也含 "aigc"，没有这个断言会从 `xmlns:aigc` 中间开始匹配，把一个纯声明
# 误判成"又一份 AIGC 标识"（进而把干净文件报成多份/冲突）。
_FALLBACK_ELEMENT_RE = re.compile(
    r"<(?P<name>[\w.-]*aigc[\w:.-]*)[^>]*>(?P<value>.*?)</(?P=name)>",
    re.IGNORECASE | re.DOTALL,
)
_FALLBACK_ATTRIBUTE_RE = re.compile(
    r"(?<![\w:.-])(?P<name>[\w.-]*aigc[\w:.-]*)\s*=\s*(?P<quote>['\"])"
    r"(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


class ImageReaderError(Exception):
    pass


# ---- 容器层：把文件里所有 XMP 数据包原样取出来 ----

def _decode_png_text_chunk(chunk_type: bytes, payload: bytes) -> str | None:
    """解出 PNG 文本块里的 XMP；不是 XMP 关键字或解压失败时返回 None。

    三种文本块的关键字之后结构不同：
      tEXt           keyword\\0text
      zTXt           keyword\\0method\\0<zlib deflate>
      iTXt           keyword\\0compressed\\0method\\0lang\\0translated\\0text
    """
    try:
        keyword, separator, remainder = payload.partition(b"\x00")
        if not separator or keyword != _PNG_XMP_KEYWORD:
            return None
        if chunk_type == b"tEXt":
            encoded = remainder
        elif chunk_type == b"zTXt":
            if len(remainder) < 2 or remainder[0] != 0:
                return None
            encoded = zlib.decompress(remainder[1:])
        elif chunk_type == b"iTXt":
            if len(remainder) < 2:
                return None
            compressed, method = remainder[0], remainder[1]
            rest = remainder[2:]
            _, sep, rest = rest.partition(b"\x00")      # 语言标签
            if not sep:
                return None
            _, sep, text = rest.partition(b"\x00")      # 翻译关键字
            if not sep:
                return None
            if compressed:
                if method != 0:
                    return None
                text = zlib.decompress(text)
            encoded = text
        else:
            return None
        return encoded.decode("utf-8-sig", "replace")
    except (ValueError, zlib.error):
        return None


def scan_png_xmp_packets(path: str) -> list[str]:
    """逐块读取 PNG，返回全部 XMP 数据包（保持文件内出现顺序）。"""
    packets: list[str] = []
    with open(path, "rb") as stream:
        if stream.read(8) != _PNG_SIGNATURE:
            return packets
        while True:
            header = stream.read(8)
            if not header:
                break
            if len(header) != 8:
                raise ImageReaderError("PNG 数据块头不完整")
            length = int.from_bytes(header[:4], "big")
            chunk_type = header[4:]
            if length > _MAX_CHUNK_BYTES:
                raise ImageReaderError(f"PNG 数据块长度异常: {length}")
            if chunk_type in (b"iTXt", b"zTXt", b"tEXt"):
                payload = stream.read(length)
                if len(payload) != length:
                    raise ImageReaderError("PNG 文本数据块不完整")
                xmp = _decode_png_text_chunk(chunk_type, payload)
                if xmp:
                    packets.append(xmp)
            else:
                stream.seek(length, 1)
            if len(stream.read(4)) != 4:
                raise ImageReaderError("PNG 数据块校验信息不完整")
            if chunk_type == b"IEND":
                break
    return packets


def scan_jpeg_xmp_packets(path: str) -> list[str]:
    """遍历 JPEG 的 APP1 段，返回标准 XMP 数据包。

    只认标准 XMP 前缀；Adobe Extended XMP 是分段承载的另一套编码，本模块不
    组装，交由 inspector 显式识别并降级（避免误判成"无标识"）。
    """
    packets: list[str] = []
    with open(path, "rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            return packets
        while True:
            lead = stream.read(1)
            if not lead:
                return packets
            if lead != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker or marker in (b"\xd9", b"\xda"):
                return packets
            # 无长度字段的独立标记
            if b"\xd0" <= marker <= b"\xd8" or marker == b"\x01":
                continue
            raw_length = stream.read(2)
            if len(raw_length) != 2:
                raise ImageReaderError("JPEG 数据段长度不完整")
            length = int.from_bytes(raw_length, "big")
            if length < 2:
                raise ImageReaderError("JPEG 数据段长度无效")
            if length > _MAX_CHUNK_BYTES:
                raise ImageReaderError(f"JPEG 数据段长度异常: {length}")
            payload = stream.read(length - 2)
            if len(payload) != length - 2:
                raise ImageReaderError("JPEG 数据段不完整")
            if marker == b"\xe1" and payload.startswith(_JPEG_XMP_APP1_PREFIX):
                packets.append(
                    payload[len(_JPEG_XMP_APP1_PREFIX):].decode("utf-8-sig", "replace")
                )


# ---- 数据包层：取出名称/命名空间含 aigc 的属性值 ----

def _qualified_parts(name: str) -> tuple[str, str]:
    """把 `{namespace}local` 拆成两段；无命名空间前缀时返回 ("", name)。"""
    if name.startswith("{") and "}" in name:
        namespace, local = name[1:].split("}", 1)
        return namespace, local
    return "", name


def _is_aigc_name(name: str) -> bool:
    namespace, local = _qualified_parts(name)
    return "aigc" in namespace.lower() or "aigc" in local.lower()


def extract_aigc_values(packet: str) -> list[tuple[str, str]]:
    """从一份 XMP 数据包中取出所有 AIGC 相关属性，返回 (属性名, 值) 列表。

    兼容两种载体命名空间：团队基线 `http://example.com/aigc#` 与队友图片
    模块使用的 `http://aigc-compliance/ns/1.0/` —— 两者本地名都是 AIGC，
    这里按"名称含 aigc"匹配，不写死命名空间，避免漏读历史文件。
    """
    try:
        root = ET.fromstring(packet.strip("\x00﻿ \t\r\n"))
    except ET.ParseError:
        # XMP 损坏时按保守口径处理：能认出的 AIGC 项一律算"已存在"
        values = [
            (m.group("name"), html.unescape(m.group("value").strip()))
            for m in _FALLBACK_ELEMENT_RE.finditer(packet)
        ]
        values.extend(
            (m.group("name"), html.unescape(m.group("value").strip()))
            for m in _FALLBACK_ATTRIBUTE_RE.finditer(packet)
        )
        return values

    values: list[tuple[str, str]] = []
    for element in root.iter():
        if _is_aigc_name(element.tag):
            values.append((element.tag, "".join(element.itertext()).strip()))
        for attr_name, attr_value in element.attrib.items():
            if _is_aigc_name(attr_name):
                values.append((attr_name, attr_value.strip()))
    return values


# ---- 对外入口 ----

def _packets_for(path: str, mime: str) -> tuple[list[tuple[str, str]], list[str]]:
    """返回 (带位置标签的数据包列表, 警告列表)。"""
    if mime == "image/png":
        packets = scan_png_xmp_packets(path)
        label = "PNG 文本块（XML:com.adobe.xmp）"
    elif mime == "image/jpeg":
        packets = scan_jpeg_xmp_packets(path)
        label = "JPEG APP1 段（标准 XMP）"
    else:
        return [], []
    total = len(packets)
    # 只有多份时才标注序号，单份保持与视频侧一致的简洁文案
    located = [
        (f"{label}第 {i + 1} 份（共 {total} 份）" if total > 1 else label, p)
        for i, p in enumerate(packets)
    ]
    return located, []


def read_image_aigc_records(path: str, mime: str) -> list[AIGCRecord]:
    """扫描图片原始 XMP 包，返回全部 AIGC 候选记录。

    这是对 reader.read_aigc_records（ExifTool 全标签）的**补充**而非替代：
    ExifTool 会把同一逻辑属性的多份物理副本折叠成一份，本函数逐份还原，
    供检测器做多份/冲突判定。调用方负责与 ExifTool 结果合并去重。
    """
    located, _ = _packets_for(path, mime)
    records: list[AIGCRecord] = []
    for location, packet in located:
        for name, value in extract_aigc_values(packet):
            if not value:
                continue
            records.append(AIGCRecord(raw=value, tag_key=_tag_key(name),
                                      aigc=parse_aigc(value), location=location))
    return records


def _tag_key(name: str) -> str:
    """把 XMP 属性名归一到 reader.py 的 tag_key 风格（如 'XMP:AIGC'）。

    检测器与替换逻辑都按 `组:标签` 解析，这里统一成 XMP 组，保证
    "读到什么" 与 "删什么" 走同一套命名。
    """
    _, local = _qualified_parts(name)
    return f"XMP:{local}" if local else "XMP:AIGC"
