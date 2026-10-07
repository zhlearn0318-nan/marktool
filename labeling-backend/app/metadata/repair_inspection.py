"""把统一检测报告翻译成修复规划器要的 ``MetadataComplianceResult``。

**为什么需要这一层**：修复规划器 ``RepairPlanner.plan()`` 的入参是图片模块的
pydantic 对象 ``MetadataComplianceResult``，而且只读其中七处——``conclusion`` /
``extended_xmp`` / ``cross_reader`` / ``c2pa_presence`` / ``source_verification`` /
``raw_records``。视频与文档的检测器产出的是**统一报告 dict**（给前端用的那套），
字段名与访问方式都不同，直接喂给规划器会 AttributeError。

**为什么不改规划器**：规划器里那七处读法就是修复的判定逻辑本身，三种模态必须
共用同一份，否则同一个文件会得到两个答案。所以翻译放在这一层，规划器一行不动。

**图片为什么不经这里**：图片检测器本来就产出 ``MetadataComplianceResult``，不需要
翻译。但翻译的**保真度**由 ``tests/test_repair_inspection.py`` 保证——它把图片的
报告绕一圈翻译回来，逐项比对规划器真正会读的那七个字段。
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional, Sequence

from app.core.reader import AIGCRecord as CarrierRecord
from app.core.reader import carrier_location
from app.metadata.c2pa_presence import C2PAPresence
from app.metadata.compliance import (CandidateEvidence, C2PAPresencePayload,
                                     ComplianceConclusion, ComplianceIssue,
                                     CrossReaderResult,
                                     MetadataComplianceResult,
                                     ProjectPolicyResult, Repairability,
                                     SourceVerificationResult)
from app.metadata.identifier_registry import IdentifierRegistry
from app.metadata.xmp_reader import AIGCRecord as PlannerRecord
from app.schemas.validation import validate_project_policy

# 检测词表（统一报告）→ 修复台词表（规划器）。
# 两个词表刻意不同：检测说"能不能修"，修复台说"下一步该谁动手"，
# ``None``（无标识）在修复语境下是"不适用"而不是"可修"。
_REPAIRABILITY = {
    "auto_fixable": Repairability.CONFIRMABLE,
    "needs_human": Repairability.MANUAL_REVIEW,
    "forbidden": Repairability.FORBIDDEN,
}

# 统一报告的 c2pa_presence 取值 → 图片模块的枚举
_C2PA = {
    "absent": C2PAPresence.NOT_FOUND,
    "not_found": C2PAPresence.NOT_FOUND,
    "present_unverified": C2PAPresence.PRESENT_UNVERIFIED,
    "indeterminate": C2PAPresence.INDETERMINATE,
}

# 统一报告的 severity → ComplianceIssue.level
_LEVEL = {"error": "error", "warn": "warning", "warning": "warning", "info": "info"}

_CARRIER_CODES = frozenset({
    "LEGACY_CARRIER", "UNREADABLE_CARRIER", "DUPLICATE_RECORDS",
    "PDF_ENCRYPTED", "PDF_SIGNED_PRESENT",
})
# 曾在这里的 ``MISSING_EOF`` 已删：没有任何检测器发这个码——缺 %%EOF 走的是
# ``UNREADABLE_CARRIER``（见 document_inspector._verdict 的载体可靠性升级）。
# 留着会在读代码时造出一个不存在的检查项，让人以为有单独一道缺失结束标记的判定。

# 检测报告里"这个容器还让不让人写"的信号，分两档。都用检测侧的原话当理由：
# 同一件事在检测页和修复台必须一个说法，各写各的文案迟早会对不上。
#
# 工具内无解：写入方式本身就会毁掉文件（PDF 整体重写 vs 签名/加密）。
_CARRIER_FORBIDDEN = frozenset({"PDF_SIGNED_PRESENT", "PDF_ENCRYPTED"})
# 载体现状不可靠：连"里面原本有没有标识"都定不了，得人来判断来源。
_CARRIER_UNRELIABLE = frozenset({"UNREADABLE_CARRIER", "METADATA_REGION_WIPED"})


def _carrier_gate(report: dict) -> tuple[list[str], bool]:
    """返回 ``(理由列表, 是否工具内无解)``；理由为空即载体可安全重写。"""
    forbidden: list[str] = []
    unreliable: list[str] = []
    for item in report.get("issues") or []:
        code = str(item.get("code", ""))
        message = str(item.get("message", "")).strip()
        if not message:
            continue
        if code in _CARRIER_FORBIDDEN:
            forbidden.append(message)
        elif code in _CARRIER_UNRELIABLE:
            unreliable.append(message)
    if forbidden:
        return forbidden, True
    return unreliable, False


def _issue_category(code: str) -> str:
    if code.startswith("C2PA"):
        return "c2pa"
    return "carrier" if code in _CARRIER_CODES else "gb45438"


def _outer_document(record: CarrierRecord) -> Optional[dict]:
    """统一报告的记录 → 规划器要的 ``{"AIGC": {七字段}}``。"""
    return {"AIGC": record.aigc} if record.aigc is not None else None


def _parse_error(record: CarrierRecord) -> Optional[str]:
    if record.aigc is not None:
        return None
    return "AIGC 元数据不是可解析的七字段 JSON"


def _normalize(record: CarrierRecord | PlannerRecord,
               mime: str) -> PlannerRecord:
    """两种 ``AIGCRecord`` 都是同名不同物的类，这里统一成规划器那一版。

    载体侧（``app.core.reader``，视频/文档路径产出）叫 ``tag_key/raw/aigc/location``；
    规划器侧（``app.metadata.xmp_reader``，图片路径产出）叫
    ``property_name/raw_value/document/parse_error``。调用方给哪种都行——
    同名类混用一旦靠"传错了自然会报错"来兜底，报出来的会是 AttributeError 而不是
    "模态不对"，排查代价远高于这里多一个 isinstance。
    """
    if isinstance(record, PlannerRecord):
        return record
    return PlannerRecord(
        raw_value=record.raw,
        property_name=record.tag_key,
        packet_location=(record.location
                         or carrier_location(record.tag_key, mime=mime)
                         or record.tag_key),
        document=_outer_document(record),
        aigc=record.aigc,
        parse_error=_parse_error(record),
    )


def to_planner_records(records: Sequence[CarrierRecord | PlannerRecord],
                       mime: str) -> list[PlannerRecord]:
    """载体读取器的记录 → 规划器记录的形状（已是对应形状的原样返回）。"""
    return [_normalize(record, mime) for record in records]


def to_candidates(records: Sequence[CarrierRecord | PlannerRecord],
                  mime: str) -> list[CandidateEvidence]:
    """同一批记录 → 规划器要的候选证据。

    以**记录**而不是报告里的 ``candidates`` 为真源：报告那边只给了字段名没有值，
    而修复要按值判"两份标识是否真的冲突"，必须拿到原始 JSON。
    """
    candidates: list[CandidateEvidence] = []
    for index, record in enumerate(to_planner_records(records, mime)):
        candidates.append(CandidateEvidence(
            index=index,
            property_name=record.property_name,
            packet_location=record.packet_location,
            raw_value_sha256=hashlib.sha256(
                record.raw_value.encode("utf-8")).hexdigest(),
            raw_value_preview=record.raw_value[:120],
            parseable=record.parse_error is None,
            parsed_document=record.document,
            parse_error=record.parse_error,
        ))
    return candidates


def to_issues(report_issues: Sequence[dict]) -> list[ComplianceIssue]:
    """统一报告的问题条目 → 规划器要的问题条目（补上 category）。"""
    issues: list[ComplianceIssue] = []
    for item in report_issues or []:
        code = str(item.get("code", "UNKNOWN"))
        issues.append(ComplianceIssue(
            code=code,
            category=_issue_category(code),
            level=_LEVEL.get(str(item.get("severity", "info")), "info"),
            detail=str(item.get("message", "")),
        ))
    return issues


def verify_source(document: Optional[dict], *, fingerprint: str,
                  fingerprint_kind: str,
                  registry: Optional[IdentifierRegistry],
                  ) -> tuple[SourceVerificationResult, list[ComplianceIssue]]:
    """编号登记核对：登记记录里的内容指纹是不是就是这一份内容。

    与图片侧 ``MetadataComplianceEngine._verify_source`` 同一套判定与同一套状态词，
    只有文案按模态改（"另一内容指纹"而不是"另一图片像素指纹"）——两处若各写一套，
    同一份文件在检测页与修复台会得到相反的结论。
    """
    aigc = None
    if isinstance(document, dict):
        candidate = document.get("AIGC", document)
        if isinstance(candidate, dict):
            aigc = candidate
    if aigc is None:
        return SourceVerificationResult(status="not_applicable"), []

    required = ("ContentProducer", "ProduceID", "ContentPropagator", "PropagateID")
    if any(not isinstance(aigc.get(f), str) or not aigc[f] for f in required):
        return SourceVerificationResult(status="not_applicable"), []
    if registry is None:
        return SourceVerificationResult(
            status="unverified",
            details=["当前检测未连接编号登记库"],
        ), []

    checks = (
        ("producer", aigc["ContentProducer"], aigc["ProduceID"]),
        ("propagator", aigc["ContentPropagator"], aigc["PropagateID"]),
    )
    matched = 0
    details: list[str] = []
    issues: list[ComplianceIssue] = []
    for role, provider, content_id in checks:
        registered = registry.lookup(role, provider, content_id)
        if registered is None:
            details.append(f"{role} 编号未在本地登记库中找到")
        elif registered == fingerprint:
            matched += 1
            details.append(f"{role} 编号与当前内容的{fingerprint_kind} 指纹一致")
        else:
            details.append(f"{role} 编号已登记到另一内容指纹")
            issues.append(ComplianceIssue(
                code="AIGC_IDENTIFIER_CONFLICT",
                category="provenance",
                level="error",
                detail=(f"{role} 编号与登记内容指纹冲突；"
                        "这不是国标结构失败，但禁止自动修复"),
            ))

    if issues:
        status = "conflict"
    elif matched == len(checks):
        status = "verified"
    elif matched:
        status = "partially_verified"
    else:
        status = "unverified"
    return SourceVerificationResult(status=status, details=details), issues


def from_report(report: dict, records: Sequence[CarrierRecord], *,
                content_fingerprint: str, fingerprint_kind: str,
                cross_reader: Optional[CrossReaderResult] = None,
                registry: Optional[IdentifierRegistry] = None,
                extra_issues: Sequence[ComplianceIssue] = (),
                ) -> MetadataComplianceResult:
    """统一检测报告 + 载体记录 → 规划器入参。

    ``cross_reader`` 由调用方按模态给出：视频拿 ffprobe 与 ExifTool 对照、PDF 拿
    字节扫描与 ExifTool 对照、Markdown 只有一套 YAML 解析器故标 ``not_applicable``。
    缺省 ``not_run`` 是有意为之——它会让规划器把"没做交叉读取"当成阻塞项，
    宁可转人工，也不要让一份没核对过的文件被自动改写。

    ``extra_issues`` 收下调用方在报告之外发现的问题（例如第二遍读取报出的
    ``AIGC_READERS_DIVERGED``）：这些问题与"读到的标识"绑定，统一报告里没有
    对应的位置可放。
    """
    mime = str(report.get("detected_mime_type") or "")
    planner_records = to_planner_records(records, mime)
    issues = to_issues(report.get("issues") or [])
    issues.extend(extra_issues)

    document = next((r.document for r in planner_records if r.document), None)
    source_verification, source_issues = verify_source(
        document, fingerprint=content_fingerprint, fingerprint_kind=fingerprint_kind,
        registry=registry)
    issues.extend(source_issues)

    policy_errors = validate_project_policy(document) if document else []
    issues.extend(
        ComplianceIssue(code="PROJECT_POLICY_REJECTED", category="project_policy",
                        level="warning", detail=error)
        for error in policy_errors
    )

    reason_codes = [str(c) for c in (report.get("reason_codes")
                                     or ([report["reason_code"]]
                                         if report.get("reason_code") else []))]
    carrier_blockers, carrier_forbidden = _carrier_gate(report)

    return MetadataComplianceResult(
        conclusion=ComplianceConclusion(str(report.get("conclusion") or "indeterminate")),
        reason_codes=reason_codes,
        repairability=_REPAIRABILITY.get(report.get("repairability"),
                                         Repairability.MANUAL_REVIEW),
        detected_format=_detected_format(report, mime),
        mime_type=mime,
        file_sha256=str(report.get("sha256") or ""),
        content_fingerprint=content_fingerprint,
        fingerprint_kind=fingerprint_kind,
        record_count=len(planner_records),
        # 这里放**内层**七字段（不是 {"AIGC": …} 外层壳），与图片检测器逐字一致：
        # 修复执行后会拿 `post.aigc_metadata != document["AIGC"]` 比对，形状差一层
        # 就会把修复成功的文件判成"七字段与计划不一致"。
        aigc_metadata=document["AIGC"] if document else None,
        candidates=to_candidates(records, mime),
        issues=issues,
        project_policy=ProjectPolicyResult(accepted=not policy_errors,
                                           errors=list(policy_errors)),
        cross_reader=cross_reader or CrossReaderResult(status="not_run"),
        source_verification=source_verification,
        c2pa_presence=C2PAPresencePayload(
            status=_C2PA.get(str(report.get("c2pa_presence")),
                             C2PAPresence.NOT_FOUND)),
        # Extended XMP 是 JPEG 的 APP11 分段，其余模态没有对应物。
        # 恒 False 而不是"未检查"：规划器把它当阻塞项，恒 True 会让所有非图片
        # 文件都掉进人工队列。
        extended_xmp=False,
        carrier_blockers=carrier_blockers,
        carrier_forbidden=carrier_forbidden,
        raw_records=planner_records,
    )


def _detected_format(report: dict, mime: str) -> str:
    """人类可读格式名。文档报告里有 ``document.format``，其余按 MIME 推。"""
    block = report.get("document")
    if isinstance(block, dict) and block.get("format"):
        return str(block["format"])
    return {"video/mp4": "MP4", "application/pdf": "PDF",
            "text/markdown": "Markdown"}.get(mime, mime or "unknown")
