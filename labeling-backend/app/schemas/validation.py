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

# 国标规定字段与字符要求，但未为项目接口给出统一的工程长度上限。
# 以下是本项目首期的防滥用边界，须在接口文档中公开。
AIGC_MAX_FIELD_CHARACTERS = 1024
AIGC_MAX_SERIALIZED_BYTES = 8192

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
    """按 GB 45438—2025 附录 E 校验外层对象和固定七字段。

    工程长度上限和首期严格字符子集不属于国标结构结论，由
    :func:`validate_aigc_business_rules` 作为项目处理规则另行返回。
    """
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
    enforce_project_lengths: bool = True,
) -> list[str]:
    """校验调用方选择启用的首次写入关系和项目兼容性规则。"""
    if validate_aigc_document(document):
        return []

    aigc = document["AIGC"]
    errors: list[str] = []
    if enforce_project_lengths:
        for field in AIGC_FIELD_ORDER:
            if len(aigc[field]) > AIGC_MAX_FIELD_CHARACTERS:
                errors.append(
                    f"AIGC.{field}: 超过项目规定的 {AIGC_MAX_FIELD_CHARACTERS} 字符上限"
                )
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


def validate_project_policy(document: Any) -> list[str]:
    """返回项目安全/兼容性限制，不把这些限制冒充为国标结论。"""
    return validate_aigc_business_rules(
        document,
        strict_characters=True,
        require_initial_relationships=False,
    )


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
    serialized = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > AIGC_MAX_SERIALIZED_BYTES:
        raise ValueError(
            f"AIGC JSON 超过项目规定的 {AIGC_MAX_SERIALIZED_BYTES} 字节上限"
        )
    return serialized
