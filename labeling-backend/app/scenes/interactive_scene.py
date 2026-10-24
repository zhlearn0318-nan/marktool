"""交互场景界面标识（GB 45438—2025 第 5.6 条，内容文件另按第 6.1 条）。

交互场景的显式标识在界面上，不烧进内容像素。描述文件记录提示文字、
放置方式（内容附近，和/或界面顶部、底部、背景）以及持续显示。
若本次交互附带可下载的内容文件，该文件按对应模态写入恰好一份隐式标识。
界面描述本身不是内容文件，不再嵌一份 AIGC JSON。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..labeling_error import LabelingError
from ..modalities.dispatch import (
    all_passed, check_item, label_file, parse_file, prepare_aigc,
)
from ..tools import ToolPaths
from .explicit import (
    INTERACTIVE_PLACEMENTS, assert_interactive_explicit, contrast_ratio, explicit_element_errors,
)

SCENE_JSON = "scene.json"
PACKAGE_NAME = "interactive-scene.zip"

_SUFFIX = {"text": ".txt", "image": ".png", "audio": ".m4a", "video": ".mp4"}


def _content_name(role: str, source: Path) -> str:
    suffix = source.suffix.lower()
    if role == "text" and suffix in {".txt", ".vtt", ".srt", ".md"}:
        return "content" + suffix
    if role == "image" and suffix in {".png", ".jpg", ".jpeg"}:
        return "content" + (".jpg" if suffix in {".jpg", ".jpeg"} else ".png")
    if role == "audio":
        return "content.m4a"
    if role == "video":
        return "content.mp4"
    return "content" + _SUFFIX[role]


def label_interactive_scene(
        output_dir: str | Path,
        *,
        text: str = "AI生成",
        placements: list[str] | None = None,
        persistent: bool = True,
        color: str = "#102033",
        background: str = "#F4EBD4",
        content: str | Path | None = None,
        content_role: str | None = None,
        aigc_obj: dict | None = None,
        policy: str = "reject",
        tools: ToolPaths | None = None,
) -> dict:
    tools = tools or ToolPaths()
    chosen = list(placements or ["near_content"])
    ratio = assert_interactive_explicit(text, chosen, persistent, color, background)
    work = Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    content_file = None
    prepared = None
    if content is not None:
        if content_role not in {"text", "image", "audio", "video"}:
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "内容文件需要声明 text、image、audio 或 video。")
        if aigc_obj is None:
            raise LabelingError("AIGC_SCHEMA_INVALID", "为内容文件写入隐式标识时需要 AIGC 对象。")
        prepared = prepare_aigc(aigc_obj)
        source = Path(content)
        if content_role == "audio":
            from ..multimodal.ffmpeg_util import normalize_to_m4a
            normalized = work / "audio-normalized.m4a"
            normalize_to_m4a(source, normalized, ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe)
            source = normalized
        content_file = _content_name(content_role, source)
        label_file(content_role, source, work / content_file, prepared, policy, tools)
    descriptor = {
        "standard": "GB45438-2025",
        "scene_type": "interactive",
        "clause": "5.6",
        "explicit": {
            "text": text,
            "placements": chosen,
            "persistent": True,
            "color": color,
            "background": background,
            "contrast_ratio": round(ratio, 3),
        },
        "content_file": content_file,
        "content_role": content_role,
    }
    (work / SCENE_JSON).write_text(json.dumps(descriptor, ensure_ascii=False, indent=2), encoding="utf-8")
    package = work / PACKAGE_NAME
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(work / SCENE_JSON, SCENE_JSON)
        if content_file:
            archive.write(work / content_file, content_file)
    report = verify_interactive_scene(package, tools, expected_aigc=prepared)
    report["operation"] = "interactive_label"
    report["package"] = PACKAGE_NAME
    if not report["passed"]:
        raise LabelingError("VERIFY_FAILED", "交互场景写入后未通过校验。", report["checks"])
    return report


def _load_descriptor(path: Path) -> tuple[dict, Path | None, Path]:
    """返回描述、解出的内容文件（可空）、以及可清理的临时目录。"""
    if path.suffix.lower() == ".json" and not zipfile.is_zipfile(path):
        descriptor = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(descriptor, dict):
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "交互场景描述必须是 JSON 对象。")
        return descriptor, None, path.parent
    if not zipfile.is_zipfile(path):
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "交互场景应为 scene.json 或场景包 zip。")
    extract = path.with_name(path.stem + ".unpacked")
    extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        _safe_extract(archive, extract)
    descriptor_path = extract / SCENE_JSON
    if not descriptor_path.is_file():
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "场景包缺少 scene.json。")
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    content_name = descriptor.get("content_file")
    content_path = extract / content_name if content_name else None
    if content_path is not None and not content_path.is_file():
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "场景包声明了内容文件，但包内没有该文件。")
    return descriptor, content_path, extract


def _safe_extract(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in archive.infolist():
        target = (dest / member.filename).resolve()
        if target != root and root not in target.parents:
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "场景包路径越界。")
    archive.extractall(dest)


def verify_interactive_scene(path: str | Path, tools: ToolPaths | None = None,
                             expected_aigc: dict | None = None) -> dict:
    tools = tools or ToolPaths()
    descriptor, content_path, _extract = _load_descriptor(Path(path))
    if descriptor.get("scene_type") != "interactive":
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "这不是交互场景描述。")
    if "AIGC" in descriptor:
        raise LabelingError("AIGC_DUPLICATE_RECORDS", "界面描述不应再嵌一份 AIGC 对象。")
    explicit = descriptor.get("explicit") or {}
    text = str(explicit.get("text") or "")
    placements = list(explicit.get("placements") or [])
    persistent = explicit.get("persistent") is True
    checks: list[dict] = []
    element_errors = explicit_element_errors(text)
    checks.append(check_item(
        "explicit_elements", "5.6.b", not element_errors,
        "界面提示同时包含人工智能要素和生成合成要素" if not element_errors else "；".join(element_errors),
    ))
    placement_ok = bool(placements) and all(item in INTERACTIVE_PLACEMENTS for item in placements)
    checks.append(check_item(
        "explicit_placement", "5.6.c", placement_ok,
        "提示位于内容附近和/或界面顶部、底部、背景" if placement_ok
        else "提示放置方式不符合第 5.6.c 条",
    ))
    checks.append(check_item(
        "explicit_persistent", "5.6.c", persistent,
        "提示持续显示" if persistent else "交互场景界面显式标识应持续显示",
    ))
    try:
        ratio = contrast_ratio(str(explicit.get("color") or ""), str(explicit.get("background") or ""))
        contrast_ok = ratio >= 3.0
        contrast_message = f"文字与背景对比度 {ratio:.2f}:1" if contrast_ok else f"对比度 {ratio:.2f}:1，不足以清晰可辨"
    except LabelingError as exc:
        contrast_ok = False
        contrast_message = exc.message
    checks.append(check_item("explicit_legibility", "5.6.d", contrast_ok, contrast_message))
    labels: list[dict] = []
    role = descriptor.get("content_role")
    if content_path is None:
        checks.append(check_item(
            "implicit_label", "6.1", True,
            "本次未附内容文件。隐式标识作用于随交互提供的内容文件，界面描述本身不写入第二份标识。",
            severity="warning",
        ))
    else:
        if role not in {"text", "image", "audio", "video"}:
            checks.append(check_item("implicit_label", "6.1", False, "内容文件缺少模态声明。"))
        else:
            implicit = parse_file(role, content_path, tools)
            labels = [item["aigc"] for item in implicit if item.get("aigc")]
            single = len(implicit) == 1 and len(labels) == 1
            from ..core import aigc as aigc_mod
            schema_ok = single and not aigc_mod.validate_aigc(labels[0])
            checks.append(check_item(
                "single_implicit_label", "6.1.c", single,
                "内容文件仅保留一份隐式标识" if single else f"内容文件检出 {len(implicit)} 份隐式标识",
            ))
            checks.append(check_item(
                "appendix_e_schema", "附录E", schema_ok,
                "内容文件隐式标识符合附录 E" if schema_ok else "内容文件隐式标识不符合附录 E",
            ))
            if expected_aigc is not None and schema_ok:
                matched = labels[0] == expected_aigc
                checks.append(check_item(
                    "readback_matches", "附录E", matched,
                    "回读结果与写入对象一致" if matched else "回读结果与写入对象不一致",
                ))
    return {
        "standard": "GB45438-2025",
        "operation": "interactive_verify",
        "scene_type": "interactive",
        "passed": all_passed(checks),
        "explicit": {
            "text": text,
            "placements": placements,
            "persistent": persistent,
            "color": explicit.get("color"),
            "background": explicit.get("background"),
        },
        "content_role": role,
        "implicit_label": labels[0] if len(labels) == 1 else None,
        "implicit_labels": labels,
        "checks": checks,
    }


def parse_interactive_scene(path: str | Path, tools: ToolPaths | None = None) -> dict:
    report = verify_interactive_scene(path, tools)
    return {
        "scene_type": "interactive",
        "explicit": report.get("explicit"),
        "implicit_labels": report.get("implicit_labels") or [],
        "count": len(report.get("implicit_labels") or []),
    }
