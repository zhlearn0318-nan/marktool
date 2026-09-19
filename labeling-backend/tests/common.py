"""测试共享常量与图片夹具构造。"""
from __future__ import annotations

import html
import struct
import zlib
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN_MP4 = FIXTURES / "sample_clean.mp4"
LEGACY_MP4 = FIXTURES / "sample_with_legacy_aigc.mp4"
EXIFTOOL_CONFIG = Path(__file__).parent.parent / "config" / "exiftool_aigc.config"

VALID_AIGC = {
    "Label": "1",
    "ContentProducer": "ORG_1565201000000016",
    "ProduceID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode1": "",
    "ContentPropagator": "ORG_1565201000000016",
    "PropagateID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode2": "",
}


# ---- 图片夹具：用 Pillow 现造，避免二进制样本入库 ----

def make_jpeg(path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    from PIL import Image
    Image.new("RGB", size, (180, 40, 40)).save(path, format="JPEG", quality=90)
    return path


def make_png(path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    from PIL import Image
    Image.new("RGB", size, (40, 120, 180)).save(path, format="PNG")
    return path


def itxt_chunk(xmp: str) -> bytes:
    """构造一个承载 XMP 的 PNG iTXt 数据块（未压缩）。"""
    payload = (b"XML:com.adobe.xmp\x00"     # 关键字 + 终止符
               b"\x00"                      # 压缩标志：未压缩
               b"\x00"                      # 压缩方法
               b"\x00"                      # 语言标签（空）
               b"\x00"                      # 翻译关键字（空）
               + xmp.encode("utf-8"))
    crc = zlib.crc32(b"iTXt" + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + b"iTXt" + payload + struct.pack(">I", crc)


def insert_before_iend(data: bytes, chunk: bytes) -> bytes:
    """把数据块插到 IEND 之前，保持 PNG 结构合法。"""
    at = data.rindex(b"\x00\x00\x00\x00IEND")
    return data[:at] + chunk + data[at:]


# 一份含 aigc:AIGC 属性的最小 XMP 包（命名空间用团队基线）
_XMP_TEMPLATE = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:aigc="http://example.com/aigc#" aigc:AIGC="{payload}"/>'
    '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)


def xmp_packet(payload: str) -> str:
    """把 AIGC JSON 文本嵌进一份最小 XMP 包。

    属性值里的引号必须实体转义成 `&quot;`。裸引号会产出**非法 XML**，
    测试就跑偏成在验"损坏包兜底"分支，而真实 exiftool 落盘正是转义形态。
    """
    return _XMP_TEMPLATE.format(payload=html.escape(payload, quote=True))
