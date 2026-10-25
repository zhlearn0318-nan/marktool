"""内容互相矛盾的多份隐式标识。

完全相同的重复标识仍由原有修复规则自动合并。只有规范化之后字段值不同的
多份记录才进入冲突组：每份候选有稳定 id，调用方不传选择时保持“不自动挑选”。
选定一份之后，其余冲突记录删除，再交给原来的修复规则补预留字段、转换数值
Label。这里不生成新的 ProduceID。

一个内容文件是一个冲突组。结果包里多个文件各自冲突时，就是多个组，必须逐组选择。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app.core import aigc
from app.metadata.repair_planner import RepairPlanner

SINGLE_FILE_GROUP = "file"


class ConflictSelectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _as_view(record: Any) -> tuple[str, str, Optional[dict], str]:
    if isinstance(record, dict):
        tag = str(record.get("tag_key") or record.get("tag") or "AIGC")
        location = str(record.get("location") or tag)
        inner = record.get("aigc")
        raw = str(record.get("raw") or "")
        return tag, location, inner if isinstance(inner, dict) else None, raw
    if hasattr(record, "tag_key"):
        tag = str(record.tag_key)
        location = str(record.location or tag)
        inner = record.aigc if isinstance(getattr(record, "aigc", None), dict) else None
        return tag, location, inner, str(getattr(record, "raw", "") or "")
    tag = str(getattr(record, "property_name", "AIGC"))
    location = str(getattr(record, "packet_location", "") or tag)
    inner = getattr(record, "aigc", None)
    if not isinstance(inner, dict):
        document = getattr(record, "document", None)
        if isinstance(document, dict) and isinstance(document.get("AIGC"), dict):
            inner = document["AIGC"]
        else:
            inner = None
    return tag, location, inner, str(getattr(record, "raw_value", "") or "")


def _compare_key(inner: dict) -> str:
    normalized, _actions = RepairPlanner._normalize_safe({"AIGC": inner})
    if normalized is not None:
        return RepairPlanner._canonical(normalized)
    return json.dumps(inner, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candidate_id(group_key: str, index: int, tag: str, compare_key: str) -> str:
    raw = f"{group_key}\n{index}\n{tag}\n{compare_key}"
    return "cand_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _group_id(group_key: str) -> str:
    return "grp_" + hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:16]


def _source_label(tag: str, location: str) -> str:
    lowered = tag.lower()
    if "id3" in lowered or tag.startswith("ID3"):
        carrier = "ID3"
    elif "quicktime" in lowered:
        carrier = "mp4 udta / QuickTime 注释"
    elif lowered.startswith("xmp"):
        carrier = "XMP"
    elif "text-trailer" in lowered or "trailer" in lowered:
        carrier = "文本尾段"
    else:
        carrier = tag
    if location and location not in {tag, carrier}:
        return f"{carrier} · {location}"
    return carrier


def build_conflict_groups(records: list, *, group_key: str, file_label: str | None = None) -> list[dict]:
    """若这些记录规范化后不是同一份，返回一个冲突组；否则返回空列表。"""
    parsed: list[tuple[int, str, str, dict, str]] = []
    for index, record in enumerate(records):
        tag, location, inner, _raw = _as_view(record)
        if not isinstance(inner, dict):
            continue
        parsed.append((index, tag, location, inner, _compare_key(inner)))
    if len(parsed) < 2:
        return []
    if len({item[4] for item in parsed}) < 2:
        return []
    fields_by_name: dict[str, list[Any]] = {name: [] for name in aigc.FIELD_ORDER}
    for _index, _tag, _location, inner, _key in parsed:
        for name in aigc.FIELD_ORDER:
            fields_by_name[name].append(inner.get(name, None))
    differing = [
        name for name, values in fields_by_name.items()
        if len({json.dumps(value, ensure_ascii=False, sort_keys=True) for value in values}) > 1
    ]
    candidates = []
    for index, tag, location, inner, key in parsed:
        candidates.append({
            "id": _candidate_id(group_key, index, tag, key),
            "index": index,
            "tag": tag,
            "location": _source_label(tag, location),
            "fields": {name: inner.get(name, None) for name in aigc.FIELD_ORDER},
            "differing_fields": [name for name in differing if any(
                json.dumps(inner.get(name, None), ensure_ascii=False, sort_keys=True)
                != json.dumps(other[3].get(name, None), ensure_ascii=False, sort_keys=True)
                for other in parsed if other[0] != index
            )],
        })
    return [{
        "id": _group_id(group_key),
        "file": file_label or group_key,
        "group_key": group_key,
        "differing_fields": differing,
        "candidates": candidates,
    }]


def attach_to_report(report: dict, records: list, *, group_key: str = SINGLE_FILE_GROUP,
                      file_label: str | None = None) -> dict:
    groups = build_conflict_groups(
        records, group_key=group_key,
        file_label=file_label or report.get("file_name") or group_key,
    )
    if groups:
        report["conflict_groups"] = groups
    return report


def resolve_selection(
    groups: list[dict],
    selections: Optional[dict[str, str]] = None,
    selected_candidate_id: Optional[str] = None,
) -> Optional[dict[str, str]]:
    """把用户选择归一成 group_id -> candidate_id。

    没有冲突组、也没有传入选择时返回 None，调用方保持原行为。
    """
    if not groups:
        if selections or selected_candidate_id:
            raise ConflictSelectionError("INVALID_CANDIDATE_ID", "当前文件没有需要选择的冲突标识")
        return None
    if not selections and not selected_candidate_id:
        return None
    choice = dict(selections or {})
    if selected_candidate_id:
        if len(groups) != 1:
            raise ConflictSelectionError(
                "SELECTION_REQUIRED",
                "有多个冲突组，请用 selections 为每一组指定候选",
            )
        choice.setdefault(groups[0]["id"], selected_candidate_id)
    known_groups = {group["id"]: group for group in groups}
    missing = [group["id"] for group in groups if group["id"] not in choice]
    if missing:
        raise ConflictSelectionError("SELECTION_REQUIRED", "每个冲突组都要选择一份标识")
    resolved: dict[str, str] = {}
    for group_id, candidate_id in choice.items():
        group = known_groups.get(group_id)
        if group is None:
            raise ConflictSelectionError("INVALID_CANDIDATE_ID", f"未知的冲突组 {group_id}")
        if candidate_id not in {item["id"] for item in group["candidates"]}:
            raise ConflictSelectionError("INVALID_CANDIDATE_ID", f"未知的候选 {candidate_id}")
        resolved[group_id] = candidate_id
    return resolved


def chosen_records(
    records: list,
    groups: list[dict],
    resolved: dict[str, str],
) -> list:
    """只留下每个冲突组里被选中的那一份记录。"""
    keep: set[int] = set()
    for group in groups:
        candidate_id = resolved[group["id"]]
        match = next(item for item in group["candidates"] if item["id"] == candidate_id)
        keep.add(int(match["index"]))
    return [record for index, record in enumerate(records) if index in keep]
