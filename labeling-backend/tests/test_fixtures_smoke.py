from PIL import Image
from tests.fixtures import make_png, VALID_AIGC


def test_make_png_embeds_xmp(tmp_path):
    p = make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC)
    img = Image.open(p)
    img.load()
    assert "XML:com.adobe.xmp" in img.info
    assert "aigc:AIGC" in img.info["XML:com.adobe.xmp"]


def test_make_png_without_metadata(tmp_path):
    p = make_png(tmp_path / "b.png", aigc_dict=None)
    img = Image.open(p)
    img.load()
    assert "XML:com.adobe.xmp" not in img.info
