import html
import json
import posixpath
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app.metadata.docx_carrier import DocxMetadataAdapter, DocxMetadataError
from tests.common import VALID_AIGC

VALID_DOCUMENT = {"AIGC": VALID_AIGC}
UPDATED_DOCUMENT = {
    "AIGC": {
        **VALID_AIGC,
        "Label": "2",
        "ProduceID": "0198F21A-6F28-7000-A102-000000000002",
        "PropagateID": "0198F21A-6F28-7000-A102-000000000002",
    }
}


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>"""

DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{}</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>"""

DOCUMENT_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId10"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>"""

PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TRANSITIONAL_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
STRICT_REL_NS = "http://purl.oclc.org/ooxml/officeDocument/relationships"
TRANSITIONAL_PROPS_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/customXmlDataProps"
)
STRICT_PROPS_NS = "http://purl.oclc.org/ooxml/officeDocument/customXmlDataProps"
AIGC_NS = "urn:marktool:gb45438:2025:aigc-metadata:v1"


def _make_docx(
    path: Path,
    *,
    text: str = "DOCX 正文保持不变",
    extra_entries: dict[str, bytes] | None = None,
    with_document_rels: bool = True,
) -> Path:
    entries = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "word/document.xml": DOCUMENT_XML.format(html.escape(text)).encode("utf-8"),
        "word/styles.xml": (
            b'<w:styles xmlns:w="http://schemas.openxmlformats.org/'
            b'wordprocessingml/2006/main"/>'
        ),
        "word/media/image1.bin": b"original-binary-content\x00\x01",
    }
    if with_document_rels:
        entries["word/_rels/document.xml.rels"] = DOCUMENT_RELS
    entries.update(extra_entries or {})
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _make_profile_docx(
    path: Path,
    *,
    strict: bool,
    main_part: str,
    text: str = "DOCX 正文保持不变",
) -> Path:
    office_rel_ns = STRICT_REL_NS if strict else TRANSITIONAL_REL_NS
    word_ns = (
        "http://purl.oclc.org/ooxml/wordprocessingml/main"
        if strict
        else "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    )
    content_types = f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/{main_part}"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""".encode("utf-8")
    root_rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{PACKAGE_REL_NS}">
  <Relationship Id="rId1" Type="{office_rel_ns}/officeDocument"
    Target="{main_part}"/>
</Relationships>""".encode("utf-8")
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{word_ns}">
  <w:body><w:p><w:r><w:t>{html.escape(text)}</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>""".encode("utf-8")
    entries = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        main_part: document,
        "assets/original.bin": b"preserve-this-content",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def _package_entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rewrite_package(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


def _external_custom_xml(document: dict) -> bytes:
    raw = html.escape(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    )
    return f"<vendor><AIGC>{raw}</AIGC></vendor>".encode("utf-8")


@pytest.fixture
def adapter():
    return DocxMetadataAdapter()


def test_clean_docx_write_readback_and_preserves_other_parts(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    output = tmp_path / "output.docx"
    source_entries = _package_entries(source)

    result = adapter.write(str(source), str(output), VALID_DOCUMENT)

    output_entries = _package_entries(output)
    assert result.detected_format == "DOCX"
    assert result.carrier == "ooxml-custom-xml-aigc-v1"
    assert result.embedded_metadata == VALID_DOCUMENT
    assert all(vars(result.validation).values())
    assert source_entries["word/document.xml"] == output_entries["word/document.xml"]
    assert source_entries["word/styles.xml"] == output_entries["word/styles.xml"]
    assert source_entries["word/media/image1.bin"] == output_entries["word/media/image1.bin"]
    records = adapter.read_records(output)
    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT
    assert records[0].canonical_carrier is True


@pytest.mark.parametrize(
    ("strict", "main_part", "expected_props_ns", "expected_profile"),
    [
        (False, "documents/main.xml", TRANSITIONAL_PROPS_NS, "transitional"),
        (True, "documents/main.xml", STRICT_PROPS_NS, "strict"),
    ],
)
def test_ooxml_profiles_and_non_default_main_part_are_supported(
    tmp_path,
    adapter,
    strict,
    main_part,
    expected_props_ns,
    expected_profile,
):
    source = _make_profile_docx(
        tmp_path / f"{expected_profile}.docx",
        strict=strict,
        main_part=main_part,
    )
    output = tmp_path / f"{expected_profile}-labeled.docx"
    original_main = _package_entries(source)[main_part]

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    inspection = adapter.inspect(output)
    entries = _package_entries(output)
    props_part = next(
        name
        for name in entries
        if name.startswith("customXml/aigcMetadataProps") and name.endswith(".xml")
    )
    props_root = ET.fromstring(entries[props_part])
    document_rels_part = posixpath.join(
        posixpath.dirname(main_part),
        "_rels",
        posixpath.basename(main_part) + ".rels",
    )
    document_rels = ET.fromstring(entries[document_rels_part])
    custom_relationship = next(
        item for item in document_rels if item.get("Type", "").endswith("/customXml")
    )
    expected_rel_ns = STRICT_REL_NS if strict else TRANSITIONAL_REL_NS

    assert inspection.ooxml_conformance == expected_profile
    assert inspection.main_document_part == main_part
    assert entries[main_part] == original_main
    assert props_root.tag == f"{{{expected_props_ns}}}datastoreItem"
    assert props_root.get(f"{{{expected_props_ns}}}itemID")
    assert custom_relationship.get("Type") == f"{expected_rel_ns}/customXml"
    assert adapter.read_records(output)[0].document == VALID_DOCUMENT


def test_docx_without_document_relationships_can_be_labeled(tmp_path, adapter):
    source = _make_docx(
        tmp_path / "no-document-rels.docx", with_document_rels=False
    )
    output = tmp_path / "output.docx"

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert adapter.read_records(output)[0].document == VALID_DOCUMENT
    assert "word/_rels/document.xml.rels" in _package_entries(output)


def test_existing_metadata_is_rejected_by_default(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    marked = tmp_path / "marked.docx"
    adapter.write(str(source), str(marked), VALID_DOCUMENT)

    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(str(marked), str(tmp_path / "rejected.docx"), UPDATED_DOCUMENT)

    assert captured.value.code == "AIGC_METADATA_EXISTS"


def test_existing_metadata_can_be_replaced_as_one_record(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    marked = tmp_path / "marked.docx"
    replaced = tmp_path / "replaced.docx"
    adapter.write(str(source), str(marked), VALID_DOCUMENT)
    marked_content_hash = adapter.inspect(marked).protected_content_sha256

    adapter.write(
        str(marked), str(replaced), UPDATED_DOCUMENT, policy="replace", initial_write=False
    )

    records = adapter.read_records(replaced)
    assert len(records) == 1
    assert records[0].document == UPDATED_DOCUMENT
    assert adapter.inspect(replaced).protected_content_sha256 == marked_content_hash


def test_unknown_custom_xml_aigc_is_detected_but_not_rewritten(tmp_path, adapter):
    source = _make_docx(
        tmp_path / "external.docx",
        extra_entries={"customXml/vendor.xml": _external_custom_xml(VALID_DOCUMENT)},
    )

    records = adapter.read_records(source)
    assert len(records) == 1
    assert records[0].canonical_carrier is False

    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(
            str(source),
            str(tmp_path / "output.docx"),
            UPDATED_DOCUMENT,
            policy="replace",
        )
    assert captured.value.code == "AIGC_CARRIER_UNSUPPORTED"


def test_damaged_custom_xml_properties_are_reported_and_not_rewritten(
    tmp_path, adapter
):
    source = _make_docx(tmp_path / "source.docx")
    marked = tmp_path / "marked.docx"
    adapter.write(str(source), str(marked), VALID_DOCUMENT)
    entries = _package_entries(marked)
    props_part = next(
        name
        for name in entries
        if name.startswith("customXml/aigcMetadataProps") and name.endswith(".xml")
    )
    entries[props_part] = entries[props_part].replace(
        TRANSITIONAL_PROPS_NS.encode("ascii"),
        b"urn:invalid:custom-xml-properties",
    )
    _rewrite_package(marked, entries)

    records = adapter.read_records(marked)

    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT
    assert records[0].canonical_carrier is False
    assert "根元素或命名空间无效" in (records[0].carrier_error or "")
    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(
            str(marked),
            str(tmp_path / "unsafe-replace.docx"),
            UPDATED_DOCUMENT,
            policy="replace",
            initial_write=False,
        )
    assert captured.value.code == "AIGC_CARRIER_UNSUPPORTED"


def test_nested_aigc_element_is_detected_but_not_treated_as_canonical(
    tmp_path, adapter
):
    source = _make_docx(tmp_path / "source.docx")
    marked = tmp_path / "marked.docx"
    adapter.write(str(source), str(marked), VALID_DOCUMENT)
    entries = _package_entries(marked)
    data_part = next(
        name
        for name in entries
        if name.startswith("customXml/aigcMetadata")
        and not name.startswith("customXml/aigcMetadataProps")
        and name.endswith(".xml")
    )
    root = ET.fromstring(entries[data_part])
    aigc = next(iter(root))
    root.remove(aigc)
    wrapper = ET.SubElement(root, f"{{{AIGC_NS}}}Wrapper")
    wrapper.append(aigc)
    entries[data_part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    _rewrite_package(marked, entries)

    records = adapter.read_records(marked)

    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT
    assert records[0].canonical_carrier is False
    assert "不是项目载体根元素的直接子元素" in (records[0].carrier_error or "")
    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(
            str(marked),
            str(tmp_path / "unsafe-replace.docx"),
            UPDATED_DOCUMENT,
            policy="replace",
            initial_write=False,
        )
    assert captured.value.code == "AIGC_CARRIER_UNSUPPORTED"


def test_new_custom_xml_item_id_does_not_collide_with_existing_package_id(
    tmp_path, adapter, monkeypatch
):
    collision = uuid.UUID("11111111-1111-4111-8111-111111111111")
    unique = uuid.UUID("22222222-2222-4222-8222-222222222222")
    props = f"""<?xml version="1.0" encoding="UTF-8"?>
<ds:datastoreItem xmlns:ds="{TRANSITIONAL_PROPS_NS}"
 ds:itemID="{{{str(collision).upper()}}}"><ds:schemaRefs/></ds:datastoreItem>""".encode(
        "utf-8"
    )
    source = _make_docx(
        tmp_path / "source.docx",
        extra_entries={"customXml/vendorProps.xml": props},
    )
    generated = iter((collision, unique))
    monkeypatch.setattr(
        "app.metadata.docx_carrier.uuid.uuid4",
        lambda: next(generated),
    )
    output = tmp_path / "output.docx"

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    entries = _package_entries(output)
    project_props = next(
        name
        for name in entries
        if name.startswith("customXml/aigcMetadataProps") and name.endswith(".xml")
    )
    root = ET.fromstring(entries[project_props])
    assert root.get(f"{{{TRANSITIONAL_PROPS_NS}}}itemID") == (
        "{" + str(unique).upper() + "}"
    )


def test_office_digital_signature_blocks_writing(tmp_path, adapter):
    source = _make_docx(
        tmp_path / "signed.docx",
        extra_entries={"_xmlsignatures/sig1.xml": b"<Signature/>",},
    )

    assert adapter.inspect(source).has_digital_signature is True
    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.docx"), VALID_DOCUMENT)
    assert captured.value.code == "DOCX_RESIGN_REQUIRED"


def test_invalid_seven_field_document_is_rejected(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    invalid = {"AIGC": {**VALID_AIGC}}
    invalid["AIGC"].pop("ReservedCode2")

    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.docx"), invalid)

    assert captured.value.code == "AIGC_SCHEMA_INVALID"


def test_initial_write_requires_matching_producer_and_propagator(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    invalid = {
        "AIGC": {
            **VALID_AIGC,
            "ContentPropagator": "ORG_OTHER",
            "PropagateID": "OTHER-ID",
        }
    }

    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.docx"), invalid)

    assert captured.value.code == "AIGC_INITIAL_RELATION_INVALID"


def test_plain_zip_and_macro_document_are_rejected(tmp_path, adapter):
    plain = tmp_path / "plain.docx"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("file.txt", "not a docx")
    macro = _make_docx(tmp_path / "macro.docm")
    entries = _package_entries(macro)
    entries["[Content_Types].xml"] = entries["[Content_Types].xml"].replace(
        b"wordprocessingml.document.main+xml",
        b"wordprocessingml.document.macroEnabled.main+xml",
    )
    with zipfile.ZipFile(macro, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)

    for candidate in (plain, macro):
        with pytest.raises(DocxMetadataError) as captured:
            adapter.write(
                str(candidate), str(tmp_path / f"{candidate.stem}-out.docx"), VALID_DOCUMENT
            )
        assert captured.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_extension_is_not_used_as_format_authority(tmp_path, adapter):
    source = _make_docx(tmp_path / "document.data")
    output = tmp_path / "result.bin"

    result = adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert result.detected_format == "DOCX"
    assert adapter.read_records(output)[0].document == VALID_DOCUMENT


def test_unsafe_and_duplicate_zip_parts_are_rejected(tmp_path, adapter):
    unsafe = _make_docx(tmp_path / "unsafe.docx")
    entries = _package_entries(unsafe)
    with zipfile.ZipFile(unsafe, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr("../outside.xml", b"<x/>")

    duplicate = _make_docx(tmp_path / "duplicate.docx")
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "a") as archive:
            archive.writestr("word/document.xml", DOCUMENT_XML.format("other"))

    with pytest.raises(DocxMetadataError) as unsafe_error:
        adapter.inspect(unsafe)
    assert unsafe_error.value.code == "DOCX_UNSAFE_PART_NAME"
    with pytest.raises(DocxMetadataError) as duplicate_error:
        adapter.inspect(duplicate)
    assert duplicate_error.value.code == "DOCX_DUPLICATE_PARTS"


def test_original_and_existing_output_are_never_overwritten(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    original = source.read_bytes()
    output = tmp_path / "existing.docx"
    output.write_bytes(b"keep")

    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(str(source), str(output), VALID_DOCUMENT)
    assert captured.value.code == "OUTPUT_FILE_EXISTS"
    assert output.read_bytes() == b"keep"

    with pytest.raises(DocxMetadataError) as captured:
        adapter.write(str(source), str(source), VALID_DOCUMENT)
    assert captured.value.code == "OUTPUT_PATH_INVALID"
    assert source.read_bytes() == original


def test_stage_callback_uses_shared_stage_names(tmp_path, adapter):
    source = _make_docx(tmp_path / "source.docx")
    stages = []

    adapter.write(
        str(source),
        str(tmp_path / "output.docx"),
        VALID_DOCUMENT,
        stage_callback=stages.append,
    )

    assert stages == ["writing_metadata", "verifying_metadata", "publishing_output"]
