import hashlib
import os
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.metadata.exiftool_client import ExifToolClient
from app.metadata.exiftool_client import ExifToolExecutionError
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.image_adapter import ImageMetadataError, ImageMetadataService
from app.metadata.xmp_reader import read_aigc_records
from tests.fixtures import (
    AIGC_NS,
    UPDATED_DOCUMENT,
    VALID_AIGC,
    VALID_DOCUMENT,
    duplicate_xmp,
    legacy_xmp,
    make_jpeg,
    make_png,
)


def _find_exiftool() -> str | None:
    configured = os.getenv("EXIFTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    discovered = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if discovered:
        return discovered
    local_windows_copy = Path(r"D:\exiftool\exiftool.exe")
    return str(local_windows_copy) if local_windows_copy.is_file() else None


@pytest.fixture
def service(tmp_path):
    executable = _find_exiftool()
    if not executable:
        if os.getenv("AIGC_REQUIRE_EXIFTOOL") == "1":
            pytest.fail("CI 要求真实 ExifTool，但当前未找到可执行文件")
        pytest.skip("需要 ExifTool；请设置 EXIFTOOL_PATH 后运行集成测试")
    return ImageMetadataService(
        ExifToolClient(executable=executable),
        SQLiteIdentifierRegistry(str(tmp_path / "identifier-registry.sqlite3")),
    )


def _file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("maker", "suffix", "expected_format", "expected_mime"),
    [
        (make_jpeg, ".jpg", "JPEG", "image/jpeg"),
        (make_png, ".png", "PNG", "image/png"),
    ],
)
def test_clean_image_write_readback_and_integrity(
    tmp_path, service, maker, suffix, expected_format, expected_mime
):
    source = Path(maker(tmp_path / f"source{suffix}"))
    output = tmp_path / f"output{suffix}"
    original_hash = _file_sha256(source)

    result = service.write(str(source), str(output), VALID_DOCUMENT)

    assert output.is_file()
    assert _file_sha256(source) == original_hash
    assert result.detected_format == expected_format
    assert result.mime_type == expected_mime
    assert result.embedded_metadata == VALID_DOCUMENT
    assert all(vars(result.validation).values())
    records = read_aigc_records(str(output))
    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT
    with Image.open(output) as image:
        image.load()
        assert image.size == (400, 300)


@pytest.mark.parametrize(
    ("maker", "suffix"),
    [(make_jpeg, ".jpg"), (make_png, ".png")],
)
def test_existing_metadata_is_rejected_by_default(tmp_path, service, maker, suffix):
    source = Path(maker(tmp_path / f"marked{suffix}", aigc_dict=VALID_AIGC))
    output = tmp_path / f"rejected{suffix}"

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(output), UPDATED_DOCUMENT)

    assert captured.value.code == "AIGC_METADATA_EXISTS"
    assert not output.exists()


@pytest.mark.parametrize(
    ("maker", "suffix"),
    [(make_jpeg, ".jpg"), (make_png, ".png")],
)
def test_existing_metadata_can_be_replaced_as_one_record(
    tmp_path, service, maker, suffix
):
    source = Path(maker(tmp_path / f"marked{suffix}", aigc_dict=VALID_AIGC))
    output = tmp_path / f"replaced{suffix}"
    original_hash = _file_sha256(source)

    result = service.write(
        str(source), str(output), UPDATED_DOCUMENT, policy="replace"
    )

    records = read_aigc_records(str(output))
    assert len(records) == 1
    assert records[0].document == UPDATED_DOCUMENT
    assert result.validation.single_aigc_record is True
    assert _file_sha256(source) == original_hash
    assert read_aigc_records(str(source))[0].document == VALID_DOCUMENT


@pytest.mark.parametrize(
    ("maker", "fake_suffix", "output_suffix", "expected_format"),
    [
        (make_jpeg, ".png", ".jpg", "JPEG"),
        (make_png, ".jpg", ".png", "PNG"),
    ],
)
def test_real_format_is_used_when_extension_is_disguised(
    tmp_path, service, maker, fake_suffix, output_suffix, expected_format
):
    source = Path(maker(tmp_path / f"disguised{fake_suffix}"))
    output = tmp_path / f"detected{output_suffix}"

    result = service.write(str(source), str(output), VALID_DOCUMENT)

    assert result.detected_format == expected_format


@pytest.mark.parametrize("suffix", [".jpg", ".png"])
def test_corrupted_image_is_rejected(tmp_path, service, suffix):
    source = tmp_path / f"broken{suffix}"
    source.write_bytes(b"not a valid image")

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / f"output{suffix}"), VALID_DOCUMENT)

    assert captured.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_invalid_seven_field_document_is_rejected_before_writing(tmp_path, service):
    source = Path(make_png(tmp_path / "source.png"))
    invalid_document = {"AIGC": {**VALID_AIGC}}
    invalid_document["AIGC"].pop("ReservedCode2")

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / "output.png"), invalid_document)

    assert captured.value.code == "AIGC_SCHEMA_INVALID"
    assert any("ReservedCode2" in detail for detail in captured.value.details)


def test_original_and_existing_output_are_never_overwritten(tmp_path, service):
    source = Path(make_png(tmp_path / "source.png"))
    output = tmp_path / "already-exists.png"
    output.write_bytes(b"keep me")

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(output), VALID_DOCUMENT)
    assert captured.value.code == "OUTPUT_FILE_EXISTS"
    assert output.read_bytes() == b"keep me"

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(source), VALID_DOCUMENT)
    assert captured.value.code == "OUTPUT_PATH_INVALID"


@pytest.mark.parametrize(
    ("maker", "suffix"),
    [(make_jpeg, ".jpg"), (make_png, ".png")],
)
def test_replace_removes_duplicate_known_records_before_writing_one(
    tmp_path, service, maker, suffix
):
    raw_xmp = duplicate_xmp(VALID_AIGC, UPDATED_DOCUMENT["AIGC"])
    source = Path(maker(tmp_path / f"duplicates{suffix}", raw_xmp=raw_xmp))
    output = tmp_path / f"deduplicated{suffix}"

    result = service.write(str(source), str(output), VALID_DOCUMENT, policy="replace")

    records = read_aigc_records(str(output))
    assert result.validation.single_aigc_record is True
    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT


def test_replace_preserves_unrelated_xmp_metadata(tmp_path, service):
    old_xmp = legacy_xmp(VALID_AIGC)
    old_xmp = old_xmp.replace(
        f'xmlns:aigc="{AIGC_NS}"',
        f'xmlns:aigc="{AIGC_NS}" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"',
    ).replace(
        "  </rdf:Description>",
        "   <dc:description>需要保留的说明</dc:description>\n  </rdf:Description>",
    )
    source = Path(make_png(tmp_path / "with-other-xmp.png", raw_xmp=old_xmp))
    output = tmp_path / "preserved.png"

    service.write(str(source), str(output), UPDATED_DOCUMENT, policy="replace")

    raw_after = service.exiftool.read_raw_xmp(str(output))
    assert "需要保留的说明" in raw_after
    assert len(read_aigc_records(str(output))) == 1


def test_unrecognized_aigc_namespace_is_not_silently_overwritten(tmp_path, service):
    raw_xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:other="http://example.test/AIGC/2.0/">'
        '<other:Record>{"AIGC":{"Label":"1"}}</other:Record>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>'
    )
    source = Path(make_png(tmp_path / "unknown-carrier.png", raw_xmp=raw_xmp))
    output = tmp_path / "must-not-publish.png"

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(output), VALID_DOCUMENT, policy="replace")

    assert captured.value.code == "AIGC_METADATA_INCONSISTENT"
    assert not output.exists()


def test_unicode_file_names_roundtrip_on_windows(tmp_path, service):
    source = Path(make_png(tmp_path / "中文原图.png"))
    output = tmp_path / "中文结果.png"

    result = service.write(str(source), str(output), VALID_DOCUMENT)

    assert output.is_file()
    assert result.embedded_metadata["AIGC"]["ContentProducer"] == "ORG_1565201000000016"


def test_initial_write_rejects_mismatched_propagation_fields(tmp_path, service):
    source = Path(make_png(tmp_path / "source.png"))
    mismatched = {
        "AIGC": {
            **VALID_AIGC,
            "ContentPropagator": "ORG_OTHER",
            "PropagateID": "PROP_OTHER",
        }
    }

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / "output.png"), mismatched)

    assert captured.value.code == "AIGC_INITIAL_RELATION_INVALID"


def test_later_propagation_allows_different_propagator(tmp_path, service):
    source = Path(make_png(tmp_path / "source.png"))
    propagated = {
        "AIGC": {
            **VALID_AIGC,
            "ContentPropagator": "ORG_OTHER",
            "PropagateID": "PROP_OTHER",
        }
    }

    result = service.write(
        str(source),
        str(tmp_path / "output.png"),
        propagated,
        initial_write=False,
    )

    assert result.embedded_metadata == propagated


@pytest.mark.parametrize("invalid_value", ["ORG TEST", "示例机构"])
def test_writer_rejects_values_outside_strict_character_profile(
    tmp_path, service, invalid_value
):
    source = Path(make_png(tmp_path / "source.png"))
    document = {"AIGC": {**VALID_AIGC, "ContentProducer": invalid_value}}

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / "output.png"), document)

    assert captured.value.code == "AIGC_CHARACTER_INVALID"


def test_same_identifier_is_allowed_for_same_image_content(tmp_path, service):
    source = Path(make_png(tmp_path / "source.png"))

    service.write(str(source), str(tmp_path / "output-1.png"), VALID_DOCUMENT)
    service.write(str(source), str(tmp_path / "output-2.png"), VALID_DOCUMENT)

    assert (tmp_path / "output-1.png").is_file()
    assert (tmp_path / "output-2.png").is_file()


def test_same_identifier_is_rejected_for_different_image_content(tmp_path, service):
    first = Path(make_png(tmp_path / "first.png"))
    second = tmp_path / "second.png"
    Image.new("RGB", (400, 300), (10, 20, 30)).save(second, "PNG")
    service.write(str(first), str(tmp_path / "first-output.png"), VALID_DOCUMENT)

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(second), str(tmp_path / "second-output.png"), VALID_DOCUMENT)

    assert captured.value.code == "AIGC_IDENTIFIER_DUPLICATE"
    assert not (tmp_path / "second-output.png").exists()


def test_failed_write_rolls_back_identifier_reservation(tmp_path, service, monkeypatch):
    source = Path(make_png(tmp_path / "source.png"))
    original_write = service.exiftool.write_aigc

    def fail_write(*_args, **_kwargs):
        raise ExifToolExecutionError("simulated failure")

    monkeypatch.setattr(service.exiftool, "write_aigc", fail_write)
    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / "failed.png"), VALID_DOCUMENT)
    assert captured.value.code == "METADATA_WRITE_FAILED"

    monkeypatch.setattr(service.exiftool, "write_aigc", original_write)
    service.write(str(source), str(tmp_path / "success.png"), VALID_DOCUMENT)
    assert (tmp_path / "success.png").is_file()


@pytest.mark.parametrize("policy", ["reject", "replace"])
def test_extended_xmp_jpeg_is_explicitly_rejected(tmp_path, service, policy):
    source = Path(make_jpeg(tmp_path / "extended.jpg"))
    original = source.read_bytes()
    extension_payload = (
        b"http://ns.adobe.com/xmp/extension/\x00"
        + b"0123456789ABCDEF0123456789ABCDEF"
        + (4).to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + b"test"
    )
    segment = b"\xff\xe1" + (len(extension_payload) + 2).to_bytes(2, "big") + extension_payload
    source.write_bytes(original[:2] + segment + original[2:])

    with pytest.raises(ImageMetadataError) as captured:
        service.write(
            str(source), str(tmp_path / "output.jpg"), VALID_DOCUMENT, policy=policy
        )

    assert captured.value.code == "EXTENDED_XMP_UNSUPPORTED"
    assert not (tmp_path / "output.jpg").exists()


def test_cross_reader_disagreement_stops_before_write(tmp_path, service, monkeypatch):
    source = Path(make_png(tmp_path / "source.png"))
    monkeypatch.setattr(
        service.exiftool,
        "read_known_aigc_values",
        lambda _path: ['{"AIGC":{"Label":"1"}}'],
    )

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / "output.png"), VALID_DOCUMENT)

    assert captured.value.code == "AIGC_METADATA_INCONSISTENT"
    assert not (tmp_path / "output.png").exists()


def test_image_dimension_limit_is_enforced_before_processing(tmp_path, service):
    source = Path(make_png(tmp_path / "wide.png", size=(101, 10)))
    service.max_image_width = 100

    with pytest.raises(ImageMetadataError) as captured:
        service.write(str(source), str(tmp_path / "output.png"), VALID_DOCUMENT)

    assert captured.value.code == "IMAGE_DIMENSIONS_EXCEEDED"
