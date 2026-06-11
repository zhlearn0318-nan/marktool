from PIL import Image
from app.metadata.xmp_reader import extract_aigc_json
from tests.fixtures import make_png, broken_json_xmp, VALID_AIGC


def _open(path):
    img = Image.open(path)
    img.load()
    return img


def test_extracts_valid_aigc_json(tmp_path):
    p = make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC)
    data = extract_aigc_json(_open(p))
    assert data["ProduceID"] == "PRD-20260610-0001"


def test_returns_none_without_metadata(tmp_path):
    p = make_png(tmp_path / "b.png", aigc_dict=None)
    assert extract_aigc_json(_open(p)) is None


def test_broken_json_returns_empty_dict(tmp_path):
    p = make_png(tmp_path / "c.png", raw_xmp=broken_json_xmp())
    assert extract_aigc_json(_open(p)) == {}
