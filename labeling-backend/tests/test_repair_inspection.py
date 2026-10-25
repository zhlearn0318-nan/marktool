"""转换层保真校验：统一检测报告 → 规划器入参（app/metadata/repair_inspection.py）。

这一层存在的唯一理由是"让视频和文本得到与图片相同的修复判定"。所以最有说服力的
测试不是"函数不报错"，而是**拿图片当基准尺**：图片本来就产出规划器要的那个对象，
把它的报告绕一圈翻译回来，逐项比对规划器真正会读的字段——任何一处失真都会让
视频/文本的修复结论与图片分叉，而分叉正是这一层要消灭的东西。
"""
from __future__ import annotations

import json

import pytest

from app.core.image_bridge import to_report
from app.metadata import markdown_carrier
from app.metadata.compliance import (ComplianceConclusion, CrossReaderResult,
                                     MetadataComplianceInspector,
                                     Repairability)
from app.metadata.document_inspector import DocumentComplianceInspector
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.repair_inspection import from_report, verify_source
from app.metadata.repair_planner import RepairPlanner
from tests.common import VALID_AIGC, make_markdown
from tests.fixtures import duplicate_xmp, make_png, make_png_with_xmp_packets

MARKDOWN_MIME = "text/markdown"
AIGC_JSON = json.dumps({"AIGC": VALID_AIGC}, ensure_ascii=False)


def _dual_carrier_markdown(tmp_path, name: str = "dual.md"):
    """双载体 Markdown：frontmatter 键 + HTML 注释，检测器会报 DUPLICATE_RECORDS。"""
    return make_markdown(tmp_path / name, f"正文\n\n<!-- AIGC: {AIGC_JSON} -->\n",
                         frontmatter=f"AIGC: '{AIGC_JSON}'")


# ---- 保真：图片报告绕一圈翻译回来 -----------------------------------------

def test_image_report_survives_translation(tmp_path):
    """逐项比对：结论 / 可修性 / C2PA / Extended XMP / 记录数 / 七字段文档。

    图片本来就不经过转换层（它产出的就是规划器要的对象），所以这里喂给转换层的
    是图片自己的记录——两种 ``AIGCRecord`` 的字段名完全不同，能对上就说明
    转换层没有丢掉规划器会读的东西。
    """
    second = {**VALID_AIGC, "ProduceID": "PRD-20260610-0002",
              "PropagateID": "PRD-20260610-0002"}
    image = make_png_with_xmp_packets(
        tmp_path / "dual.png", [duplicate_xmp(VALID_AIGC, second)])

    original = MetadataComplianceInspector().inspect(image)
    assert original.record_count == 2, "夹具没造出重复记录，这条测试就失去意义了"
    report = to_report(image, original)

    translated = from_report(
        report, original.raw_records,
        content_fingerprint=original.content_fingerprint, fingerprint_kind="pixel")

    assert translated.conclusion is original.conclusion
    assert translated.repairability is original.repairability
    assert translated.c2pa_presence.status is original.c2pa_presence.status
    assert translated.extended_xmp is original.extended_xmp
    assert translated.mime_type == original.mime_type
    assert translated.record_count == original.record_count
    assert [r.document for r in translated.raw_records] == \
        [r.document for r in original.raw_records]
    assert [r.parse_error for r in translated.raw_records] == \
        [r.parse_error for r in original.raw_records]
    # 规划器要按值判"两份是否真的冲突"，原始 JSON 必须一字不动地过来
    assert [r.raw_value for r in translated.raw_records] == \
        [r.raw_value for r in original.raw_records]


def test_clean_image_translates_to_not_found(tmp_path):
    clean = make_png(tmp_path / "clean.png")
    original = MetadataComplianceInspector().inspect(clean)
    report = to_report(clean, original)

    translated = from_report(report, [], content_fingerprint="x",
                             fingerprint_kind="pixel")
    assert translated.conclusion is ComplianceConclusion.NOT_FOUND
    assert translated.raw_records == []
    assert translated.record_count == 0


# ---- Markdown：报告 → 规划器真的能规划 -------------------------------------

def _markdown_report(path):
    report = DocumentComplianceInspector(MARKDOWN_MIME).inspect(path)
    return report, markdown_carrier.read_records(path)


def test_markdown_document_report_reaches_the_planner(tmp_path):
    """双载体 Markdown：报告 → 转换层 → 规划器，结论与计划都必须出得来。"""
    dual = _dual_carrier_markdown(tmp_path)
    report, records = _markdown_report(dual)
    assert report["conclusion"] == "noncompliant"
    assert report["reason_code"] == "DUPLICATE_RECORDS"
    assert len(records) == 2

    inspection = from_report(report, records, content_fingerprint="fp",
                             fingerprint_kind="body",
                             cross_reader=CrossReaderResult(status="not_applicable"))
    assert inspection.conclusion is ComplianceConclusion.NONCOMPLIANT
    assert inspection.repairability is Repairability.CONFIRMABLE
    assert inspection.raw_records[0].document == {"AIGC": VALID_AIGC}

    # Markdown 只有一套 YAML 解析器，标 not_applicable 即可——不需要在调用处
    # 另传 require_cross_reader=False。少一个"忘了传就静默改坏"的开关。
    draft = RepairPlanner().plan(inspection)
    assert draft.executable is True, draft.blocking_reasons
    assert draft.repairability is Repairability.CONFIRMABLE
    assert draft.proposed_document is not None


def test_missing_cross_reader_still_blocks(tmp_path):
    """缺省 ``not_run`` 仍要拦住：忘了标注模态的后果必须是"转人工"。

    ``not_applicable`` 是"已知没有第二条读取路径"，``not_run`` 是"不知道读没读"。
    后者照样不该被自动改写——这条测试就是防止上一条把闸门整个拆掉。
    """
    dual = _dual_carrier_markdown(tmp_path)
    report, records = _markdown_report(dual)

    inspection = from_report(report, records, content_fingerprint="fp",
                             fingerprint_kind="body")
    assert inspection.cross_reader.status == "not_run"

    draft = RepairPlanner().plan(inspection)
    assert draft.executable is False
    assert any("交叉读取" in r for r in draft.blocking_reasons)


def test_markdown_not_found_is_not_repairable(tmp_path):
    """干净 Markdown 应走打标流程，修复台不能假装能修。"""
    clean = make_markdown(tmp_path / "clean.md", "# 标题\n\n正文。\n")
    report, records = _markdown_report(clean)
    assert report["conclusion"] == "not_found"

    draft = RepairPlanner().plan(
        from_report(report, records, content_fingerprint="fp",
                    fingerprint_kind="body",
                    cross_reader=CrossReaderResult(status="not_applicable")))
    assert draft.executable is False
    assert draft.repairability is Repairability.NOT_APPLICABLE


def test_extended_xmp_is_always_false_for_non_image(tmp_path):
    """Extended XMP 是 JPEG 的 APP11 分段。若误报 True，所有非图片都会掉进人工。"""
    report, records = _markdown_report(_dual_carrier_markdown(tmp_path))
    inspection = from_report(report, records, content_fingerprint="fp",
                             fingerprint_kind="body")
    assert inspection.extended_xmp is False
    assert inspection.fingerprint_kind == "body"


# ---- 来源核对：与图片侧同一套状态词 ---------------------------------------

def _registry(tmp_path) -> SQLiteIdentifierRegistry:
    return SQLiteIdentifierRegistry(str(tmp_path / "registry.sqlite3"))


def test_verify_source_matches_registered_fingerprint(tmp_path):
    registry = _registry(tmp_path)
    reservation = registry.reserve({"AIGC": VALID_AIGC}, "fp-abc")
    reservation.commit()

    status, issues = verify_source({"AIGC": VALID_AIGC}, fingerprint="fp-abc",
                                   fingerprint_kind="body", registry=registry)
    assert status.status == "verified"
    assert issues == []


def test_verify_source_flags_conflicting_fingerprint(tmp_path):
    registry = _registry(tmp_path)
    registry.reserve({"AIGC": VALID_AIGC}, "fp-abc").commit()

    status, issues = verify_source({"AIGC": VALID_AIGC}, fingerprint="fp-OTHER",
                                   fingerprint_kind="stream", registry=registry)
    assert status.status == "conflict"
    # 提供者编号与传播者编号各登记一次，两份都对不上就该报两条
    assert {i.code for i in issues} == {"AIGC_IDENTIFIER_CONFLICT"}
    assert len(issues) == 2
    assert all(i.level == "error" for i in issues)


def test_verify_source_without_registry_is_unverified(tmp_path):
    status, issues = verify_source({"AIGC": VALID_AIGC}, fingerprint="fp",
                                   fingerprint_kind="body", registry=None)
    assert status.status == "unverified"
    assert issues == []


def test_verify_source_without_document_is_not_applicable():
    status, _ = verify_source(None, fingerprint="fp", fingerprint_kind="body",
                              registry=None)
    assert status.status == "not_applicable"


def test_conflict_forbids_repair_end_to_end(tmp_path):
    """冲突必须一路传到规划器变成 forbidden，而不是只躺在检测报告里。

    这条走完整链路（登记库 → 转换层 → 规划器），因为它要证明的是"禁止自动修复"
    这个结论本身，而不是某个函数返回了什么。
    """
    dual = _dual_carrier_markdown(tmp_path)
    report, records = _markdown_report(dual)

    registry = _registry(tmp_path)
    registry.reserve({"AIGC": VALID_AIGC}, "fp-abc").commit()

    inspection = from_report(
        report, records, content_fingerprint="fp-OTHER", fingerprint_kind="body",
        cross_reader=CrossReaderResult(status="not_applicable"), registry=registry)
    assert inspection.source_verification.status == "conflict"

    # 生产默认配置（不传 require_cross_reader）：上面已显式给了 not_applicable
    draft = RepairPlanner().plan(inspection)
    assert draft.repairability is Repairability.FORBIDDEN
    assert draft.executable is False


# ---- 载体闸门：标识对不对是一回事，容器让不让写是另一回事 --------------------

def _markdown_report_with_issue(tmp_path, code: str, message: str):
    """把一条载体级问题塞进真实报告，走真实的转换层。"""
    dual = _dual_carrier_markdown(tmp_path)
    report, records = _markdown_report(dual)
    report["issues"] = list(report["issues"]) + [
        {"code": code, "severity": "warn", "message": message}]
    return report, records


def _plan_for(report, records):
    inspection = from_report(report, records, content_fingerprint="fp",
                             fingerprint_kind="body",
                             cross_reader=CrossReaderResult(status="not_applicable"))
    return inspection, RepairPlanner().plan(inspection)


@pytest.mark.parametrize("code,message,expected", [
    # PDF 有签名/加密：ExifTool 写 PDF 是整体重写，写下去签名必失效——工具内无解
    ("PDF_SIGNED_PRESENT", "PDF 含数字签名：写入标识会使签名失效", "forbidden"),
    ("PDF_ENCRYPTED", "PDF 已加密，元数据不可读", "forbidden"),
    # 载体坏了：连"原本有没有标识"都定不了，得人来判断来源
    ("UNREADABLE_CARRIER", "PDF 缺少 %%EOF 结束标记（截断或尾部损坏）", "manual_review"),
])
def test_carrier_blockers_stop_the_rewrite(tmp_path, code, message, expected):
    """载体不能安全重写时，把检测侧的原话当理由，且必须**先于**标识判断生效。

    夹具用的是"标识本来该修"的双载体 Markdown——如果闸门放在标识判断之后，
    这条会变成 executable=True，那正是"同一份文件在检测页说不可写、在修复台
    被自动改写"的来源。
    """
    report, records = _markdown_report_with_issue(tmp_path, code, message)
    inspection, draft = _plan_for(report, records)

    assert inspection.carrier_forbidden is (expected == "forbidden")
    assert draft.executable is False
    assert draft.repairability.value == expected
    assert draft.blocking_reasons == [message], "理由必须是检测侧的原话"


def test_carrier_gate_ignores_ordinary_compliance_problems(tmp_path):
    """闸门只认"容器不让写"，不能把普通的重复标识也一并拦下。

    拦过头比漏拦更难发现：修复台会整体变得没用，而每条测试看着都还是绿的。
    """
    report, records = _markdown_report_with_issue(
        tmp_path, "DUPLICATE_RECORDS", "检出 2 份标识，应仅保留一份")
    inspection, draft = _plan_for(report, records)

    assert inspection.carrier_blockers == []
    assert draft.executable is True, draft.blocking_reasons
