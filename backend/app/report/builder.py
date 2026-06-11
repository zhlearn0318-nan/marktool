from app.report.compliance_rules import evaluate_compliance
from app.schemas.models import (
    AIDetectionReport, CheckItem, CheckStatus, ComplianceReport,
    MarkType, ProvenanceNode, Report, TamperReport,
)


def _provenance(aigc: dict | None) -> list[ProvenanceNode]:
    nodes: list[ProvenanceNode] = []
    if not aigc:
        return nodes
    if aigc.get("ContentProducer"):
        nodes.append(ProvenanceNode(role="ContentProducer",
                                    name=aigc["ContentProducer"],
                                    id=aigc.get("ProduceID")))
    if aigc.get("ContentPropagator"):
        nodes.append(ProvenanceNode(role="ContentPropagator",
                                    name=aigc["ContentPropagator"],
                                    id=aigc.get("PropagateID")))
    return nodes


def _tamper(items: list[CheckItem]) -> TamperReport:
    findings: list[str] = []
    meta = next((i for i in items if i.id.startswith("metadata")), None)
    wm = next((i for i in items if i.mark_type == MarkType.IMPLICIT_WATERMARK), None)
    if meta and meta.status == CheckStatus.PASS and wm and wm.status == CheckStatus.WARN:
        findings.append("水印检测未启用，无法验证元数据与水印一致性")
    return TamperReport(consistent=None, findings=findings)


def build_report(items: list[CheckItem], aigc_metadata: dict | None) -> Report:
    rating, suggestions = evaluate_compliance(items)
    return Report(
        provenance=_provenance(aigc_metadata),
        compliance=ComplianceReport(items=items, rating=rating, suggestions=suggestions),
        tamper=_tamper(items),
        ai_detection=AIDetectionReport(
            enabled=False, probability=None,
            note="AI 内容概率检测为辅助参考，MVP 未启用",
        ),
    )
