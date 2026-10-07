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


# ---- 文档夹具：Markdown / PDF 现造，避免二进制样本入库 ----

def make_markdown(path: Path, body: str = "# 标题\n\n正文。\n",
                  frontmatter: str | None = None) -> Path:
    text = body if frontmatter is None else f"---\n{frontmatter}\n---\n{body}"
    path.write_text(text, encoding="utf-8")
    return path


def make_pdf(path: Path, pages: int = 2) -> Path:
    """生成一个**结构合法**的最小 PDF（正确 xref 偏移）。

    项目不依赖 PDF 库（见 app/core/pdfstruct.py 的说明），所以夹具也自己写：
    页数可控、xref 正确，ExifTool 能读出 PDF:PageCount，用于验证写入后
    页数与 %%EOF 是否存活（§9.4 媒体完整性）。
    """
    objs: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for i in range(pages):
        content = f"BT /F1 12 Tf 20 100 Td (Page {i + 1}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
                    f"/Contents {4 + i * 2} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % num + body + b"\nendobj\n"

    xref_at = len(out)
    n = len(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % n
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (n, xref_at)
    path.write_bytes(bytes(out))
    return path


def make_signed_pdf(path: Path, pages: int = 1) -> Path:
    """在合法 PDF 后追加一个含 /ByteRange 的增量更新段，模拟已签名文档。"""
    base = make_pdf(path, pages).read_bytes()
    prev = base.rindex(b"startxref")
    prev_off = int(base[prev + 9:].split(b"\n")[1])
    out = bytearray(base)
    off = len(out)
    out += (b"7 0 obj\n<< /Type /Sig /Filter /Adobe.PPKLite "
            b"/ByteRange [0 100 200 300] /Contents <00> >>\nendobj\n")
    xref_at = len(out)
    out += b"xref\n0 1\n0000000000 65535 f \n7 1\n%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size 8 /Root 1 0 R /Prev %d >>\nstartxref\n%d\n%%%%EOF\n"
            % (prev_off, xref_at))
    path.write_bytes(bytes(out))
    return path


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
