import pytest

import app.schemas.validation as validation_module

from app.schemas.validation import (
    serialize_aigc_document,
    validate_aigc_business_rules,
    validate_aigc_document,
    validate_aigc_json,
    validate_project_policy,
)
from tests.fixtures import VALID_AIGC, VALID_DOCUMENT


@pytest.mark.parametrize("label", ["1", "2", "3"])
def test_all_standard_label_values_pass(label):
    document = {"AIGC": {**VALID_AIGC, "Label": label}}
    assert validate_aigc_document(document) == []


def test_compatibility_validator_accepts_inner_object():
    assert validate_aigc_json(VALID_AIGC) == []


@pytest.mark.parametrize("field", list(VALID_AIGC))
def test_every_standard_field_is_required(field):
    inner = {key: value for key, value in VALID_AIGC.items() if key != field}
    errors = validate_aigc_document({"AIGC": inner})
    assert any(field in error for error in errors)


def test_label_must_be_string_enum():
    document = {"AIGC": {**VALID_AIGC, "Label": 1}}
    assert validate_aigc_document(document)


def test_unknown_field_is_rejected():
    document = {"AIGC": {**VALID_AIGC, "Extra": "not-standard"}}
    errors = validate_aigc_document(document)
    assert any("Extra" in error for error in errors)


def test_outer_aigc_object_is_required():
    assert validate_aigc_document(VALID_AIGC)


def test_non_reserved_identity_fields_cannot_be_empty():
    for field in ("ContentProducer", "ProduceID", "ContentPropagator", "PropagateID"):
        document = {"AIGC": {**VALID_AIGC, field: ""}}
        assert validate_aigc_document(document)


def test_serializer_is_compact_and_keeps_outer_object():
    serialized = serialize_aigc_document(VALID_DOCUMENT)
    assert serialized.startswith('{"AIGC":{"Label":"1"')
    assert " " not in serialized
    assert '"ReservedCode2":""' in serialized


@pytest.mark.parametrize(
    "invalid_value",
    ["ORG TEST", 'ORG"TEST', r"ORG\TEST", "ORG\nTEST", "示例机构"],
)
def test_strict_character_profile_rejects_out_of_range_values(invalid_value):
    document = {"AIGC": {**VALID_AIGC, "ContentProducer": invalid_value}}
    errors = validate_aigc_business_rules(document)
    assert any("ContentProducer" in error for error in errors)


def test_strict_character_profile_accepts_boundary_characters():
    document = {"AIGC": {**VALID_AIGC, "ReservedCode1": "!#AZ[]~"}}
    assert validate_aigc_business_rules(document) == []


def test_initial_write_requires_producer_and_propagator_to_match():
    document = {
        "AIGC": {
            **VALID_AIGC,
            "ContentPropagator": "ORG_OTHER",
            "PropagateID": "PROP_OTHER",
        }
    }
    errors = validate_aigc_business_rules(
        document,
        require_initial_relationships=True,
    )
    assert any("ContentPropagator" in error for error in errors)
    assert any("PropagateID" in error for error in errors)


def test_later_propagation_may_use_different_provider_and_identifier():
    document = {
        "AIGC": {
            **VALID_AIGC,
            "ContentPropagator": "ORG_OTHER",
            "PropagateID": "PROP_OTHER",
        }
    }
    assert validate_aigc_business_rules(
        document,
        require_initial_relationships=False,
    ) == []


def test_project_field_length_limit_is_enforced():
    document = {"AIGC": {**VALID_AIGC, "ReservedCode1": "A" * 1025}}
    assert validate_aigc_document(document) == []
    assert validate_project_policy(document)


def test_serialized_json_total_byte_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(validation_module, "AIGC_MAX_SERIALIZED_BYTES", 10)
    with pytest.raises(ValueError, match="字节上限"):
        serialize_aigc_document(VALID_DOCUMENT)
