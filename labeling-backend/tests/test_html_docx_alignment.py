"""HTML / DOCX 与文本模态公共接口的对齐回归。"""
from __future__ import annotations

import html
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import DocxAdapter, HtmlAdapter, get_adapter
from app.core.mimetype import detect_mime, suffix_for_mime
from app.config import Settings
from app.main import create_app
from app.metadata.document_inspector import DocumentComplianceInspector
from tests.common import VALID_AIGC


DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def make_html(path: Path) -> Path:
    path.write_text(
        "<!doctype html>\r\n<html><head><meta charset=\"utf-8\"><title>示例</title>"
        "</head><body><main>正文</main></body></html>",
        encoding="utf-8",
        newline="",
    )
    return path


def make_docx(path: Path) -> Path:
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    root_rels = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{html.escape("DOCX 正文")}</w:t></w:r></w:p></w:body>'
        '</w:document>'
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
    return path


@pytest.mark.parametrize(
    ("mime", "adapter_type", "suffix"),
    [("text/html", HtmlAdapter, ".html"), (DOCX_MIME, DocxAdapter, ".docx")],
)
def test_registry_and_suffix(mime, adapter_type, suffix):
    adapter = get_adapter(mime)
    assert isinstance(adapter, adapter_type)
    assert adapter.modality == "text"
    assert suffix_for_mime(mime) == suffix


def test_mime_detection_is_not_extension_only(tmp_path):
    html_path = make_html(tmp_path / "sample.html")
    docx_path = make_docx(tmp_path / "sample.docx")
    assert detect_mime(html_path.read_bytes()[:4096], html_path.name) == "text/html"
    assert detect_mime(docx_path.read_bytes()[:4096], docx_path.name) == DOCX_MIME
    assert detect_mime(b"not html", "fake.html") is None
    assert detect_mime(b"not a zip", "fake.docx") is None


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_write_read_remove_and_fingerprint_are_common(kind, tmp_path):
    if kind == "html":
        source = make_html(tmp_path / "source.html")
        adapter = HtmlAdapter()
        labeled = tmp_path / "labeled.html"
        clean = tmp_path / "clean.html"
        expected_kind = "body"
    else:
        source = make_docx(tmp_path / "source.docx")
        adapter = DocxAdapter()
        labeled = tmp_path / "labeled.docx"
        clean = tmp_path / "clean.docx"
        expected_kind = "package"

    before = adapter.content_fingerprint(source)
    adapter.write_metadata(source, labeled, VALID_AIGC)
    records = adapter.detect_existing(labeled)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC
    assert adapter.content_fingerprint(labeled) == before
    assert before[1] == expected_kind
    assert adapter.media_integrity_check(source, labeled).passed is True

    adapter.remove_aigc(labeled, clean)
    assert adapter.detect_existing(clean) == []
    assert adapter.content_fingerprint(clean) == before
    assert adapter.media_integrity_check(source, clean).passed is True


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_document_inspector_uses_common_report(kind, tmp_path):
    if kind == "html":
        source = make_html(tmp_path / "source.html")
        labeled = tmp_path / "labeled.html"
        adapter = HtmlAdapter()
        mime = "text/html"
    else:
        source = make_docx(tmp_path / "source.docx")
        labeled = tmp_path / "labeled.docx"
        adapter = DocxAdapter()
        mime = DOCX_MIME
    adapter.write_metadata(source, labeled, VALID_AIGC)

    report = DocumentComplianceInspector(mime).inspect(labeled)

    assert report["conclusion"] == "compliant"
    assert report["record_count"] == 1
    assert report["detected_mime_type"] == mime
    assert report["document"]["format"] == kind


@pytest.fixture
def client(tmp_path):
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    app = create_app(
        settings=settings,
        storage_root=str(tmp_path / "storage"),
        db_path=str(tmp_path / "jobs.db"),
    )
    with TestClient(app) as value:
        yield value


def _request(policy="reject", obj=None) -> str:
    return json.dumps({
        "standard": "GB45438-2025",
        "modality": "text",
        "existing_metadata_policy": policy,
        "AIGC": obj or VALID_AIGC,
    })


def _wait(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        result = client.get(f"/api/v1/metadata-label-jobs/{job_id}").json()
        if result["status"] in {"succeeded", "failed"}:
            return result
        time.sleep(0.05)
    raise AssertionError("任务未在限定时间内结束")


def _wait_repair(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        result = client.get(f"/api/v1/metadata-repair-jobs/{job_id}").json()
        if result["status"] in {"succeeded", "failed"}:
            return result
        time.sleep(0.05)
    raise AssertionError("修复任务未在限定时间内结束")


def test_health_exposes_both_new_text_capabilities(client):
    capabilities = client.get("/api/v1/health").json()["capabilities"]
    assert capabilities["text/html"] is True
    assert capabilities[DOCX_MIME] is True


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_async_label_and_compliance_api(kind, tmp_path, client):
    if kind == "html":
        source = make_html(tmp_path / "source.html")
        filename = "source.html"
    else:
        source = make_docx(tmp_path / "source.docx")
        filename = "source.docx"

    created = client.post(
        "/api/v1/metadata-label-jobs",
        files={"file": (filename, source.read_bytes(), "application/octet-stream")},
        data={"request": _request()},
    )
    assert created.status_code == 202, created.text
    job = _wait(client, created.json()["job_id"])
    assert job["status"] == "succeeded", job
    assert job["validation"]["single_aigc_record"] is True
    output = client.get(job["links"]["output"])
    assert output.status_code == 200

    report = client.post(
        "/api/v1/compliance-inspect",
        files={"file": (filename, output.content, "application/octet-stream")},
    )
    assert report.status_code == 200, report.text
    assert report.json()["conclusion"] == "compliant"
    assert report.json()["record_count"] == 1


def _incomplete_aigc() -> dict:
    value = dict(VALID_AIGC)
    value.pop("ReservedCode2")
    return value


def make_incomplete_html(path: Path) -> Path:
    raw = json.dumps({"AIGC": _incomplete_aigc()}, ensure_ascii=False,
                     separators=(",", ":"))
    meta = f'<meta name="AIGC" content="{html.escape(raw, quote=True)}">'
    path.write_text(
        f"<!doctype html><html><head>{meta}</head><body>正文</body></html>",
        encoding="utf-8",
    )
    return path


def add_c2pa_reference(path: Path) -> Path:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("</head>", '<link rel="c2pa-manifest" href="claim.c2pa"></head>'),
        encoding="utf-8",
        newline="",
    )
    return path


def make_incomplete_docx(path: Path, tmp_path: Path) -> Path:
    clean = make_docx(tmp_path / "clean.docx")
    labeled = tmp_path / "complete.docx"
    DocxAdapter().write_metadata(clean, labeled, VALID_AIGC)
    with zipfile.ZipFile(labeled, "r") as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    part = next(name for name in entries if name.startswith("customXml/aigcMetadata")
                and name.endswith(".xml") and "Props" not in name)
    needle = b',"ReservedCode2":""'
    assert needle in entries[part]
    entries[part] = entries[part].replace(needle, b"", 1)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def add_docx_signature(path: Path) -> Path:
    with zipfile.ZipFile(path, "r") as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries["_xmlsignatures/sig1.xml"] = b"<Signature/>"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_shared_repair_plan_and_execution(kind, tmp_path, client):
    if kind == "html":
        source = make_incomplete_html(tmp_path / "incomplete.html")
    else:
        source = make_incomplete_docx(tmp_path / "incomplete.docx", tmp_path)

    planned = client.post(
        "/api/v1/metadata-repair-plans",
        files={"file": (source.name, source.read_bytes(), "application/octet-stream")},
    )
    assert planned.status_code == 201, planned.text
    plan = planned.json()
    assert plan["inspection"]["conclusion"] == "noncompliant"
    assert plan["inspection"]["cross_reader"]["status"] == "not_applicable"
    assert plan["repair_plan"]["executable"] is True, plan["repair_plan"]

    started = client.post("/api/v1/metadata-repair-jobs", json={
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "confirmed": True,
        "operator_label": "HTML-DOCX 公共接口回归",
    })
    assert started.status_code == 202, started.text
    job = _wait_repair(client, started.json()["job_id"])
    assert job["status"] == "succeeded", job
    assert job["validation"]["post_repair_conclusion"] == "compliant"
    assert job["validation"]["single_aigc_record"] is True
    assert job["validation"]["content_fingerprint_unchanged"] is True


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_existing_label_reject_and_explicit_replace(kind, tmp_path, client):
    if kind == "html":
        source = make_html(tmp_path / "source.html")
        labeled = tmp_path / "labeled.html"
        adapter = HtmlAdapter()
    else:
        source = make_docx(tmp_path / "source.docx")
        labeled = tmp_path / "labeled.docx"
        adapter = DocxAdapter()
    adapter.write_metadata(source, labeled, VALID_AIGC)

    rejected = client.post(
        "/api/v1/metadata-label-jobs",
        files={"file": (labeled.name, labeled.read_bytes(), "application/octet-stream")},
        data={"request": _request("reject")},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "AIGC_METADATA_EXISTS"

    replacement = dict(VALID_AIGC)
    replacement.update({
        "Label": "2",
        "ProduceID": "0198F21A-6F28-7000-A102-000000000002",
        "PropagateID": "0198F21A-6F28-7000-A102-000000000002",
    })
    started = client.post(
        "/api/v1/metadata-label-jobs",
        files={"file": (labeled.name, labeled.read_bytes(), "application/octet-stream")},
        data={"request": _request("replace", replacement)},
    )
    assert started.status_code == 202, started.text
    job = _wait(client, started.json()["job_id"])
    assert job["status"] == "succeeded", job
    assert job["embedded_metadata"]["AIGC"] == replacement
    assert job["validation"]["single_aigc_record"] is True


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_signed_or_c2pa_protected_document_is_readonly(kind, tmp_path, client):
    if kind == "html":
        source = add_c2pa_reference(make_incomplete_html(tmp_path / "protected.html"))
        expected_code = "HTML_C2PA_MANIFEST_PRESENT"
    else:
        source = add_docx_signature(
            make_incomplete_docx(tmp_path / "protected.docx", tmp_path)
        )
        expected_code = "DOCX_SIGNED_PRESENT"

    inspected = client.post(
        "/api/v1/compliance-inspect",
        files={"file": (source.name, source.read_bytes(), "application/octet-stream")},
    )
    assert inspected.status_code == 200, inspected.text
    assert any(item["code"] == expected_code for item in inspected.json()["issues"])

    planned = client.post(
        "/api/v1/metadata-repair-plans",
        files={"file": (source.name, source.read_bytes(), "application/octet-stream")},
    )
    assert planned.status_code == 201, planned.text
    plan = planned.json()["repair_plan"]
    assert plan["executable"] is False
    assert plan["repairability"] == "forbidden"

    labeling = client.post(
        "/api/v1/metadata-label-jobs",
        files={"file": (source.name, source.read_bytes(), "application/octet-stream")},
        data={"request": _request("replace")},
    )
    assert labeling.status_code == 415
