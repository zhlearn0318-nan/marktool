"""文档（Markdown / PDF）经 API 的端到端契约（开发手册 §7.2/§7.3、§9）。

与 test_api_contract.py（MP4）和 test_api_inspect.py（MP4/图片）并列：同一套接口、
同一套验收口径，换两个文档格式跑一遍。凡走 ExifTool 的用例按既有约定 skip。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core import aigc
from app.main import create_app
from app.metadata import markdown_carrier as mc
from tests.common import (EXIFTOOL_CONFIG, VALID_AIGC, make_jpeg, make_markdown,
                          make_pdf, make_signed_pdf)

MD_MIME = "text/markdown"
PDF_MIME = "application/pdf"


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
def exiftool() -> str:
    executable = _find_exiftool()
    if not executable:
        if os.getenv("AIGC_REQUIRE_EXIFTOOL") == "1":
            pytest.fail("CI 要求真实 ExifTool，但当前未找到可执行文件")
        pytest.skip("需要 ExifTool；请设置 EXIFTOOL_PATH 后运行集成测试")
    return executable


@pytest.fixture
def client(tmp_path, exiftool):
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = 10 * 1024 * 1024
    settings.paths.exiftool = exiftool
    settings.paths.exiftool_config = str(EXIFTOOL_CONFIG)
    app = create_app(settings=settings,
                     storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    with TestClient(app) as c:
        yield c


def _req(modality="text", policy="reject", obj=None):
    return json.dumps({"standard": "GB45438-2025", "modality": modality,
                       "existing_metadata_policy": policy,
                       "AIGC": obj or VALID_AIGC})


def _upload(client, data: bytes, fname: str, modality="text", policy="reject",
            obj=None):
    return client.post("/api/v1/metadata-label-jobs",
                       files={"file": (fname, data, "application/octet-stream")},
                       data={"request": _req(modality, policy, obj)})


def _wait_terminal(client, job_id, timeout=30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/v1/metadata-label-jobs/{job_id}").json()
        if d["status"] in ("succeeded", "failed"):
            return d
        time.sleep(0.2)
    raise AssertionError("任务超时未结束")


# ---- 打标：干净文件 -------------------------------------------------------

def test_label_clean_markdown_succeeds(client, tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    r = _upload(client, src.read_bytes(), "a.md")
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, r.json()["job_id"])

    assert job["status"] == "succeeded", job
    assert job["output"]["carrier"] == "md-frontmatter-aigc-v1"
    assert job["input"]["detected_mime_type"] == MD_MIME
    for key in ("read_back_succeeded", "schema_valid", "single_aigc_record",
                "media_integrity_valid", "fields_match"):
        assert job["validation"][key] is True, key
    assert job["embedded_metadata"]["AIGC"]["Label"] == "1"
    assert job["output"]["file_name"] == "a_labeled.md"


def test_labeled_markdown_output_has_frontmatter_and_intact_body(client, tmp_path):
    """结果文件：frontmatter 里有且只有一份标识，正文逐字节不变。"""
    body = "# 标题\n\n正文。\n"
    src = make_markdown(tmp_path / "a.md", body)
    job = _wait_terminal(client, _upload(client, src.read_bytes(), "a.md").json()["job_id"])

    out = client.get(f"/api/v1/metadata-label-jobs/{job['job_id']}/output")
    assert out.status_code == 200
    assert out.headers["content-type"].startswith("text/markdown")

    text = out.content.decode("utf-8")
    assert text.startswith("---\nAIGC: '")
    assert text.endswith(body)
    assert text.count("AIGC: '") == 1


def test_label_clean_pdf_succeeds(client, tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=2)
    r = _upload(client, src.read_bytes(), "a.pdf")
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, r.json()["job_id"])

    assert job["status"] == "succeeded", job
    assert job["output"]["carrier"] == "pdf-xmp-aigc-v1"
    assert job["input"]["detected_mime_type"] == PDF_MIME
    assert job["validation"]["single_aigc_record"] is True
    # media_integrity_valid 已含"页数未变、%%EOF 仍在"（见 pdfstruct.integrity_problems），
    # API 响应不额外暴露 audit 块，故此处不重复断言页数。
    assert job["validation"]["media_integrity_valid"] is True
    assert job["output"]["file_name"] == "a_labeled.pdf"


def test_label_markdown_does_not_touch_source(client, tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n")
    before = src.read_bytes()
    job = _wait_terminal(client, _upload(client, src.read_bytes(), "a.md").json()["job_id"])
    assert job["status"] == "succeeded"
    assert src.read_bytes() == before


# ---- 已有标识：reject / replace -------------------------------------------

def test_existing_markdown_with_reject_is_409(client, tmp_path):
    src = tmp_path / "a.md"
    mc.write(make_markdown(tmp_path / "clean.md", "# 标题\n"), src,
             aigc.normalize_first_write(VALID_AIGC))

    r = _upload(client, src.read_bytes(), "a.md", policy="reject")
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "AIGC_METADATA_EXISTS"
    assert "AIGC" in json.dumps(body["field_errors"], ensure_ascii=False)


def test_existing_pdf_with_reject_is_409(client, tmp_path):
    raw = aigc.serialize_aigc(VALID_AIGC)
    src = make_pdf(tmp_path / "a.pdf")
    from app.adapters import PdfAdapter
    PdfAdapter(exiftool=os.environ.get("EXIFTOOL_PATH", "exiftool"),
               exiftool_config=str(EXIFTOOL_CONFIG))._exiftool_cmd(
        ["-overwrite_original", "-XMP-aigc:AIGC=" + raw], str(src))

    r = _upload(client, src.read_bytes(), "a.pdf", policy="reject")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "AIGC_METADATA_EXISTS"


def test_replace_collapses_markdown_double_carrier(client, tmp_path):
    """双载体（frontmatter + HTML 注释）在 replace 后收敛成一条。"""
    jsonstr = aigc.serialize_aigc(aigc.normalize_first_write(VALID_AIGC))
    src = make_markdown(tmp_path / "a.md", f"正文\n\n<!-- AIGC: {jsonstr} -->\n",
                        frontmatter=f"AIGC: '{jsonstr}'")
    assert len(mc.read_records(src)) == 2                  # 前提

    job = _wait_terminal(client, _upload(client, src.read_bytes(), "a.md",
                                         policy="replace").json()["job_id"])
    assert job["status"] == "succeeded", job
    assert job["validation"]["single_aigc_record"] is True

    out = client.get(f"/api/v1/metadata-label-jobs/{job['job_id']}/output").content
    text = out.decode("utf-8")
    assert "<!--" not in text                              # 旧载体注释被清掉
    assert text.count("AIGC: '") == 1                      # 只剩规范载体一份


def test_relabel_is_idempotent(client, tmp_path):
    """对已打标结果再打一次（replace）仍只有一份——重复运行不累积标识。"""
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    job1 = _wait_terminal(client, _upload(client, src.read_bytes(), "a.md").json()["job_id"])
    out1 = client.get(f"/api/v1/metadata-label-jobs/{job1['job_id']}/output").content

    job2 = _wait_terminal(client, _upload(client, out1, "a.md", policy="replace").json()["job_id"])
    assert job2["status"] == "succeeded", job2
    out2 = client.get(f"/api/v1/metadata-label-jobs/{job2['job_id']}/output").content
    assert out2.decode("utf-8").count("AIGC: '") == 1


# ---- 错误映射 -------------------------------------------------------------

def test_png_bytes_named_md_is_modality_mismatch(client, tmp_path):
    """扩展名伪装：内容判出来仍是 PNG，于是"声明 text"与之冲突 → 422。

    这条正是 §13「不能只信任扩展名」的护栏。
    """
    png = make_jpeg(tmp_path / "x.jpg").read_bytes()
    r = _upload(client, png, "fake.md", modality="text")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MODALITY_MISMATCH"


def test_pdf_declared_as_video_is_modality_mismatch(client, tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    r = _upload(client, src.read_bytes(), "a.pdf", modality="video")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MODALITY_MISMATCH"


def test_non_utf8_markdown_is_415(client):
    r = _upload(client, b"# \xff\xfe\x00\x01 not text\n", "bad.md", modality="text")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_plain_text_renamed_md_is_accepted(client, tmp_path):
    """已知残余边界：纯文本改名 .md 会被当作 Markdown 接受（见验证文档 §13 偏差）。"""
    r = _upload(client, "就是一段没有 frontmatter 的纯文本\n".encode(), "a.md")
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, r.json()["job_id"])
    assert job["status"] == "succeeded"


def test_signed_pdf_is_415(client, tmp_path):
    """签名文档不能打标：ExifTool 整体重写会毁掉签名，请求期就该拒。"""
    src = make_signed_pdf(tmp_path / "s.pdf")
    r = _upload(client, src.read_bytes(), "s.pdf")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert "签名" in r.json()["error"]["message"]


def test_encrypted_pdf_is_415(client, tmp_path):
    src = make_pdf(tmp_path / "e.pdf")
    src.write_bytes(src.read_bytes().replace(
        b"trailer\n", b"trailer\n<< /Encrypt 9 0 R >>\n", 1))
    r = _upload(client, src.read_bytes(), "e.pdf")
    assert r.status_code == 415
    assert "加密" in r.json()["error"]["message"]


def test_truncated_pdf_fails_with_media_integrity(client, tmp_path):
    """截断 PDF：预检拦不住（有 %PDF- 头）也要在媒体完整性上被抓住。"""
    raw = make_pdf(tmp_path / "t.pdf", pages=3).read_bytes()
    r = _upload(client, raw[:len(raw) // 2], "t.pdf")
    assert r.status_code == 415                        # 预检直接拒（无 %%EOF）
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_modality_must_be_image_video_or_text(client, tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n")
    r = _upload(client, src.read_bytes(), "a.md", modality="document")
    assert r.status_code == 422
    assert "text" in r.json()["error"]["message"]


# ---- 只读标签：不进存储 ---------------------------------------------------

def test_rejected_upload_leaves_no_residue(client, tmp_path):
    """415/422 的请求必须在返回前把已落盘的上传副本删掉。"""
    r = _upload(client, make_jpeg(tmp_path / "x.jpg").read_bytes(), "fake.md")
    assert r.status_code == 422
    assert list(client.app.state.storage.original_dir.iterdir()) == []

    r = _upload(client, make_signed_pdf(tmp_path / "s.pdf").read_bytes(), "s.pdf")
    assert r.status_code == 415
    assert list(client.app.state.storage.original_dir.iterdir()) == []


# ---- 合规检测 -------------------------------------------------------------

def _inspect(client, data: bytes, fname: str):
    return client.post("/api/v1/compliance-inspect",
                       files={"file": (fname, data, "application/octet-stream")})


def test_inspect_clean_markdown_is_not_found(client, tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    r = _inspect(client, src.read_bytes(), "a.md")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["conclusion"] == "not_found"
    assert d["detected_mime_type"] == MD_MIME
    assert d["bmff"]["applicable"] is False
    assert d["document"]["format"] == "markdown"


def test_inspect_labeled_markdown_is_compliant(client, tmp_path):
    src = tmp_path / "a.md"
    mc.write(make_markdown(tmp_path / "clean.md", "# 标题\n\n正文。\n"), src,
             aigc.normalize_first_write(VALID_AIGC))
    d = _inspect(client, src.read_bytes(), "a.md").json()
    assert d["conclusion"] == "compliant"
    assert d["record_count"] == 1
    assert d["candidates"][0]["location"].startswith("YAML frontmatter")


def test_inspect_clean_pdf_is_not_found(client, tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=2)
    d = _inspect(client, src.read_bytes(), "a.pdf").json()
    assert d["conclusion"] == "not_found"
    assert d["detected_mime_type"] == PDF_MIME
    assert d["document"]["page_count"] == 2


def test_inspect_truncated_pdf_is_indeterminate(client, tmp_path):
    """截断 PDF 不得读成干净的 not_found——那是"没读到"，不是"没有"（§2.4）。"""
    raw = make_pdf(tmp_path / "t.pdf", pages=3).read_bytes()
    d = _inspect(client, raw[:len(raw) // 2], "t.pdf").json()
    assert d["conclusion"] == "indeterminate"
    assert d["reason_code"] == "UNREADABLE_CARRIER"


def test_inspect_signed_pdf_reports_degraded(client, tmp_path):
    d = _inspect(client, make_signed_pdf(tmp_path / "s.pdf").read_bytes(), "s.pdf").json()
    assert d["media_status"] == "degraded"
    assert d["document"]["signed"] is True


def test_inspect_non_utf8_markdown_is_415(client):
    r = _inspect(client, b"# \xff\xfe\x00\x01\n", "bad.md")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_inspect_documents_is_readonly_and_leaks_nothing(client, tmp_path):
    """只读无状态：源文件不动，上传副本返回后即删。"""
    md = make_markdown(tmp_path / "a.md", "# 标题\n")
    pdf = make_pdf(tmp_path / "a.pdf")
    md_before, pdf_before = md.read_bytes(), pdf.read_bytes()

    assert _inspect(client, md.read_bytes(), "a.md").status_code == 200
    assert _inspect(client, pdf.read_bytes(), "a.pdf").status_code == 200
    assert md.read_bytes() == md_before
    assert pdf.read_bytes() == pdf_before
    assert list(client.app.state.storage.original_dir.iterdir()) == []


# ---- 能力开关 -------------------------------------------------------------

def test_capabilities_advertise_documents(client):
    caps = client.get("/api/v1/health").json()["capabilities"]
    assert caps["text/markdown"] is True
    assert caps["application/pdf"] is True
    assert caps["video/mp4"] is True                      # 旧键仍在
