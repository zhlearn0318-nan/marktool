from PIL import Image

from app.engine.base import DetectionContext
from app.engine.detectors.metadata_detector import MetadataDetector
from app.schemas.models import CheckStatus
from tests.fixtures import (
    MISSING_FIELD_AIGC,
    UPDATED_AIGC,
    VALID_AIGC,
    broken_json_xmp,
    duplicate_xmp,
    legacy_xmp,
    make_png,
)


def _ctx(path):
    image = Image.open(path)
    image.load()
    return DetectionContext(file_path=str(path), image=image)


def test_valid_metadata_passes(tmp_path):
    ctx = _ctx(make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.PASS
    assert ctx.cache["aigc_record_count"] == 1
    assert ctx.cache["aigc_metadata"]["ProduceID"] == "PRD-20260610-0001"


def test_no_metadata_is_fail(tmp_path):
    ctx = _ctx(make_png(tmp_path / "b.png", aigc_dict=None))
    assert MetadataDetector().detect(ctx)[0].status == CheckStatus.FAIL


def test_missing_field_is_fail(tmp_path):
    ctx = _ctx(make_png(tmp_path / "c.png", aigc_dict=MISSING_FIELD_AIGC))
    item = MetadataDetector().detect(ctx)[0]
    assert item.status == CheckStatus.FAIL
    assert "ProduceID" in item.detail


def test_broken_json_is_fail(tmp_path):
    ctx = _ctx(make_png(tmp_path / "d.png", raw_xmp=broken_json_xmp()))
    assert MetadataDetector().detect(ctx)[0].status == CheckStatus.FAIL


def test_legacy_inner_object_is_fail(tmp_path):
    ctx = _ctx(make_png(tmp_path / "legacy.png", raw_xmp=legacy_xmp(VALID_AIGC)))
    item = MetadataDetector().detect(ctx)[0]
    assert item.status == CheckStatus.FAIL
    assert "AIGC" in item.detail


def test_duplicate_records_are_fail(tmp_path):
    raw = duplicate_xmp(VALID_AIGC, UPDATED_AIGC)
    ctx = _ctx(make_png(tmp_path / "duplicate.png", raw_xmp=raw))
    item = MetadataDetector().detect(ctx)[0]
    assert item.status == CheckStatus.FAIL
    assert item.id == "metadata.uniqueness"


def test_strict_character_violation_is_project_warning_not_gb_failure(tmp_path):
    invalid_characters = {
        **VALID_AIGC,
        "ContentProducer": "示例机构",
    }
    ctx = _ctx(make_png(tmp_path / "characters.png", aigc_dict=invalid_characters))
    items = MetadataDetector().detect(ctx)
    assert items[0].status == CheckStatus.PASS
    assert items[1].status == CheckStatus.WARN
    assert items[1].id == "metadata.project_policy"
    assert "不等同于国标不合规" in items[1].suggestion


def test_later_propagation_with_different_provider_is_still_valid(tmp_path):
    propagated = {
        **VALID_AIGC,
        "ContentPropagator": "ORG_OTHER",
        "PropagateID": "PROP_OTHER",
    }
    ctx = _ctx(make_png(tmp_path / "propagated.png", aigc_dict=propagated))
    assert MetadataDetector().detect(ctx)[0].status == CheckStatus.PASS
