import html
import json
from pathlib import Path

import pytest

from app.metadata.html_carrier import HtmlMetadataAdapter, HtmlMetadataError
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


def _compact(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def _meta(document: dict, *, name: str = "AIGC") -> str:
    return f'<meta name="{name}" content="{html.escape(_compact(document), quote=True)}">'


def _write_html(
    path: Path,
    *,
    head: str = '<meta charset="utf-8"><title>测试页面</title>',
    body: str = '<main id="content">正文<script>const value = "<head>";</script></main>',
    encoding: str = "utf-8",
    bom: bool = False,
) -> Path:
    text = f"<!doctype html>\r\n<html><head>{head}</head><body>{body}</body></html>"
    codec = "utf-8-sig" if bom else encoding
    path.write_bytes(text.encode(codec))
    return path


@pytest.fixture
def adapter():
    return HtmlMetadataAdapter()


def test_clean_html_write_readback_and_content_integrity(tmp_path, adapter):
    source = _write_html(tmp_path / "source.html")
    output = tmp_path / "output.html"
    original = source.read_bytes()

    result = adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert source.read_bytes() == original
    assert result.detected_format == "HTML"
    assert result.mime_type == "text/html"
    assert result.carrier == "html-meta-aigc-v1"
    assert result.embedded_metadata == VALID_DOCUMENT
    assert all(vars(result.validation).values())
    records = adapter.read_records(output)
    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT
    assert '<main id="content">正文' in output.read_text(encoding="utf-8")


def test_existing_metadata_is_rejected_by_default(tmp_path, adapter):
    source = _write_html(tmp_path / "marked.html", head=_meta(VALID_DOCUMENT))
    output = tmp_path / "rejected.html"

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(output), UPDATED_DOCUMENT)

    assert captured.value.code == "AIGC_METADATA_EXISTS"
    assert not output.exists()


def test_existing_metadata_can_be_replaced_as_one_record(tmp_path, adapter):
    source = _write_html(
        tmp_path / "marked.html",
        head=f'<title>保留标题</title>{_meta(VALID_DOCUMENT)}<link rel="stylesheet" href="a.css">',
    )
    output = tmp_path / "replaced.html"

    result = adapter.write(
        str(source), str(output), UPDATED_DOCUMENT, policy="replace"
    )

    records = adapter.read_records(output)
    assert len(records) == 1
    assert records[0].document == UPDATED_DOCUMENT
    assert result.validation.content_integrity_valid is True
    rendered = output.read_text(encoding="utf-8")
    assert "保留标题" in rendered
    assert '<link rel="stylesheet" href="a.css">' in rendered


def test_replace_removes_all_duplicate_candidates(tmp_path, adapter):
    head = _meta(VALID_DOCUMENT) + '<meta property="site:AIGC" content="broken">'
    source = _write_html(tmp_path / "duplicates.html", head=head)
    output = tmp_path / "deduplicated.html"

    adapter.write(str(source), str(output), UPDATED_DOCUMENT, policy="replace")

    records = adapter.read_records(output)
    assert len(records) == 1
    assert records[0].document == UPDATED_DOCUMENT


def test_broken_json_is_exposed_to_the_caller(tmp_path, adapter):
    source = _write_html(
        tmp_path / "broken-json.html",
        head='<meta name="AIGC" content="{&quot;AIGC&quot;:">',
    )

    records = adapter.read_records(source)

    assert len(records) == 1
    assert records[0].document is None
    assert "不是合法 JSON" in records[0].parse_error


def test_aigc_name_matching_is_ascii_case_insensitive(tmp_path, adapter):
    source = _write_html(
        tmp_path / "case.html", head=_meta(VALID_DOCUMENT, name="aIgC")
    )

    records = adapter.read_records(source)

    assert len(records) == 1
    assert records[0].document == VALID_DOCUMENT


def test_aigc_text_in_body_or_javascript_is_not_a_metadata_record(tmp_path, adapter):
    source = _write_html(
        tmp_path / "body-text.html",
        body='<p>AIGC</p><script>const AIGC = {"Label":"1"};</script>',
    )

    assert adapter.read_records(source) == []


def test_aigc_meta_outside_head_stops_writing(tmp_path, adapter):
    source = _write_html(
        tmp_path / "outside.html", body=f"<main>正文</main>{_meta(VALID_DOCUMENT)}"
    )

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "AIGC_CARRIER_INVALID"


@pytest.mark.parametrize(
    "c2pa_tag",
    [
        '<script type="application/c2pa">AAAA</script>',
        '<link rel="c2pa-manifest" href="manifest.c2pa" type="application/c2pa">',
    ],
)
def test_existing_c2pa_manifest_requires_resigning(tmp_path, adapter, c2pa_tag):
    source = _write_html(tmp_path / "c2pa.html", head=c2pa_tag)

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "C2PA_RESIGN_REQUIRED"


def test_utf8_bom_is_preserved(tmp_path, adapter):
    source = _write_html(tmp_path / "bom.html", bom=True)
    output = tmp_path / "bom-output.html"

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert adapter.read_records(output)[0].document == VALID_DOCUMENT


def test_declared_gb18030_body_is_preserved(tmp_path, adapter):
    source = _write_html(
        tmp_path / "gb18030.html",
        head='<meta charset="gb18030"><title>中文标题</title>',
        body="<p>中文正文保持不变</p>",
        encoding="gb18030",
    )
    output = tmp_path / "gb18030-output.html"

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    decoded = output.read_bytes().decode("gb18030")
    assert "中文正文保持不变" in decoded
    assert adapter.read_records(output)[0].document == VALID_DOCUMENT


def test_javascript_charset_text_is_not_mistaken_for_encoding_declaration(
    tmp_path, adapter
):
    source = _write_html(
        tmp_path / "script-charset.html",
        body='<script>const charset = "shift_jis";</script><p>中文正文</p>',
    )
    output = tmp_path / "script-charset-output.html"

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert adapter.read_records(output)[0].document == VALID_DOCUMENT
    assert "中文正文" in output.read_text(encoding="utf-8")


def test_fake_meta_inside_comment_or_script_does_not_declare_encoding(
    tmp_path, adapter
):
    source = _write_html(
        tmp_path / "fake-meta-charset.html",
        head=(
            '<meta charset="utf-8">'
            '<!-- <meta charset="shift_jis"> -->'
            '<script>const sample = \'<meta charset="big5">\';</script>'
        ),
    )
    output = tmp_path / "fake-meta-charset-output.html"

    adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert adapter.read_records(output)[0].document == VALID_DOCUMENT


def test_conflicting_real_charset_declarations_are_rejected(tmp_path, adapter):
    source = _write_html(
        tmp_path / "conflict.html",
        head='<meta charset="utf-8"><meta charset="gb18030">',
    )

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "HTML_ENCODING_CONFLICT"


def test_html_attribute_special_characters_roundtrip(tmp_path, adapter):
    source = _write_html(tmp_path / "special.html")
    special = {
        "AIGC": {
            **VALID_AIGC,
            "ContentProducer": "ORG&A<B>C'D",
            "ContentPropagator": "ORG&A<B>C'D",
        }
    }
    output = tmp_path / "special-output.html"

    adapter.write(str(source), str(output), special)

    assert adapter.read_records(output)[0].document == special


def test_unsupported_utf16_is_rejected(tmp_path, adapter):
    source = tmp_path / "utf16.html"
    source.write_bytes(
        "<!doctype html><html><head></head><body>正文</body></html>".encode(
            "utf-16"
        )
    )

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "HTML_ENCODING_UNSUPPORTED"


def test_extension_is_not_used_as_the_format_authority(tmp_path, adapter):
    source = _write_html(tmp_path / "disguised.txt")
    output = tmp_path / "result.data"

    result = adapter.write(str(source), str(output), VALID_DOCUMENT)

    assert result.detected_format == "HTML"
    assert adapter.read_records(output)[0].document == VALID_DOCUMENT


def test_plain_text_with_html_extension_is_rejected(tmp_path, adapter):
    source = tmp_path / "fake.html"
    source.write_text("这不是 HTML", encoding="utf-8")

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_html_without_closing_root_is_safely_rejected(tmp_path, adapter):
    source = tmp_path / "incomplete.html"
    source.write_text(
        "<!doctype html><html><head></head><body><p>正文</p></body>",
        encoding="utf-8",
    )

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_html_without_explicit_head_is_safely_rejected(tmp_path, adapter):
    source = tmp_path / "no-head.html"
    source.write_text(
        "<!doctype html><html><body><p>正文</p></body></html>", encoding="utf-8"
    )

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), VALID_DOCUMENT)

    assert captured.value.code == "HTML_STRUCTURE_INVALID"


def test_invalid_seven_field_document_is_rejected_before_writing(tmp_path, adapter):
    source = _write_html(tmp_path / "source.html")
    invalid = {"AIGC": {**VALID_AIGC}}
    invalid["AIGC"].pop("ReservedCode2")

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), invalid)

    assert captured.value.code == "AIGC_SCHEMA_INVALID"
    assert any("ReservedCode2" in item for item in captured.value.details)


def test_initial_write_rejects_mismatched_propagation_fields(tmp_path, adapter):
    source = _write_html(tmp_path / "source.html")
    invalid = {
        "AIGC": {
            **VALID_AIGC,
            "ContentPropagator": "ORG_OTHER",
            "PropagateID": "PROP_OTHER",
        }
    }

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(tmp_path / "output.html"), invalid)

    assert captured.value.code == "AIGC_INITIAL_RELATION_INVALID"


def test_original_and_existing_output_are_never_overwritten(tmp_path, adapter):
    source = _write_html(tmp_path / "source.html")
    original = source.read_bytes()
    output = tmp_path / "exists.html"
    output.write_bytes(b"keep me")

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(output), VALID_DOCUMENT)
    assert captured.value.code == "OUTPUT_FILE_EXISTS"
    assert output.read_bytes() == b"keep me"

    with pytest.raises(HtmlMetadataError) as captured:
        adapter.write(str(source), str(source), VALID_DOCUMENT)
    assert captured.value.code == "OUTPUT_PATH_INVALID"
    assert source.read_bytes() == original


def test_stage_callback_uses_existing_job_stage_names(tmp_path, adapter):
    source = _write_html(tmp_path / "source.html")
    stages = []

    adapter.write(
        str(source),
        str(tmp_path / "output.html"),
        VALID_DOCUMENT,
        stage_callback=stages.append,
    )

    assert stages == ["writing_metadata", "verifying_metadata", "publishing_output"]
