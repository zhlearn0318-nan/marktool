from PIL import Image

from app.metadata.xmp_reader import (
    extract_aigc_json,
    extract_aigc_records,
    read_aigc_records,
)
from tests.fixtures import (
    UPDATED_AIGC,
    VALID_AIGC,
    broken_json_xmp,
    duplicate_xmp,
    legacy_xmp,
    make_png,
    make_png_with_xmp_packets,
)
from tests.fixtures import _xmp_for


def _open(path):
    image = Image.open(path)
    image.load()
    return image


def test_extracts_valid_outer_aigc_document(tmp_path):
    path = make_png(tmp_path / "a.png", aigc_dict=VALID_AIGC)
    records = extract_aigc_records(_open(path))
    assert records[0].document == {"AIGC": VALID_AIGC}
    assert extract_aigc_json(_open(path))["ProduceID"] == "PRD-20260610-0001"


def test_returns_none_without_metadata(tmp_path):
    path = make_png(tmp_path / "b.png", aigc_dict=None)
    assert extract_aigc_json(_open(path)) is None
    assert extract_aigc_records(_open(path)) == []


def test_broken_json_returns_parse_error(tmp_path):
    path = make_png(tmp_path / "c.png", raw_xmp=broken_json_xmp())
    records = extract_aigc_records(_open(path))
    assert records[0].parse_error
    assert extract_aigc_json(_open(path)) == {}


def test_legacy_inner_object_is_read_but_not_rewritten(tmp_path):
    path = make_png(tmp_path / "legacy.png", raw_xmp=legacy_xmp(VALID_AIGC))
    records = extract_aigc_records(_open(path))
    assert records[0].document == VALID_AIGC
    assert records[0].aigc == VALID_AIGC


def test_duplicate_properties_are_counted(tmp_path):
    raw = duplicate_xmp(VALID_AIGC, UPDATED_AIGC)
    path = make_png(tmp_path / "duplicate.png", raw_xmp=raw)
    assert len(extract_aigc_records(_open(path))) == 2


def test_duplicate_png_xmp_chunks_are_counted_from_file(tmp_path):
    path = make_png_with_xmp_packets(
        tmp_path / "two-packets.png",
        [_xmp_for(VALID_AIGC), _xmp_for(UPDATED_AIGC)],
    )
    assert len(read_aigc_records(path)) == 2
