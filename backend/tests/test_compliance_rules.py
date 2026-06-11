from app.report.compliance_rules import evaluate_compliance
from app.report.builder import build_report
from app.schemas.models import CheckItem, CheckStatus, MarkType


def _item(id_, status, mark=MarkType.IMPLICIT_METADATA, suggestion=None):
    return CheckItem(id=id_, title=id_, status=status, detail="d",
                     suggestion=suggestion, mark_type=mark)


def test_no_metadata_item_is_noncompliant():
    rating, _ = evaluate_compliance([])
    assert rating == "不合规"


def test_metadata_fail_is_noncompliant():
    rating, _ = evaluate_compliance([_item("metadata.presence", CheckStatus.FAIL)])
    assert rating == "不合规"


def test_metadata_warn_is_C():
    rating, _ = evaluate_compliance([_item("metadata.schema", CheckStatus.WARN)])
    assert rating == "C"


def test_metadata_pass_with_unverified_others_is_B():
    items = [
        _item("metadata.schema", CheckStatus.PASS),
        _item("explicit.ocr", CheckStatus.WARN, MarkType.EXPLICIT_TEXT),
    ]
    rating, _ = evaluate_compliance(items)
    assert rating == "B"


def test_all_pass_is_A():
    items = [
        _item("metadata.schema", CheckStatus.PASS),
        _item("explicit.ocr", CheckStatus.PASS, MarkType.EXPLICIT_TEXT),
    ]
    rating, _ = evaluate_compliance(items)
    assert rating == "A"


def test_build_report_provenance_from_metadata():
    items = [_item("metadata.schema", CheckStatus.PASS)]
    aigc = {"ContentProducer": "P", "ProduceID": "X1", "ContentPropagator": "Q", "PropagateID": "Y2"}
    rep = build_report(items, aigc)
    roles = [n.role for n in rep.provenance]
    assert roles == ["ContentProducer", "ContentPropagator"]
    assert rep.ai_detection.enabled is False
