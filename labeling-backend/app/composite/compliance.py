"""多模态与场景的合规检测、修复方案和执行。

单模态文件不在这里处理。调用方只有在明确要求自动识别、且文件确实是
多模态媒体、多模态结果包或场景包时才进入本模块，因此 JPEG / PNG / MP4 /
文档的原有检测与修复接口保持原样。

一份内容文件上的隐式标识，结论和修复约束直接复用单模态：
``classify_records`` 给出合规 / 不合规 / 未检出，``RepairPlanner`` 决定
能不能修。重复且内容一致才保留一份；JSON 损坏、身份字段缺失、多份内容
互相冲突，都不自动挑选。ProduceID 只沿用文件里已经能确定的值，不新造。

场景包比单模态多出来的显式标识（第 5.5 / 5.6 条）只做诊断。像素与
``scene.json`` 对不上时不自动重绘：没有未标注的底图，重绘会把提示叠上去。
这一条记在交接文档的待确认问题里。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from ..adapters import AdapterError
from ..core.inspector_common import (
    CONCLUSION_COMPLIANT,
    CONCLUSION_INDETERMINATE,
    CONCLUSION_NONCOMPLIANT,
    CONCLUSION_NOT_FOUND,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    MEDIA_OK,
    REPAIR_AUTO,
    REPAIR_HUMAN,
    Issue,
    LegacyRule,
    bmff_not_applicable,
    build_report,
    candidate_entry,
    classify_records,
)
from ..core.reader import AIGCRecord, ReaderError, read_aigc_records
from ..metadata.c2pa_presence import C2PAPresence
from ..metadata.compliance import (
    C2PAPresencePayload,
    ComplianceConclusion,
    CrossReaderResult,
    MetadataComplianceResult,
    ProjectPolicyResult,
    Repairability,
    SourceVerificationResult,
)
from ..metadata.identifier_registry import build_identifier_lookup
from ..metadata.repair_planner import RepairPlanner
from ..core import aigc as aigc_mod
from ..modalities.dispatch import label_file
from ..modalities.text_label import MARKER, parse_text_bytes
from ..multimodal.ffmpeg_util import probe as probe_media
from ..scenes.explicit import (
    INTERACTIVE_PLACEMENTS,
    VIRTUAL_POSITIONS,
    contrast_ratio,
    explicit_element_errors,
)
from ..scenes.overlay import overlay_matches
from ..tools import ToolPaths

_MEDIA_LEGACY = LegacyRule(
    detects=lambda tag: "QuickTime" in tag or str(tag).startswith("text-trailer"),
    name="QuickTime 注释或字幕轨内的标识",
    single_note="容器上应只保留一份文件元数据标识",
)
_FILE_LEGACY = LegacyRule(
    detects=lambda tag: "QuickTime" in tag,
    name="QuickTime 注释",
    single_note="应只保留一份规范载体",
)

_KIND_LABEL = {
    "multimodal-media": "多模态媒体文件",
    "multimodal-bundle": "多模态结果包",
    "multimodal-package": "多模态单文件包",
    "virtual-scene": "虚拟场景包",
    "interactive-scene": "交互场景包",
    "broken-descriptor": "无法读取的场景描述",
}

_RANK = {
    CONCLUSION_INDETERMINATE: 3,
    CONCLUSION_NONCOMPLIANT: 2,
    CONCLUSION_NOT_FOUND: 1,
    CONCLUSION_COMPLIANT: 0,
}


def classify_upload(path: Path) -> str | None:
    """识别多模态或场景。普通单模态文件返回 None。"""
    if zipfile.is_zipfile(path):
        return _classify_zip(path)
    if path.suffix.lower() not in {".mp4", ".m4v", ".mov"} and not _looks_mp4(path):
        return None
    try:
        info = probe_media(path)
    except Exception:
        return None
    kinds = {stream.get("codec_type") for stream in info.get("streams") or []}
    if "video" in kinds and ({"audio", "subtitle"} & kinds):
        return "multimodal-media"
    return None


def inspect_composite(
    path: Path,
    *,
    file_name: str,
    size_bytes: int,
    sha256: str,
    request_id: str,
    tools: ToolPaths,
) -> dict:
    started = time.monotonic()
    kind = classify_upload(path)
    if kind is None:
        raise ValueError("不是多模态或场景文件")
    units, explicit_issues, media = _inspect_kind(kind, path, tools)
    return _assemble(
        kind, units, explicit_issues, media, path,
        file_name=file_name, size_bytes=size_bytes, sha256=sha256,
        request_id=request_id, tools=tools,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def build_repair_plan(report: dict) -> dict:
    """按单文件的 RepairPlanner 汇总整包修复方案。不修改文件。"""
    actions: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    changes: list[dict] = []
    units_out: list[dict] = []
    any_change = False
    for unit in report.get("units") or []:
        draft_units, unit_actions, unit_blockers, unit_changes = _plan_unit(unit)
        actions.extend(unit_actions)
        blockers.extend(unit_blockers)
        changes.extend(unit_changes)
        if draft_units.get("proposed"):
            any_change = True
        units_out.append(draft_units)
    for issue in report.get("explicit_issues") or []:
        blockers.append(issue["message"])
    strip_subtitle = bool(report.get("subject", {}).get("strip_subtitle"))
    if strip_subtitle and not blockers:
        actions.append("去掉字幕轨里的隐式标识，只保留容器上的一份")
        any_change = True
    if report.get("conclusion") == CONCLUSION_COMPLIANT and not any_change:
        blockers.append("当前文件元数据已经符合国标，无需修复")
    if report.get("conclusion") == CONCLUSION_NOT_FOUND:
        blockers.append("未发现标识；如需新增应进入普通标注流程")
    executable = any_change and not blockers
    return {
        "repairability": "confirmable" if executable else (
            "not_applicable" if report.get("conclusion") in {
                CONCLUSION_COMPLIANT, CONCLUSION_NOT_FOUND,
            } else "manual_review"
        ),
        "executable": executable,
        "source_type": "derived_from_file" if executable else None,
        "source_reference": "原文件中全部可识别 AIGC 候选记录" if executable else None,
        "write_context": "unknown",
        "actions": list(dict.fromkeys(actions)),
        "field_changes": changes,
        "blocking_reasons": list(dict.fromkeys(blockers)),
        "warnings": warnings or ["执行前仍需用户明确确认；原文件不会被覆盖"],
        "units": units_out,
        "strip_subtitle": strip_subtitle and executable,
        "kind": report.get("subject", {}).get("kind"),
    }


def apply_repair_plan(src: Path, dest: Path, plan: dict, tools: ToolPaths) -> None:
    """按方案改副本。原文件保持不动。"""
    if not plan.get("executable"):
        raise ValueError("该方案不可执行")
    kind = plan.get("kind")
    proposed = {
        unit["name"]: unit.get("proposed")
        for unit in plan.get("units") or []
        if unit.get("proposed")
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "multimodal-media":
        _repair_media(src, dest, proposed, bool(plan.get("strip_subtitle")), tools)
        return
    if kind in {"virtual-scene", "interactive-scene", "multimodal-bundle", "multimodal-package"}:
        _repair_zip(src, dest, kind, proposed, tools)
        return
    raise ValueError("无法修复该类型")


def _classify_zip(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "scene.json" in names:
                try:
                    descriptor = json.loads(archive.read("scene.json").decode("utf-8"))
                except (json.JSONDecodeError, UnicodeError):
                    return "broken-descriptor"
                scene_type = descriptor.get("scene_type") if isinstance(descriptor, dict) else None
                if scene_type == "virtual" and "start_frame.png" in names:
                    return "virtual-scene"
                if scene_type == "interactive":
                    return "interactive-scene"
                return "broken-descriptor"
            if "AIGC.json" in names and "manifest.json" in names:
                return "multimodal-package"
            if any(name.startswith("components/") or name.startswith("recombined/")
                   or name == "report.json" for name in names):
                return "multimodal-bundle"
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, OSError):
        return None
    return None


def _looks_mp4(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return len(head) >= 12 and head[4:8] == b"ftyp"


def _inspect_kind(kind: str, path: Path, tools: ToolPaths) -> tuple[list[dict], list[Issue], dict]:
    if kind == "multimodal-media":
        return _inspect_media(path, tools)
    if kind == "multimodal-bundle":
        return _inspect_bundle(path, tools)
    if kind == "multimodal-package":
        return _inspect_package(path, tools)
    if kind == "virtual-scene":
        return _inspect_virtual(path, tools)
    if kind == "broken-descriptor":
        unit = {
            "name": "scene.json",
            "role": "descriptor",
            "conclusion": CONCLUSION_INDETERMINATE,
            "reason_code": "UNREADABLE_CARRIER",
            "repairability": REPAIR_HUMAN,
            "record_count": 0,
            "candidates": [],
            "issues": [Issue("UNREADABLE_CARRIER", "error", "scene.json 无法解析，不能判断标识是否合规").to_dict()],
            "records": [],
        }
        return [unit], [], {"media_status": "unreadable", "note": "场景描述损坏"}
    return _inspect_interactive(path, tools)


def _inspect_media(path: Path, tools: ToolPaths) -> tuple[list[dict], list[Issue], dict]:
    info = probe_media(path, tools.ffprobe)
    records = _safe_records(path, tools)
    subtitle_text = _extract_subtitle(path, tools)
    strip = False
    marker = MARKER.decode("utf-8")
    if subtitle_text and marker in subtitle_text:
        strip = True
        records.extend(_records_from_text(subtitle_text.encode("utf-8"), "字幕轨正文"))
    unit = _unit_from_records("composite.mp4", "容器", records, _MEDIA_LEGACY)
    unit["strip_subtitle"] = strip
    container = info.get("format") or {}
    media = {
        "media_status": MEDIA_OK,
        "format_name": container.get("format_name"),
        "duration": container.get("duration"),
        "streams": info.get("streams") or [],
        "has_video": True,
        "note": "按多模态容器检测：画面、音频、字幕属于同一份内容文件",
    }
    return [unit], [], media


def _inspect_bundle(path: Path, tools: ToolPaths) -> tuple[list[dict], list[Issue], dict]:
    units: list[dict] = []
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in archive.namelist():
            if name.endswith("/") or name == "report.json":
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
            role = "recombined" if name.startswith("recombined/") else "component"
            units.append(_unit_from_path(name, role, target, tools))
    return units, [], {"media_status": MEDIA_OK, "note": "多模态结果包，按其中每个内容文件分别检测"}


def _inspect_package(path: Path, tools: ToolPaths) -> tuple[list[dict], list[Issue], dict]:
    units: list[dict] = []
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
        raw = archive.read("AIGC.json")
        units.append(_unit_from_records(
            "AIGC.json", "package-label", _records_from_label_json(raw), _FILE_LEGACY,
        ))
        leaked = []
        for name in archive.namelist():
            if not name.startswith("payload/") or name.endswith("/"):
                continue
            payload = Path(tmp) / Path(name).name
            payload.write_bytes(archive.read(name))
            found = _safe_records(payload, tools)
            if not found and payload.suffix.lower() in {".txt", ".vtt", ".srt", ".md"}:
                found = _records_from_text(payload.read_bytes(), name)
            if found:
                leaked.append(name)
        if leaked:
            units.append({
                "name": "payload",
                "role": "payload",
                "conclusion": CONCLUSION_NONCOMPLIANT,
                "reason_code": "DUPLICATE_RECORDS",
                "repairability": REPAIR_HUMAN,
                "record_count": len(leaked),
                "candidates": [],
                "issues": [Issue(
                    "DUPLICATE_RECORDS", "error",
                    "单文件结果包的负载里还有隐式标识，会和 AIGC.json 形成第二份：" + ", ".join(leaked),
                ).to_dict()],
                "records": [],
            })
    return units, [], {"media_status": MEDIA_OK, "note": "多模态单文件结果包"}


def _inspect_virtual(path: Path, tools: ToolPaths) -> tuple[list[dict], list[Issue], dict]:
    with zipfile.ZipFile(path) as archive:
        descriptor = json.loads(archive.read("scene.json").decode("utf-8"))
        frame = archive.read("start_frame.png")
    issues = _virtual_explicit_issues(descriptor, frame)
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "start_frame.png"
        frame_path.write_bytes(frame)
        unit = _unit_from_path("start_frame.png", "image", frame_path, tools)
    return [unit], issues, {"media_status": MEDIA_OK, "note": "虚拟场景起始画面"}


def _inspect_interactive(path: Path, tools: ToolPaths) -> tuple[list[dict], list[Issue], dict]:
    with zipfile.ZipFile(path) as archive:
        descriptor = json.loads(archive.read("scene.json").decode("utf-8"))
        content_name = descriptor.get("content_file")
        content = archive.read(content_name) if content_name and content_name in archive.namelist() else None
    issues = _interactive_explicit_issues(descriptor)
    units: list[dict] = []
    if content is not None and content_name:
        with tempfile.TemporaryDirectory() as tmp:
            content_path = Path(tmp) / Path(str(content_name)).name
            content_path.write_bytes(content)
            units.append(_unit_from_path(str(content_name), str(descriptor.get("content_role") or "text"),
                                         content_path, tools))
    elif not units:
        units.append({
            "name": "scene.json",
            "role": "explicit-only",
            "conclusion": CONCLUSION_COMPLIANT,
            "reason_code": None,
            "repairability": None,
            "record_count": 0,
            "candidates": [],
            "issues": [],
            "records": [],
        })
    return units, issues, {"media_status": MEDIA_OK, "note": "交互场景界面提示与内容文件"}


def _virtual_explicit_issues(descriptor: dict, frame: bytes) -> list[Issue]:
    spec = descriptor.get("explicit") or {}
    issues: list[Issue] = []
    if "AIGC" in descriptor:
        issues.append(Issue("DUPLICATE_RECORDS", "error", "scene.json 不应再嵌一份 AIGC 对象", "scene.json"))
    element_errors = explicit_element_errors(str(spec.get("text") or ""))
    if element_errors:
        issues.append(Issue("EXPLICIT_ELEMENTS", "error", "；".join(element_errors), "explicit.text"))
    if spec.get("position") not in VIRTUAL_POSITIONS:
        issues.append(Issue("EXPLICIT_POSITION", "error", "显式标识不在画面的边或角", "explicit.position"))
    try:
        from PIL import Image, ImageFont
        import io
        with Image.open(io.BytesIO(frame)) as image:
            image.load()
            min_side = min(image.size)
            font = ImageFont.truetype(str(spec["font_path"]), size=int(spec["font_size"]))
            bbox = font.getbbox(str(spec.get("text") or ""))
            ratio = ((bbox[3] - bbox[1]) / min_side) if min_side else 0
            if ratio + 1e-9 < 0.05:
                issues.append(Issue(
                    "EXPLICIT_SIZE", "error",
                    f"文字高度比例为 {ratio:.3f}，低于最短边的 5%", "explicit.font_size",
                ))
            matched, message = overlay_matches(image, spec)
            if not matched:
                issues.append(Issue("EXPLICIT_PIXELS", "error", message, "start_frame.png"))
    except Exception as exc:
        issues.append(Issue("UNREADABLE_CARRIER", "error", f"起始画面或显式标识无法核对：{exc}", "start_frame.png"))
    return issues


def _interactive_explicit_issues(descriptor: dict) -> list[Issue]:
    explicit = descriptor.get("explicit") or {}
    issues: list[Issue] = []
    if "AIGC" in descriptor:
        issues.append(Issue("DUPLICATE_RECORDS", "error", "界面描述不应再嵌一份 AIGC 对象", "scene.json"))
    element_errors = explicit_element_errors(str(explicit.get("text") or ""))
    if element_errors:
        issues.append(Issue("EXPLICIT_ELEMENTS", "error", "；".join(element_errors), "explicit.text"))
    placements = list(explicit.get("placements") or [])
    if not placements or any(item not in INTERACTIVE_PLACEMENTS for item in placements):
        issues.append(Issue("EXPLICIT_POSITION", "error", "显式标识的放置位置不在标准允许的范围内", "explicit.placements"))
    if explicit.get("persistent") is not True:
        issues.append(Issue("EXPLICIT_PERSISTENT", "error", "交互场景的显式标识应在服务过程中持续显示", "explicit.persistent"))
    try:
        ratio = contrast_ratio(str(explicit.get("color") or ""), str(explicit.get("background") or ""))
        if ratio + 1e-9 < 3:
            issues.append(Issue("EXPLICIT_CONTRAST", "error", f"文字与背景对比度 {ratio:.2f}:1，低于 3:1", "explicit.color"))
    except Exception as exc:
        issues.append(Issue("EXPLICIT_CONTRAST", "error", str(exc), "explicit.color"))
    return issues


def _unit_from_path(name: str, role: str, path: Path, tools: ToolPaths) -> dict:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".vtt", ".srt", ".json"} or role == "text":
        return _unit_from_records(
            name, role, _records_from_text(path.read_bytes(), name), _FILE_LEGACY,
        )
    records = _safe_records(path, tools)
    for record in records:
        if record.location:
            record.location = f"{name} / {record.location}"
    return _unit_from_records(name, role, records, _FILE_LEGACY)


def _records_from_text(data: bytes, location: str) -> list[AIGCRecord]:
    _, raws = parse_text_bytes(data)
    return [
        AIGCRecord(
            tag_key="text-trailer:AIGC",
            raw=raw,
            aigc=aigc_mod.parse_aigc(raw),
            location=location,
        )
        for raw in raws
    ]


def _records_from_label_json(data: bytes) -> list[AIGCRecord]:
    trailed = _records_from_text(data, "AIGC.json")
    if trailed:
        return trailed
    text = data.decode("utf-8", "replace").strip()
    if not text:
        return []
    return [AIGCRecord(
        tag_key="text-trailer:AIGC",
        raw=text,
        aigc=aigc_mod.parse_aigc(text),
        location="AIGC.json",
    )]


def _safe_records(path: Path, tools: ToolPaths) -> list[AIGCRecord]:
    if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return _image_records(path)
    try:
        return read_aigc_records(str(path), exiftool=tools.exiftool, config=tools.exiftool_config)
    except (ReaderError, OSError):
        return []


def _image_records(path: Path) -> list[AIGCRecord]:
    """图片沿用单模态的分段读取，同名 XMP 块不会被合并成一份。"""
    from ..metadata.xmp_reader import read_aigc_records as read_image_records
    try:
        found = read_image_records(str(path))
    except Exception:
        return []
    records: list[AIGCRecord] = []
    for item in found:
        name = item.property_name or "XMP:AIGC"
        tag = name if name.endswith(":AIGC") or "AIGC" in name else f"XMP:{name}"
        if not tag.endswith(":AIGC") and "AIGC" in tag:
            tag = "XMP:AIGC"
        records.append(AIGCRecord(
            tag_key="XMP:AIGC" if "AIGC" in tag else tag,
            raw=item.raw_value,
            aigc=item.aigc if isinstance(getattr(item, "aigc", None), dict) else None,
            location=item.packet_location,
        ))
    return records


def _unit_from_records(name: str, role: str, records: list[AIGCRecord], legacy: LegacyRule) -> dict:
    classified = classify_records(records, legacy)
    repairability = classified["repairability"]
    if repairability == REPAIR_AUTO:
        draft = _planner(records, CONCLUSION_NONCOMPLIANT)
        if not draft.executable:
            repairability = REPAIR_HUMAN
            classified["issues"].append(Issue(
                "REPAIR_BLOCKED", "warn",
                "；".join(draft.blocking_reasons) or "该问题不能按单模态规则自动修复",
            ))
    return {
        "name": name,
        "role": role,
        "conclusion": classified["conclusion"],
        "reason_code": classified["reason_code"],
        "repairability": repairability,
        "record_count": len(records),
        "candidates": [candidate_entry(record) for record in records],
        "issues": [issue.to_dict() for issue in classified["issues"]],
        "records": [
            {"tag_key": record.tag_key, "raw": record.raw, "aigc": record.aigc, "location": record.location}
            for record in records
        ],
    }


class _PlannerRecord:
    def __init__(self, *, raw_value: str, document, parse_error: str | None):
        self.raw_value = raw_value
        self.document = document
        self.parse_error = parse_error


def _planner(records: list[AIGCRecord], conclusion: str):
    raw = []
    for record in records:
        document = {"AIGC": record.aigc} if isinstance(record.aigc, dict) else None
        raw.append(_PlannerRecord(
            raw_value=record.raw or "",
            document=document,
            parse_error=None if document else "BAD_JSON",
        ))
    inspection = MetadataComplianceResult.model_construct(
        conclusion=ComplianceConclusion(conclusion),
        reason_codes=[],
        repairability=Repairability.MANUAL_REVIEW,
        detected_format="composite",
        mime_type="application/octet-stream",
        file_sha256="",
        content_fingerprint="",
        record_count=len(records),
        project_policy=ProjectPolicyResult(accepted=True),
        cross_reader=CrossReaderResult(status="not_applicable"),
        source_verification=SourceVerificationResult(status="not_applicable"),
        c2pa_presence=C2PAPresencePayload(status=C2PAPresence.NOT_FOUND),
        raw_records=raw,
    )
    return RepairPlanner(require_cross_reader=False).plan(inspection)


def _plan_unit(unit: dict) -> tuple[dict, list[str], list[str], list[dict]]:
    records = [
        AIGCRecord(
            tag_key=item["tag_key"], raw=item.get("raw") or "",
            aigc=item.get("aigc"), location=item.get("location"),
        )
        for item in unit.get("records") or []
    ]
    if unit.get("conclusion") in {CONCLUSION_COMPLIANT, None} and unit.get("repairability") is None:
        return {"name": unit["name"], "role": unit.get("role"), "proposed": None}, [], [], []
    if unit.get("conclusion") == CONCLUSION_NOT_FOUND:
        return (
            {"name": unit["name"], "role": unit.get("role"), "proposed": None},
            [],
            [f"{unit['name']} 未发现标识；如需新增应进入普通标注流程"],
            [],
        )
    if unit.get("repairability") != REPAIR_AUTO or not records:
        reasons = [issue["message"] for issue in unit.get("issues") or [] if issue.get("severity") == "error"]
        return (
            {"name": unit["name"], "role": unit.get("role"), "proposed": None},
            [],
            reasons or [f"{unit['name']} 需要人工处理"],
            [],
        )
    draft = _planner(records, CONCLUSION_NONCOMPLIANT)
    changes = [item.model_dump(mode="json") for item in draft.field_changes]
    if not draft.executable or not draft.proposed_document:
        return (
            {"name": unit["name"], "role": unit.get("role"), "proposed": None},
            [],
            list(draft.blocking_reasons) or [f"{unit['name']} 不能自动修复"],
            changes,
        )
    actions = [f"{unit['name']}：{action}" for action in draft.actions]
    return (
        {"name": unit["name"], "role": unit.get("role"), "proposed": draft.proposed_document},
        actions,
        [],
        changes,
    )


def _assemble(kind, units, explicit_issues, media, path, *, file_name, size_bytes, sha256,
              request_id, tools, elapsed_ms) -> dict:
    conclusions = [unit["conclusion"] for unit in units]
    if explicit_issues:
        if any(issue.severity == "error" and issue.code == "UNREADABLE_CARRIER" for issue in explicit_issues):
            conclusions.append(CONCLUSION_INDETERMINATE)
        elif any(issue.severity == "error" for issue in explicit_issues):
            conclusions.append(CONCLUSION_NONCOMPLIANT)
    if not conclusions:
        conclusion = CONCLUSION_INDETERMINATE
    elif any(item == CONCLUSION_INDETERMINATE for item in conclusions):
        conclusion = CONCLUSION_INDETERMINATE
    elif any(item == CONCLUSION_NONCOMPLIANT for item in conclusions):
        conclusion = CONCLUSION_NONCOMPLIANT
    elif any(item == CONCLUSION_NOT_FOUND for item in conclusions) and any(
            item == CONCLUSION_COMPLIANT for item in conclusions):
        conclusion = CONCLUSION_NONCOMPLIANT
    elif all(item == CONCLUSION_NOT_FOUND for item in conclusions):
        conclusion = CONCLUSION_NOT_FOUND
    elif all(item == CONCLUSION_COMPLIANT for item in conclusions):
        conclusion = CONCLUSION_COMPLIANT
    else:
        conclusion = CONCLUSION_NOT_FOUND

    reason = None
    if conclusion == CONCLUSION_NONCOMPLIANT:
        reason = next((unit.get("reason_code") for unit in units if unit.get("reason_code")), None)
        if explicit_issues and reason is None:
            reason = explicit_issues[0].code
        if reason is None and CONCLUSION_NOT_FOUND in conclusions:
            reason = "MISSING_FIELD"
    if conclusion == CONCLUSION_INDETERMINATE:
        reason = "UNREADABLE_CARRIER"

    repairability = None
    if conclusion == CONCLUSION_NONCOMPLIANT:
        blocked = explicit_issues or any(
            unit.get("repairability") == REPAIR_HUMAN or unit.get("conclusion") == CONCLUSION_NOT_FOUND
            for unit in units
        )
        auto = any(unit.get("repairability") == REPAIR_AUTO for unit in units)
        repairability = REPAIR_HUMAN if blocked or not auto else REPAIR_AUTO

    issues: list[Issue] = []
    candidates = []
    for unit in units:
        for issue in unit.get("issues") or []:
            issues.append(Issue(issue["code"], issue["severity"], f"{unit['name']}：{issue['message']}", issue.get("field")))
        candidates.extend(unit.get("candidates") or [])
    issues.extend(explicit_issues)
    produce_id = None
    for unit in units:
        for record in unit.get("records") or []:
            aigc = record.get("aigc") or {}
            if isinstance(aigc, dict) and aigc.get("ProduceID"):
                produce_id = aigc["ProduceID"]
                break
    registry = {"mode": "local_produceid", "reason": "仅按 ProduceID 是否在本系统出现过核对"}
    if produce_id and tools.registry_path:
        try:
            registry = {
                "mode": "local_produceid",
                "produce_id": produce_id,
                "known": bool(build_identifier_lookup(tools.registry_path)(produce_id)),
                "reason": "仅按 ProduceID 是否在本系统出现过核对；指纹级核对待登记库完善",
            }
        except Exception:
            registry["produce_id"] = produce_id
    strip = any(unit.get("strip_subtitle") for unit in units)
    public_units = [{key: value for key, value in unit.items() if key != "records"} | {
        "records": unit.get("records") or [],
    } for unit in units]
    report = build_report(
        bmff=bmff_not_applicable("多模态与场景按内容文件检测，不单看 BMFF box"),
        media=media,
        candidates=candidates,
        issues=issues,
        conclusion=conclusion,
        reason_code=reason,
        repairability=repairability,
        c2pa="absent",
        confidence=CONFIDENCE_HIGH if conclusion == CONCLUSION_COMPLIANT else CONFIDENCE_LOW,
        registry=registry,
        file_name=file_name,
        size_bytes=size_bytes,
        sha256=sha256,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        detected_mime="application/zip" if zipfile.is_zipfile(path) else "video/mp4",
        detector_version="composite-compliance-1",
        exiftool_version_value=None,
        extra={
            "subject": {
                "kind": kind,
                "label": _KIND_LABEL[kind],
                "strip_subtitle": strip,
            },
            "units": public_units,
            "explicit_issues": [issue.to_dict() for issue in explicit_issues],
        },
    )
    return report


def _extract_subtitle(path: Path, tools: ToolPaths) -> str | None:
    info = probe_media(path, tools.ffprobe)
    if not any(stream.get("codec_type") == "subtitle" for stream in info.get("streams") or []):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "subtitle.vtt"
        proc = subprocess.run(
            [tools.ffmpeg, "-y", "-i", str(path), "-map", "0:s:0", str(target)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not target.is_file():
            return None
        return target.read_text(encoding="utf-8", errors="replace")


def _repair_media(src: Path, dest: Path, proposed: dict, strip_subtitle: bool, tools: ToolPaths) -> None:
    document = proposed.get("composite.mp4")
    current_src = src
    if document:
        label_file("video", src, dest, document["AIGC"], "replace", tools)
        current_src = dest
    elif src != dest:
        shutil.copyfile(src, dest)
        current_src = dest
    if strip_subtitle:
        cleaned = dest.with_suffix(".clean.mp4")
        _strip_subtitle(current_src, cleaned, tools)
        cleaned.replace(dest)


def _strip_subtitle(src: Path, dest: Path, tools: ToolPaths) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subtitle = root / "subtitle.vtt"
        proc = subprocess.run(
            [tools.ffmpeg, "-y", "-i", str(src), "-map", "0:s:0", str(subtitle)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise AdapterError(f"无法抽出字幕: {proc.stderr[-400:]}")
        text = subtitle.read_text(encoding="utf-8", errors="replace")
        marker = MARKER.decode("utf-8")
        if marker in text:
            text = text.split(marker)[0].rstrip() + "\n"
            subtitle.write_text(text, encoding="utf-8")
        proc = subprocess.run(
            [tools.ffmpeg, "-y", "-i", str(src), "-i", str(subtitle),
             "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0",
             "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
             str(dest)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise AdapterError(f"去掉字幕标识后无法封装: {proc.stderr[-400:]}")


def _repair_zip(src: Path, dest: Path, kind: str, proposed: dict, tools: ToolPaths) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(src) as archive:
            archive.extractall(root)
        for name, document in proposed.items():
            target = root / name
            if not target.is_file():
                raise ValueError(f"方案指向的文件不在包内: {name}")
            role = _role_for(kind, name)
            rewritten = target.with_suffix(target.suffix + ".rewritten")
            if name == "AIGC.json":
                rewritten.write_text(aigc_mod.serialize_aigc(document["AIGC"]), encoding="ascii")
            elif target.suffix.lower() in {".txt", ".vtt", ".srt"}:
                label_file("text", target, rewritten, document["AIGC"], "replace", tools)
            elif target.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                label_file("image", target, rewritten, document["AIGC"], "replace", tools)
            elif target.suffix.lower() in {".mp4", ".mov", ".m4a"}:
                label_file("video" if target.suffix.lower() != ".m4a" else "audio",
                           target, rewritten, document["AIGC"], "replace", tools)
            else:
                label_file(role, target, rewritten, document["AIGC"], "replace", tools)
            rewritten.replace(target)
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(root.rglob("*")):
                if item.is_file():
                    archive.write(item, item.relative_to(root).as_posix())


def _role_for(kind: str, name: str) -> str:
    if kind == "virtual-scene":
        return "image"
    suffix = Path(name).suffix.lower()
    if suffix in {".txt", ".vtt", ".srt", ".json"}:
        return "text"
    if suffix in {".mp3", ".m4a", ".wav"}:
        return "audio"
    if suffix in {".mp4", ".mov"}:
        return "video"
    return "image"


def plan_hash(file_sha256: str, plan: dict) -> str:
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((file_sha256 + payload).encode("utf-8")).hexdigest()
