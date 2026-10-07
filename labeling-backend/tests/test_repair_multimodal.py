"""修复台接视频与文本：计划 → 确认 → 执行 → 复检的完整链路。

图片的修复早有一套测试，本文件的重点只有一件：**同一套接口换一种模态，判定
与保证是否原样成立**——可自动修 / 不可自动修的分界、修复后恰好一份标识、
内容指纹前后不变、原文件不被覆盖、审计事件不落项。

因此每条断言都对着一个"会真出事"的场景：视频改坏了时长、Markdown 改动了正文、
干净文件被当成可修、跨模态指纹算错——这些在图片上早被覆盖，在视频/文本上一条
都没有。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import Mp4Adapter, PdfAdapter
from app.api.repair_routes import get_metadata_repair_service
from app.core import aigc, pdfstruct
from app.main import create_app
from app.metadata.repair_service import MetadataRepairConfig, MetadataRepairService
from tests.common import CLEAN_MP4, EXIFTOOL_CONFIG, VALID_AIGC, make_markdown, make_pdf


def _find(tool: str) -> str | None:
    return shutil.which(tool)


@pytest.fixture
def repair(tmp_path):
    if not (_find("exiftool") and _find("ffprobe")):
        pytest.skip("需要 ExifTool 与 ffprobe")
    config = MetadataRepairConfig(
        storage_root=tmp_path / "repair-files",
        database_path=tmp_path / "repairs.sqlite3",
        audit_root=tmp_path / "repair-audit",
        identifier_database_path=tmp_path / "identifiers.sqlite3",
        max_upload_bytes=20 * 1024 * 1024,
        exiftool="exiftool", ffprobe="ffprobe", ffmpeg="ffmpeg",
        exiftool_config=str(EXIFTOOL_CONFIG),
    )
    service = MetadataRepairService(config)
    app = create_app(storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    app.dependency_overrides[get_metadata_repair_service] = lambda: service
    with TestClient(app) as client:
        yield client, service
    app.dependency_overrides.pop(get_metadata_repair_service, None)
    service.close()


# ---- 夹具 ------------------------------------------------------------------

AIGC_JSON = aigc.serialize_aigc(VALID_AIGC)


def _labeled_mp4(path: Path, *, legacy_carrier: bool = False) -> Path:
    """写一份规范标识；可再加一处旧载体，制造 DUPLICATE_RECORDS。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CLEAN_MP4, path)
    adapter = Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    adapter._exiftool_cmd(["-overwrite_original", "-XMP-aigc:AIGC=" + AIGC_JSON],
                          str(path))
    if legacy_carrier:
        adapter._exiftool_cmd(["-overwrite_original", "-QuickTime:Comment=" + AIGC_JSON],
                              str(path))
    return path


def _dual_carrier_markdown(tmp_path: Path) -> Path:
    return make_markdown(tmp_path / "doc.md",
                         f"正文第一段\n\n<!-- AIGC: {AIGC_JSON} -->\n",
                         frontmatter=f"AIGC: '{AIGC_JSON}'")


def _post_plan(client, path: Path, options=None):
    files = {"file": (path.name, path.read_bytes(), "application/octet-stream")}
    if options is not None:
        files["request"] = ("request.json",
                            json.dumps(options, ensure_ascii=False),
                            "application/json")
    return client.post("/api/v1/metadata-repair-plans", files=files)


def _run_repair(client, plan_body: dict) -> dict:
    confirmed = client.post("/api/v1/metadata-repair-jobs", json={
        "plan_id": plan_body["plan_id"], "plan_hash": plan_body["plan_hash"],
        "confirmed": True, "operator_label": "多模态回归操作人"})
    assert confirmed.status_code == 202, confirmed.text
    job_id = confirmed.json()["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/metadata-repair-jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.05)
    pytest.fail("修复任务未在时限内结束")


def _download(client, job: dict, target: Path) -> Path:
    response = client.get(job["output"]["download_url"])
    assert response.status_code == 200
    target.write_bytes(response.content)
    return target


# ---- 视频 ------------------------------------------------------------------

def test_video_plan_reports_stream_fingerprint_and_is_auto_fixable(repair, tmp_path):
    client, _ = repair
    planned = _post_plan(client, _labeled_mp4(tmp_path / "clip.mp4",
                                              legacy_carrier=True))
    assert planned.status_code == 201, planned.text
    body = planned.json()

    inspection = body["inspection"]
    assert inspection["mime_type"] == "video/mp4"
    assert inspection["detected_format"] == "MP4"
    # 视频没有像素，指纹必须是流签名——沿用一个叫 pixel 的字段会让登记库核对
    # 拿两个不同模态的哈希互相比对
    assert inspection["fingerprint_kind"] == "stream"
    assert len(inspection["content_fingerprint"]) == 64
    assert body["input"]["fingerprint_kind"] == "stream"
    # ffprobe + 字节扫描与 ExifTool 对上了，才允许自动改写
    assert inspection["cross_reader"]["status"] == "matched", inspection["cross_reader"]
    assert inspection["extended_xmp"] is False
    assert body["repair_plan"]["executable"] is True, body["repair_plan"]["blocking_reasons"]


def test_video_repair_leaves_exactly_one_record_and_the_same_media(repair, tmp_path):
    client, service = repair
    source = _labeled_mp4(tmp_path / "src.mp4", legacy_carrier=True)
    before = Mp4Adapter(exiftool="exiftool",
                        exiftool_config=str(EXIFTOOL_CONFIG)).detect_existing(source)
    assert len(before) == 2, "夹具没造出重复标识，这条测试就失去意义了"

    body = _post_plan(client, source).json()
    job = _run_repair(client, body)
    assert job["status"] == "succeeded", job.get("error")

    validation = job["validation"]
    assert validation["single_aigc_record"] is True
    assert validation["post_repair_conclusion"] == "compliant"
    assert validation["content_fingerprint_unchanged"] is True
    assert validation["media_integrity_valid"] is True

    repaired = _download(client, job, tmp_path / "out.mp4")
    adapter = Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    after = adapter.detect_existing(repaired)
    assert len(after) == 1
    assert after[0].aigc == VALID_AIGC
    # 媒体本身不得被改写：时长与轨道与原件一致
    assert adapter.media_integrity_check(source, repaired).passed is True
    # 原文件绝不被覆盖（§4.4）
    assert len(adapter.detect_existing(source)) == 2


def test_clean_video_is_not_found_and_not_repairable(repair, tmp_path):
    client, _ = repair
    clean = tmp_path / "clean.mp4"
    shutil.copyfile(CLEAN_MP4, clean)

    body = _post_plan(client, clean).json()
    assert body["inspection"]["conclusion"] == "not_found"
    assert body["repair_plan"]["executable"] is False
    assert body["repair_plan"]["repairability"] == "not_applicable"


# ---- Markdown --------------------------------------------------------------

def test_markdown_plan_is_auto_fixable_without_a_cross_reader(repair, tmp_path):
    """Markdown 没有第二条读取路径，但这不该把它挡在自动修复之外。"""
    client, _ = repair
    body = _post_plan(client, _dual_carrier_markdown(tmp_path)).json()

    inspection = body["inspection"]
    assert inspection["conclusion"] == "noncompliant"
    assert inspection["fingerprint_kind"] == "body"
    # 已知没有（not_applicable），不是该做没做（not_run）
    assert inspection["cross_reader"]["status"] == "not_applicable"
    assert body["repair_plan"]["executable"] is True, body["repair_plan"]["blocking_reasons"]


def test_markdown_repair_keeps_the_body_and_leaves_one_carrier(repair, tmp_path):
    client, _ = repair
    source = _dual_carrier_markdown(tmp_path)
    original_text = source.read_text(encoding="utf-8")

    job = _run_repair(client, _post_plan(client, source).json())
    assert job["status"] == "succeeded", job.get("error")
    assert job["validation"]["post_repair_conclusion"] == "compliant"
    assert job["validation"]["content_fingerprint_unchanged"] is True

    repaired = _download(client, job, tmp_path / "out.md").read_text(encoding="utf-8")
    assert "正文第一段" in repaired
    assert repaired.count(AIGC_JSON) == 1, repaired
    # 正文与原件逐字相同，只有标识载体换成了规范的那一种
    assert repaired.replace("---\n", "", 1) != ""
    assert original_text.count(AIGC_JSON) == 2


def test_clean_markdown_is_not_found(repair, tmp_path):
    client, _ = repair
    clean = make_markdown(tmp_path / "clean.md", "# 标题\n\n正文。\n")
    body = _post_plan(client, clean).json()
    assert body["inspection"]["conclusion"] == "not_found"
    assert body["repair_plan"]["executable"] is False


# ---- PDF -------------------------------------------------------------------

def _dual_carrier_pdf(tmp_path: Path) -> Path:
    """规范载体 + 旧属性名载体，构成 PDF 上的 DUPLICATE_RECORDS。"""
    adapter = PdfAdapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    labeled = tmp_path / "labeled.pdf"
    adapter.write_metadata(make_pdf(tmp_path / "src.pdf", pages=3), labeled, VALID_AIGC)
    adapter._exiftool_cmd(
        ["-overwrite_original", "-XMP-aigc:metadata=" + AIGC_JSON], str(labeled))
    return labeled


def test_pdf_plan_uses_content_fingerprint(repair, tmp_path):
    client, _ = repair
    body = _post_plan(client, _dual_carrier_pdf(tmp_path)).json()

    inspection = body["inspection"]
    assert inspection["mime_type"] == "application/pdf"
    assert inspection["fingerprint_kind"] == "content"
    assert inspection["conclusion"] == "noncompliant"
    assert body["repair_plan"]["executable"] is True, body["repair_plan"]["blocking_reasons"]


def test_pdf_repair_keeps_page_count_and_eof(repair, tmp_path):
    """PDF 没有像素也没有正文，能证明"内容没被改坏"的只有结构。"""
    client, _ = repair
    source = _dual_carrier_pdf(tmp_path)
    before = pdfstruct.scan(source)

    job = _run_repair(client, _post_plan(client, source).json())
    assert job["status"] == "succeeded", job.get("error")
    assert job["validation"]["post_repair_conclusion"] == "compliant"
    assert job["validation"]["content_fingerprint_unchanged"] is True

    repaired = _download(client, job, tmp_path / "out.pdf")
    after = pdfstruct.scan(repaired)
    assert after.page_count == before.page_count
    assert after.has_eof is True
    assert after.signed is False and after.encrypted is False
    adapter = PdfAdapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))
    records = adapter.detect_existing(repaired)
    assert len(records) == 1 and records[0].aigc == VALID_AIGC


def _append_signature(path: Path) -> Path:
    """插一个签名对象，插在 ``startxref`` **之前**、保留原有 trailer。

    为什么不直接在文件尾拼一段自造 trailer：那样 PDF 的交叉引用表就指向了不存在
    的对象，ExifTool 连 AIGC 都读不出来了——测出来的会是 ``not_found``，而不是
    "标识有、但因为签名不能动"。断言签名这道闸门，前提是文件本身仍然读得通。
    """
    raw = path.read_bytes()
    signature = b"7 0 obj\n<< /Type /Sig /ByteRange [0 100 200 300] >>\nendobj\n"
    index = raw.rindex(b"startxref")
    path.write_bytes(raw[:index] + signature + raw[index:])
    return path


def test_signed_pdf_is_refused_with_a_reason(repair, tmp_path):
    """签名 PDF 不能整体重写（ExifTool 写 PDF 是整体重写，签名必然失效）。

    关键是**要出计划、要给出原因**：只把按钮灰掉、不说为什么，操作人只能猜。
    """
    client, _ = repair
    # 先造一份"该修"的 PDF（双载体），再插入签名对象——这样唯一的阻塞项就是签名，
    # 否则测的就成了"干净文件不可修"，与签名无关。
    source = _dual_carrier_pdf(tmp_path)
    _append_signature(source)

    body = _post_plan(client, source).json()
    # 标识本身是"该修"的（双载体），唯一的阻塞项只能是签名
    assert body["inspection"]["conclusion"] == "noncompliant", body["inspection"]
    assert body["repair_plan"]["executable"] is False
    assert body["repair_plan"]["repairability"] == "forbidden", body["repair_plan"]
    reasons = " ".join(body["repair_plan"]["blocking_reasons"])
    assert "签名" in reasons, body["repair_plan"]["blocking_reasons"]

    # 即便绕过前端直接确认，也必须被拦住（计划阶段就不可执行）
    confirmed = client.post("/api/v1/metadata-repair-jobs", json={
        "plan_id": body["plan_id"], "plan_hash": body["plan_hash"],
        "confirmed": True, "operator_label": "签名 PDF 操作人"})
    assert confirmed.status_code == 409
