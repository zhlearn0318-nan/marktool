import json
from pathlib import Path
from jsonschema import Draft7Validator

_SCHEMA_PATH = Path(__file__).with_name("gb45438_appendix_e.json")
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft7Validator(_SCHEMA)


def validate_aigc_json(data: dict) -> list[str]:
    """返回错误消息列表；空列表表示通过附录E结构校验。"""
    errors = []
    for err in sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or (
            err.message.split("'")[1] if "'" in err.message else "root"
        )
        errors.append(f"{field}: {err.message}")
    return errors
