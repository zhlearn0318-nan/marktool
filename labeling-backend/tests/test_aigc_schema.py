"""AIGC 数据测试（开发手册 §15.2）。

覆盖手册列出的九类用例：Label 枚举与字符串数字、七字段缺失、大小写错误、
未知字段、空生产者/编号、首期禁字符、预留字段空串、首次写入一致性。
"""
from app.core import aigc

VALID = {
    "Label": "1",
    "ContentProducer": "ORG_1565201000000016",
    "ProduceID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode1": "",
    "ContentPropagator": "ORG_1565201000000016",
    "PropagateID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode2": "",
}


def _ok(obj):
    return aigc.validate_aigc(obj) == []


def _errors_for(obj, field_prefix):
    return [e for e in aigc.validate_aigc(obj) if e["field"].startswith(field_prefix)]


def test_valid_labels():
    for label in ("1", "2", "3"):
        assert _ok({**VALID, "Label": label})


def test_label_numeric_1_rejected():
    """Label 传数字 1 必须拒绝（§5.3 是字符串枚举）。"""
    errs = _errors_for({**VALID, "Label": 1}, "AIGC.Label")
    assert errs


def test_each_of_seven_fields_required():
    for f in aigc.FIELD_ORDER:
        obj = {**VALID}
        del obj[f]
        assert _errors_for(obj, f"AIGC.{f}")


def test_wrong_case_rejected():
    """字段大小写错误（如 Propagator / PropatorID）→ 视为缺失 + 未知字段。"""
    obj = {**VALID, "ContentPropagator": None}
    del obj["ContentPropagator"]
    obj["Propagator"] = "ORG_X"
    errs = aigc.validate_aigc(obj)
    assert any("ContentPropagator" in e["field"] for e in errs)
    assert any("未知字段" in e["reason"] for e in errs)


def test_unknown_field_rejected():
    errs = aigc.validate_aigc({**VALID, "Extra": "x"})
    assert any("未知字段" in e["reason"] for e in errs)


def test_empty_producer_and_ids_rejected():
    for f in ("ContentProducer", "ProduceID", "ContentPropagator", "PropagateID"):
        assert _errors_for({**VALID, f: ""}, f"AIGC.{f}")


def test_reserved_fields_empty_string_ok():
    """预留字段为空字符串是合法状态（§5.2）。"""
    assert _ok(VALID)


def test_forbidden_chars_rejected():
    """首期禁空格、双引号、反斜杠、换行及中文（§5.5）。"""
    for bad in ('a b', 'a"b', "a\\b", "a\nb", "中文"):
        assert _errors_for({**VALID, "ContentProducer": bad}, "AIGC.ContentProducer")


def test_allowed_chars_ok():
    assert _ok({**VALID, "ContentProducer": "ORG_1565201000000016"})


def test_serialize_compact_and_ordered():
    raw = aigc.serialize_aigc(VALID)
    import json
    parsed = json.loads(raw)
    assert list(parsed.keys()) == ["AIGC"]
    inner = parsed["AIGC"]
    assert list(inner.keys()) == aigc.FIELD_ORDER
    assert raw.count(" ") == 0  # 紧凑：无空白分隔


def test_normalize_first_write():
    n = aigc.normalize_first_write({
        "Label": "2",
        "ContentProducer": "PROD",
        "ProduceID": "PID",
        "ReservedCode1": None,
        "ContentPropagator": "",
        "PropagateID": "",
        "ReservedCode2": None,
    })
    assert n["ContentPropagator"] == "PROD"
    assert n["PropagateID"] == "PID"
    assert n["ReservedCode1"] == "" and n["ReservedCode2"] == ""
    assert aigc.first_write_consistent(n)


def test_normalize_does_not_override_explicit():
    n = aigc.normalize_first_write({**VALID, "ContentPropagator": "ORG_OTHER",
                                    "PropagateID": "PID_OTHER"})
    assert n["ContentPropagator"] == "ORG_OTHER"
    assert n["PropagateID"] == "PID_OTHER"
