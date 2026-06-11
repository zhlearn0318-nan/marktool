import html
import json
from PIL import Image, PngImagePlugin

AIGC_NS = "http://aigc-compliance/ns/1.0/"

_XMP_TEMPLATE = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="" xmlns:aigc="%s">\n'
    "   <aigc:metadata>%s</aigc:metadata>\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>'
)

VALID_AIGC = {
    "Label": "1",
    "ContentProducer": "示例生成服务商",
    "ProduceID": "PRD-20260610-0001",
    "ContentPropagator": "示例传播平台",
    "PropagateID": "PRO-20260610-0009",
}

MISSING_FIELD_AIGC = {"Label": "1", "ContentProducer": "示例生成服务商"}  # 缺 ProduceID


def _xmp_for(aigc_dict: dict) -> str:
    body = html.escape(json.dumps(aigc_dict, ensure_ascii=False))
    return _XMP_TEMPLATE % (AIGC_NS, body)


def make_png(path, aigc_dict=None, size=(400, 300), raw_xmp=None):
    """生成 PNG。aigc_dict 非空则嵌入合规结构的 XMP；raw_xmp 用于注入任意(含损坏)XMP。"""
    img = Image.new("RGB", size, (180, 180, 180))
    xmp = raw_xmp if raw_xmp is not None else (_xmp_for(aigc_dict) if aigc_dict else None)
    if xmp is not None:
        meta = PngImagePlugin.PngInfo()
        meta.add_itxt("XML:com.adobe.xmp", xmp)
        img.save(path, "PNG", pnginfo=meta)
    else:
        img.save(path, "PNG")
    return str(path)


def broken_json_xmp() -> str:
    """合法 XMP 包裹但 JSON 本身损坏。"""
    return _XMP_TEMPLATE % (AIGC_NS, html.escape('{"Label": "1", '))
