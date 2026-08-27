"""共享 AIGC 数据模型 · 校验 · 序列化（GB 45438-2025 附录 E）

开发手册 §5：七字段、Label 枚举、首期字符约束、首次写入默认值、紧凑 JSON。
本模块只用标准库，供适配器、任务流水线、API 与检测器共同复用（手册 §14）。
"""
from __future__ import annotations

import json
from typing import Any

# §5.2 字段顺序 = 序列化输出顺序（§6.2）
FIELD_ORDER = [
    "Label",
    "ContentProducer",
    "ProduceID",
    "ReservedCode1",
    "ContentPropagator",
    "PropagateID",
    "ReservedCode2",
]
REQUIRED = frozenset(FIELD_ORDER)
LABEL_VALUES = ("1", "2", "3")
LABEL_MEANINGS = {"1": "属于人工智能生成合成内容",
                  "2": "可能为人工智能生成合成内容",
                  "3": "疑似为人工智能生成合成内容"}


def allowed_char(ch: str) -> bool:
    """§5.5 首期字符约束。

    允许 GB18030 单字节可打印字符中除 `"`、`\\`、空格、换行外的集合：
    0x21，以及 0x23~0x5B、0x5D~0x7E。与检测器 aigc_check.py 的判定一致。
    """
    c = ord(ch)
    return c == 0x21 or 0x23 <= c <= 0x5B or 0x5D <= c <= 0x7E


def validate_aigc(value: Any) -> list[dict]:
    """校验顶层 AIGC 对象，返回错误列表；空列表表示通过。

    覆盖手册 §5.7 Schema 与 §5.5 字符约束：
      - 结构（对象、未知字段、必填七字段、类型）
      - Label 枚举（字符串 "1"/"2"/"3"，拒绝数字 1）
      - 非空（ContentProducer/ProduceID/ContentPropagator/PropagateID）
      - 字符集
    """
    errors: list[dict] = []
    if not isinstance(value, dict):
        return [{"field": "AIGC", "reason": "必须是 JSON 对象"}]

    unknown = set(value) - REQUIRED
    if unknown:
        errors.append({"field": "AIGC." + sorted(unknown)[0], "reason": "不允许的未知字段"})

    for f in FIELD_ORDER:
        if f not in value:
            errors.append({"field": f"AIGC.{f}", "reason": "缺失必填字段"})
            continue
        v = value[f]
        if not isinstance(v, str):
            errors.append({"field": f"AIGC.{f}", "reason": "必须是字符串"})
            continue
        if f in ("ContentProducer", "ProduceID", "ContentPropagator", "PropagateID") and not v:
            errors.append({"field": f"AIGC.{f}", "reason": "不能为空"})
        bad = [c for c in v if not allowed_char(c)]
        if bad:
            errors.append({
                "field": f"AIGC.{f}",
                "reason": "含不允许字符（首期仅限 ASCII 单字节可打印字符，禁空格/引号/反斜杠/换行）",
            })

    label = value.get("Label")
    if label is not None and label not in LABEL_VALUES:
        errors.append({"field": "AIGC.Label", "reason": "必须是字符串 1、2 或 3"})
    return errors


def normalize_first_write(aigc: dict) -> dict:
    """§5.4 首次写入默认值（不修改入参）。

    ContentPropagator=ContentProducer、PropagateID=ProduceID、预留字段为空串。
    仅当提交值为空时自动填充，不覆盖用户显式给出的非空值。
    """
    out = dict(aigc)
    if not out.get("ContentPropagator"):
        out["ContentPropagator"] = out.get("ContentProducer", "")
    if not out.get("PropagateID"):
        out["PropagateID"] = out.get("ProduceID", "")
    if out.get("ReservedCode1") is None:
        out["ReservedCode1"] = ""
    if out.get("ReservedCode2") is None:
        out["ReservedCode2"] = ""
    return out


def serialize_aigc(aigc: dict) -> str:
    """§6.1/§6.2 序列化为紧凑 JSON：UTF-8、保留大小写、字段按 §5.2 顺序、
    不二次转义。禁止把平台运行字段（standard/modality/哈希/时间）混入。
    """
    ordered = {f: aigc[f] for f in FIELD_ORDER if f in aigc}
    return json.dumps({"AIGC": ordered}, ensure_ascii=True, separators=(",", ":"))


def parse_aigc(raw: str) -> dict | None:
    """解析文件内读回的 AIGC JSON 字符串，返回内层 AIGC 对象；非法返回 None。"""
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    inner = obj.get("AIGC")
    return inner if isinstance(inner, dict) else None


def first_write_consistent(aigc: dict) -> bool:
    """§5.4 校验：首次写入时传播方=生成方、传播编号=制作编号。"""
    return (aigc.get("ContentProducer") == aigc.get("ContentPropagator")
            and aigc.get("ProduceID") == aigc.get("PropagateID"))
