from PIL import Image
from app.engine.base import DetectionContext
from app.engine.detectors.metadata_detector import MetadataDetector
from app.schemas.models import CheckStatus
from tests.fixtures import make_png, broken_json_xmp, VALID_AIGC, MISSING_FIELD_AIGC


def _ctx(path):
    img = Image.open(path)
    img.load()
    return DetectionContext(file_path=str(path), image=img)


def test_valid_metadata_passes(tmp_path):
    ctx = _ctx(make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.PASS
    assert ctx.cache["aigc_metadata"]["ProduceID"] == "PRD-20260610-0001"


def test_no_metadata_is_fail(tmp_path):
    ctx = _ctx(make_png(tmp_path / "b.png", aigc_dict=None))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.FAIL


def test_missing_field_is_warn(tmp_path):
    ctx = _ctx(make_png(tmp_path / "c.png", aigc_dict=MISSING_FIELD_AIGC))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.WARN
    assert "ProduceID" in items[0].detail


def test_broken_json_is_warn(tmp_path):
    ctx = _ctx(make_png(tmp_path / "d.png", raw_xmp=broken_json_xmp()))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.WARN
