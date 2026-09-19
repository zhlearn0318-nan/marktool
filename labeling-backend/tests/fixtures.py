import html
import json

from PIL import Image, PngImagePlugin

AIGC_NS = "http://example.com/aigc#"

_XMP_TEMPLATE = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="" xmlns:aigc="%s">\n'
    "   <aigc:AIGC>%s</aigc:AIGC>\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)

VALID_AIGC = {
    "Label": "1",
    "ContentProducer": "ORG_1565201000000016",
    "ProduceID": "PRD-20260610-0001",
    "ReservedCode1": "",
    "ContentPropagator": "ORG_1565201000000016",
    "PropagateID": "PRD-20260610-0001",
    "ReservedCode2": "",
}

VALID_DOCUMENT = {"AIGC": VALID_AIGC}

UPDATED_AIGC = {
    **VALID_AIGC,
    "Label": "2",
    "ProduceID": "PRD-20260610-0002",
    "PropagateID": "PRD-20260610-0002",
}

UPDATED_DOCUMENT = {"AIGC": UPDATED_AIGC}

MISSING_FIELD_AIGC = {
    key: value for key, value in VALID_AIGC.items() if key != "ProduceID"
}


def _compact_document(aigc_dict: dict) -> str:
    return json.dumps({"AIGC": aigc_dict}, ensure_ascii=False, separators=(",", ":"))


def _xmp_for(aigc_dict: dict) -> str:
    return _XMP_TEMPLATE % (AIGC_NS, html.escape(_compact_document(aigc_dict)))


def legacy_xmp(aigc_dict: dict) -> str:
    """旧版仅保存内层对象，属性名为 aigc:metadata。"""
    body = html.escape(json.dumps(aigc_dict, ensure_ascii=False, separators=(",", ":")))
    return _XMP_TEMPLATE.replace("aigc:AIGC", "aigc:metadata") % (AIGC_NS, body)


def duplicate_xmp(first: dict, second: dict) -> str:
    first_value = html.escape(_compact_document(first))
    second_value = html.escape(_compact_document(second))
    first_element = f"<aigc:AIGC>{first_value}</aigc:AIGC>"
    second_element = f"<aigc:AIGC>{second_value}</aigc:AIGC>"
    return _XMP_TEMPLATE.replace(
        "<aigc:AIGC>%s</aigc:AIGC>",
        first_element + "\n   " + second_element,
    ) % (AIGC_NS,)


def make_png(path, aigc_dict=None, size=(400, 300), raw_xmp=None):
    """生成 PNG；可以注入标准或任意 XMP。"""
    image = Image.new("RGB", size, (180, 180, 180))
    xmp = raw_xmp if raw_xmp is not None else (_xmp_for(aigc_dict) if aigc_dict else None)
    if xmp is not None:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_itxt("XML:com.adobe.xmp", xmp)
        image.save(path, "PNG", pnginfo=metadata)
    else:
        image.save(path, "PNG")
    return str(path)


def make_png_with_xmp_packets(path, packets, size=(400, 300)):
    """生成含多个同名标准 XMP iTXt 块的 PNG，用于唯一性测试。"""
    image = Image.new("RGB", size, (180, 180, 180))
    metadata = PngImagePlugin.PngInfo()
    for packet in packets:
        metadata.add_itxt("XML:com.adobe.xmp", packet)
    image.save(path, "PNG", pnginfo=metadata)
    return str(path)


def make_jpeg(path, aigc_dict=None, size=(400, 300), raw_xmp=None):
    """生成 JPEG；Pillow 以标准 APP1 XMP 写入可选测试数据。"""
    image = Image.new("RGB", size, (180, 180, 180))
    xmp = raw_xmp if raw_xmp is not None else (_xmp_for(aigc_dict) if aigc_dict else None)
    save_options = {"quality": 90}
    if xmp is not None:
        save_options["xmp"] = xmp.encode("utf-8")
    image.save(path, "JPEG", **save_options)
    return str(path)


def broken_json_xmp() -> str:
    """合法 XMP 包裹但 JSON 本身损坏。"""
    return _XMP_TEMPLATE % (AIGC_NS, html.escape('{"AIGC":{"Label":"1",'))
