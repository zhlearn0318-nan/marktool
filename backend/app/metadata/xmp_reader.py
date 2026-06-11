import html
import json
import re
from typing import Optional
from PIL import Image

_AIGC_RE = re.compile(r"<aigc:metadata>(.*?)</aigc:metadata>", re.DOTALL)
_XMP_APP1_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"


def _raw_xmp(image: Image.Image) -> Optional[str]:
    # PNG: iTXt 关键字 "XML:com.adobe.xmp" 进入 image.info
    xmp = image.info.get("XML:com.adobe.xmp")
    if xmp:
        return xmp.decode("utf-8", "ignore") if isinstance(xmp, (bytes, bytearray)) else xmp
    # JPEG: XMP 在 APP1 段
    applist = getattr(image, "applist", None)
    if applist:
        for marker, content in applist:
            if marker == "APP1" and content.startswith(_XMP_APP1_PREFIX):
                return content[len(_XMP_APP1_PREFIX):].decode("utf-8", "ignore")
    return None


def extract_aigc_json(image: Image.Image) -> Optional[dict]:
    """返回 AIGC JSON dict；无 XMP/无该元素返回 None；JSON 损坏返回 {}。"""
    raw = _raw_xmp(image)
    if not raw:
        return None
    m = _AIGC_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError:
        return {}
