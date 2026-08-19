import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_SCHEMA_PATH = Path(__file__).with_name("gb45438_appendix_e.json")
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)

AIGC_FIELD_ORDER = (
    "Label",
    "ContentProducer",
    "ProduceID",
    "ReservedCode1",
    "ContentPropagator",
    "PropagateID",
    "ReservedCode2",
)

# GB 45438—2025 附录 E j) 给出的主要字符范围。项目首期采用严格子集：
# 0x21、0x23~0x5B、0x5D~0x7E；排除空格、双引号、反斜杠和换行。
GB45438_STRICT_VALUE_CHARACTERS = frozenset(
    chr(code_point)
    for code_point in (
        [0x21]
        + list(range(0x23, 0x5C))
        + list(range(0x5D, 0x7F))
    )
)


def validate_aigc_document(document: Any) -> list[str]:
    """校验文件中实际保存的外层 ``{"AIGC": {...}}`` 对象。"""
    errors: list[str] = []
    for error in sorted(_VALIDATOR.iter_errors(document), key=lambda item: list(item.path)):
        field = ".".join(str(part) for part in error.path) or "root"
        errors.append(f"{field}: {error.message}")
    return errors


def validate_aigc_json(data: dict) -> list[str]:
    """兼容旧接口；内层七字段对象会先包装为标准外层对象再校验。"""
    document = data if "AIGC" in data else {"AIGC": data}
    return validate_aigc_document(document)


def validate_aigc_business_rules(
    document: Any,
    *,
    require_initial_relationships: bool = False,
    strict_characters: bool = True,
) -> list[str]:
    """校验 JSON Schema 无法表达的国标业务规则。"""
    if validate_aigc_document(document):
        return []

    aigc = document["AIGC"]
    errors: list[str] = []
    if strict_characters:
        for field in AIGC_FIELD_ORDER:
            value = aigc[field]
            invalid = sorted({
                character
                for character in value
                if character not in GB45438_STRICT_VALUE_CHARACTERS
            })
            if invalid:
                rendered = ", ".join(repr(character) for character in invalid)
                errors.append(
                    f"AIGC.{field}: 含首期严格字符范围之外的字符 {rendered}"
                )

    if require_initial_relationships:
        if aigc["ContentPropagator"] != aigc["ContentProducer"]:
            errors.append(
                "AIGC.ContentPropagator: 首次写入时必须等于 ContentProducer"
            )
        if aigc["PropagateID"] != aigc["ProduceID"]:
            errors.append(
                "AIGC.PropagateID: 首次写入时必须等于 ProduceID"
            )
    return errors


def serialize_aigc_document(document: dict) -> str:
    """按固定字段顺序生成待写入 XMP 的紧凑 UTF-8 JSON。"""
    errors = validate_aigc_document(document)
    if errors:
        raise ValueError("; ".join(errors))
    ordered = {
        "AIGC": {
            field: document["AIGC"][field]
            for field in AIGC_FIELD_ORDER
        }
    }
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
