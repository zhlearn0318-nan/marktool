from app.schemas.validation import validate_aigc_json


def test_valid_metadata_passes():
    data = {"Label": "1", "ContentProducer": "ACME", "ProduceID": "P-001"}
    assert validate_aigc_json(data) == []


def test_missing_required_field_reports_error():
    data = {"Label": "1", "ContentProducer": "ACME"}  # 缺 ProduceID
    errors = validate_aigc_json(data)
    assert any("ProduceID" in e for e in errors)


def test_empty_object_reports_all_required():
    errors = validate_aigc_json({})
    assert len(errors) >= 3
