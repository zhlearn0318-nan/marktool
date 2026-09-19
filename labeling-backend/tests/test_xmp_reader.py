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


# ---- 合并时补的回归用例（原模块缺此覆盖）----

def test_namespace_declaration_is_not_counted_as_a_record():
    """命名空间声明 `xmlns:aigc=…` 不是 AIGC 属性，不得被当成一份标识。

    回归用例：XMP 包损坏走正则兜底时，属性名模式若不加词边界，会从
    `xmlns:aigc` 的中间开始匹配，把一个纯声明读成"又一份标识"——
    后果是干净文件被误报成多份/字段冲突，进而误判为不合规。
    """
    # 故意的非法 XML：属性值里有未转义的引号 → ElementTree 解析失败走兜底分支
    malformed = (
        '<rdf:Description xmlns:aigc="http://example.com/aigc#" '
        'aigc:AIGC="{"AIGC":{"Label":"1"}}"/>'
    )
    records = _records_from_raw(malformed)
    assert len(records) == 1, [r.property_name for r in records]
    assert not any("xmlns" in r.property_name for r in records)


def _records_from_raw(raw_xmp: str):
    """走兜底正则分支：直接构造假 Image，只带一份会被解析失败的 XMP。"""
    from app.metadata.xmp_reader import _candidate_values, _parse_record

    return [
        _parse_record(value, property_name=name)
        for name, value in _candidate_values(raw_xmp)
    ]
