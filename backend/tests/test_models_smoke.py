from app.schemas.models import CheckItem, CheckStatus, MarkType, Report, ComplianceReport, TamperReport, AIDetectionReport


def test_checkitem_roundtrip():
    item = CheckItem(
        id="metadata.presence",
        title="隐式标识(元数据)",
        status=CheckStatus.PASS,
        detail="ok",
        mark_type=MarkType.IMPLICIT_METADATA,
    )
    assert item.model_dump()["status"] == "pass"


def test_report_builds():
    rep = Report(
        provenance=[],
        compliance=ComplianceReport(items=[], rating="A", suggestions=[]),
        tamper=TamperReport(consistent=None, findings=[]),
        ai_detection=AIDetectionReport(enabled=False, probability=None, note="未启用"),
    )
    assert rep.model_dump()["compliance"]["rating"] == "A"
