import html
import json
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from typing import Any, Optional

from PIL import Image

_XMP_APP1_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_XMP_KEYWORD = b"XML:com.adobe.xmp"
_FALLBACK_ELEMENT_RE = re.compile(
    r"<(?P<name>[\w.-]*aigc[\w:.-]*|aigc:[\w.-]+)[^>]*>"
    r"(?P<value>.*?)</(?P=name)>",
    re.IGNORECASE | re.DOTALL,
)
_FALLBACK_ATTRIBUTE_RE = re.compile(
    r"(?P<name>[\w.-]*aigc[\w:.-]*|aigc:[\w.-]+)\s*=\s*"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class AIGCRecord:
    """从一个 XMP 属性中读取到的 AIGC 记录。"""

    raw_value: str
    document: Any = None
    aigc: Optional[dict] = None
    parse_error: Optional[str] = None


def _decode_xmp(value: object) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8-sig", "replace")
    return None


def raw_xmp_packets(image: Image.Image) -> list[str]:
    """读取 Pillow 可见的标准 XMP 数据包，兼容 PNG 与 JPEG。"""
    packets: list[str] = []

    # PNG: 标准 XMP 关键字通常位于 iTXt 的 XML:com.adobe.xmp。
    png_xmp = _decode_xmp(image.info.get("XML:com.adobe.xmp"))
    if png_xmp:
        packets.append(png_xmp)

    # JPEG: 标准 XMP 位于 APP1 段。
    for marker, content in getattr(image, "applist", None) or []:
        if marker == "APP1" and content.startswith(_XMP_APP1_PREFIX):
            packets.append(
                content[len(_XMP_APP1_PREFIX):].decode("utf-8-sig", "replace")
            )
    return packets


def _decode_png_text_chunk(chunk_type: bytes, payload: bytes) -> Optional[str]:
    try:
        keyword, separator, remainder = payload.partition(b"\x00")
        if not separator or keyword != _PNG_XMP_KEYWORD:
            return None
        if chunk_type == b"tEXt":
            encoded_xmp = remainder
        elif chunk_type == b"zTXt":
            if len(remainder) < 2 or remainder[0] != 0:
                return None
            encoded_xmp = zlib.decompress(remainder[1:])
        elif chunk_type == b"iTXt":
            if len(remainder) < 2:
                return None
            compressed, compression_method = remainder[0], remainder[1]
            remainder = remainder[2:]
            _, separator, remainder = remainder.partition(b"\x00")  # language tag
            if not separator:
                return None
            _, separator, encoded_xmp = remainder.partition(b"\x00")  # translated keyword
            if not separator:
                return None
            if compressed:
                if compression_method != 0:
                    return None
                encoded_xmp = zlib.decompress(encoded_xmp)
        else:
            return None
        return encoded_xmp.decode("utf-8-sig", "replace")
    except (ValueError, zlib.error):
        return None


def _png_xmp_packets(file_path: str) -> list[str]:
    """逐块读取 PNG，避免 image.info 把同名的多份 iTXt 合并成一份。"""
    packets: list[str] = []
    with open(file_path, "rb") as stream:
        if stream.read(8) != _PNG_SIGNATURE:
            return packets
        while True:
            header = stream.read(8)
            if not header:
                break
            if len(header) != 8:
                raise OSError("PNG 数据块头不完整")
            length = int.from_bytes(header[:4], "big")
            chunk_type = header[4:]
            if chunk_type in {b"iTXt", b"zTXt", b"tEXt"}:
                payload = stream.read(length)
                if len(payload) != length:
                    raise OSError("PNG 文本数据块不完整")
                xmp = _decode_png_text_chunk(chunk_type, payload)
                if xmp:
                    packets.append(xmp)
            else:
                stream.seek(length, 1)
            if len(stream.read(4)) != 4:
                raise OSError("PNG 数据块校验信息不完整")
            if chunk_type == b"IEND":
                break
    return packets


def _qualified_name_parts(name: str) -> tuple[str, str]:
    if name.startswith("{") and "}" in name:
        namespace, local_name = name[1:].split("}", 1)
        return namespace, local_name
    return "", name


def _is_aigc_property(name: str) -> bool:
    namespace, local_name = _qualified_name_parts(name)
    return "aigc" in namespace.lower() or "aigc" in local_name.lower()


def _candidate_values(raw_xmp: str) -> list[str]:
    """找出名称、完整属性名或命名空间中含 AIGC 的 XMP 值。"""
    try:
        root = ET.fromstring(raw_xmp.strip("\x00\ufeff \t\r\n"))
    except ET.ParseError:
        # XMP 本身损坏时仍要把可识别的 AIGC 项视为“已存在”，防止继续追加。
        values = [
            html.unescape(match.group("value").strip())
            for match in _FALLBACK_ELEMENT_RE.finditer(raw_xmp)
        ]
        values.extend(
            html.unescape(match.group("value").strip())
            for match in _FALLBACK_ATTRIBUTE_RE.finditer(raw_xmp)
        )
        return values

    values: list[str] = []
    for element in root.iter():
        if _is_aigc_property(element.tag):
            values.append("".join(element.itertext()).strip())
        for attribute_name, attribute_value in element.attrib.items():
            if _is_aigc_property(attribute_name):
                values.append(attribute_value.strip())
    return values


def _parse_record(raw_value: str) -> AIGCRecord:
    try:
        document = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError) as exc:
        message = exc.msg if hasattr(exc, "msg") else str(exc)
        return AIGCRecord(
            raw_value=raw_value,
            parse_error=f"AIGC 元数据不是合法 JSON: {message}",
        )

    if isinstance(document, dict) and isinstance(document.get("AIGC"), dict):
        return AIGCRecord(
            raw_value=raw_value,
            document=document,
            aigc=document["AIGC"],
        )

    # 兼容读取旧版“只存七字段内层对象”的记录；严格 Schema 会把它判为不合规。
    legacy_aigc = document if isinstance(document, dict) else None
    return AIGCRecord(raw_value=raw_value, document=document, aigc=legacy_aigc)


def extract_aigc_records(image: Image.Image) -> list[AIGCRecord]:
    records: list[AIGCRecord] = []
    for packet in raw_xmp_packets(image):
        records.extend(_parse_record(value) for value in _candidate_values(packet))
    return records


def read_aigc_records(file_path: str) -> list[AIGCRecord]:
    """从图片文件读取全部可识别的 AIGC XMP 记录。"""
    with Image.open(file_path) as image:
        image.load()
        if (image.format or "").upper() == "PNG":
            packets = _png_xmp_packets(file_path)
            records: list[AIGCRecord] = []
            for packet in packets:
                records.extend(_parse_record(value) for value in _candidate_values(packet))
            return records
        return extract_aigc_records(image)


def extract_aigc_json(image: Image.Image) -> Optional[dict]:
    """兼容旧调用：无记录返回 None，损坏记录返回 {}，否则返回第一份内层对象。"""
    records = extract_aigc_records(image)
    if not records:
        return None
    return records[0].aigc or {}
