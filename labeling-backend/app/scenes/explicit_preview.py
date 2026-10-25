"""场景显式提示的修复预览。

第 5.5 / 5.6 条不合格时，先在原画面或界面示意上另叠一条合规提示，并列出
会改哪些文件。这一步不把场景包交给调用方。确认预览 id 之后，才把已经
生成的包写出去。原来的不合格提示保留：虚拟场景留在像素里，交互场景留在
``previous_explicit``。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..metadata.timeutil import iso_utc, utc_now
from ..tools import ToolPaths
from .explicit import (
    INTERACTIVE_PLACEMENTS,
    MIN_CONTRAST,
    VIRTUAL_POSITIONS,
    contrast_ratio,
    explicit_element_errors,
)
from .overlay import _place, draw_explicit_label, find_font

PREVIEW_TTL_HOURS = 24
QUIET_STDDEV = 18.0
NOTICE = "原有不合格提示不会删除。确认写入后，画面或场景配置里会同时保留原来的提示和新的合规提示。"
_PREVIEW_ID = re.compile(r"^expv_[0-9a-f]{20}$")
_DEFAULT_COLOR = "#FFFFFF"
_DEFAULT_BACKGROUND = "#102033"
_POSITION_ZH = {
    "top_left": "左上角",
    "top_right": "右上角",
    "bottom_left": "左下角",
    "bottom_right": "右下角",
    "top_edge": "上边",
    "bottom_edge": "下边",
    "left_edge": "左边",
    "right_edge": "右边",
}
_PLACEMENT_ZH = {
    "near_content": "内容附近",
    "interface_top": "界面顶部",
    "interface_bottom": "界面底部",
    "interface_background": "界面背景",
}
_PLACEMENT_POSITION = {
    "interface_top": "top_edge",
    "interface_bottom": "bottom_edge",
    "near_content": "bottom_left",
    "interface_background": "right_edge",
}


class ExplicitPreviewError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def build_explicit_preview(path: Path, tools: ToolPaths) -> dict:
    """读场景包，生成预览图、改动清单和待确认的新包字节。不改传入的文件。"""
    kind, descriptor, members = _read_scene(path)
    font = find_font(tools.font_path)
    if kind == "virtual-scene":
        built = _virtual_preview(descriptor, members["start_frame.png"], font, tools)
    else:
        built = _interactive_preview(descriptor, members, font)
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = {
        "kind": kind,
        "text": built["explicit"].get("text"),
        "position": built["explicit"].get("position"),
        "placements": built["explicit"].get("placements"),
        "color": built["explicit"].get("color"),
        "background": built["explicit"].get("background"),
        "font_size": built["explicit"].get("font_size"),
        "box": built["explicit"].get("box"),
        "persistent": built["explicit"].get("persistent"),
        "file_sha256": file_sha,
    }
    preview_id = "expv_" + hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    built["preview_id"] = preview_id
    built["kind"] = kind
    built["file_sha256"] = file_sha
    return built


def save_preview(root: Path, built: dict, *, file_name: str) -> dict:
    """把预览落在存储目录。同一个内容和同一套改动得到同一个 id，并刷新有效期。"""
    preview_id = built["preview_id"]
    folder = root / preview_id
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / "before.png").write_bytes(built["before_png"])
    (folder / "after.png").write_bytes(built["after_png"])
    (folder / "proposed.zip").write_bytes(built["proposed_zip"])
    expires = utc_now() + timedelta(hours=PREVIEW_TTL_HOURS)
    record = {
        "preview_id": preview_id,
        "expires_at": iso_utc(expires),
        "kind": built["kind"],
        "file_name": file_name,
        "file_sha256": built["file_sha256"],
        "notice": NOTICE,
        "position_rationale": built["position_rationale"],
        "changes": built["changes"],
    }
    (folder / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    record["before_url"] = f"/api/v1/scene-explicit-previews/{preview_id}/before"
    record["after_url"] = f"/api/v1/scene-explicit-previews/{preview_id}/after"
    return record


def load_preview(root: Path, preview_id: str) -> dict:
    if not _PREVIEW_ID.match(preview_id or ""):
        raise ExplicitPreviewError("INVALID_PREVIEW_ID", "预览 id 无效。")
    folder = root / preview_id
    record_path = folder / "record.json"
    proposed = folder / "proposed.zip"
    if not record_path.is_file() or not proposed.is_file():
        raise ExplicitPreviewError("INVALID_PREVIEW_ID", "预览不存在。")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    try:
        expires = datetime.fromisoformat(str(record.get("expires_at", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExplicitPreviewError("PREVIEW_EXPIRED", "修复预览已过期，请重新生成。") from exc
    if utc_now() >= expires:
        raise ExplicitPreviewError("PREVIEW_EXPIRED", "修复预览已过期，请重新生成。")
    record["proposed_path"] = str(proposed)
    return record


def choose_overlay_position(image: Image.Image, *, text: str, color: str, background: str,
                            font: Path, preferred: str | None, old_box: list | None) -> tuple[Image.Image, dict, str]:
    """在八个边角里放一条新提示。原位置会挡住旧提示或关键内容时，改到最不遮挡的一处。"""
    probe, probe_spec = draw_explicit_label(
        image, text=text, position="bottom_right", color=color, background=background, font_path=font,
    )
    box_w, box_h = int(probe_spec["box"][2]), int(probe_spec["box"][3])
    margin = int(probe_spec["margin"])
    width, height = image.size
    details: dict[str, dict] = {}
    for position in VIRTUAL_POSITIONS:
        x, y = _place(position, width, height, box_w, box_h, margin)
        rect = (x, y, box_w, box_h)
        stddev = _stddev(image, rect)
        overlap = _overlap_ratio(rect, old_box)
        details[position] = {"stddev": stddev, "overlap": overlap}
    legal = preferred in VIRTUAL_POSITIONS
    best = min(
        VIRTUAL_POSITIONS,
        key=lambda item: (details[item]["overlap"], details[item]["stddev"], VIRTUAL_POSITIONS.index(item)),
    )
    keep = (
        legal
        and details[preferred]["overlap"] <= details[best]["overlap"] + 1e-9
        and details[preferred]["stddev"] <= QUIET_STDDEV
        and details[preferred]["stddev"] <= details[best]["stddev"] + 1e-6
    )
    chosen = preferred if keep else best
    if chosen == "bottom_right":
        labeled, spec = probe, probe_spec
    else:
        labeled, spec = draw_explicit_label(
            image, text=text, position=chosen, color=color, background=background, font_path=font,
        )
    return labeled, spec, _position_rationale(preferred, chosen, details, legal)


def _virtual_preview(descriptor: dict, frame: bytes, font: Path, tools: ToolPaths) -> dict:
    try:
        with Image.open(io.BytesIO(frame)) as opened:
            image = opened.convert("RGB")
    except Exception as exc:
        raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", f"起始画面无法读取：{exc}") from exc
    old = dict(descriptor.get("explicit") or {})
    text = _repair_text(str(old.get("text") or ""))
    color, background = _repair_colors(old.get("color"), old.get("background"))
    old_box = old.get("box") if isinstance(old.get("box"), list) and len(old.get("box")) == 4 else None
    labeled, spec, rationale = choose_overlay_position(
        image, text=text, color=color, background=background, font=font,
        preferred=str(old.get("position") or ""), old_box=old_box,
    )
    spec = dict(spec)
    spec["repair_basis"] = rationale
    spec["repair_note"] = NOTICE
    before = _png_bytes(image)
    after_image_bytes = _png_bytes(labeled)
    after = _with_original_xmp(frame, after_image_bytes, tools)
    updated = dict(descriptor)
    updated["previous_explicit"] = old
    updated["explicit"] = spec
    return {
        "before_png": before,
        "after_png": after_image_bytes,
        "proposed_zip": _zip_members({"scene.json": _json_bytes(updated), "start_frame.png": after}),
        "explicit": spec,
        "position_rationale": rationale,
        "changes": _virtual_changes(old, spec),
    }


def _interactive_preview(descriptor: dict, members: dict[str, bytes], font: Path) -> dict:
    old = dict(descriptor.get("explicit") or {})
    text = _repair_text(str(old.get("text") or ""))
    color, background = _repair_colors(old.get("color"), old.get("background"))
    base = _interface_canvas(members)
    old_rect = _draw_old_chip(base, old)
    labeled, placement, rationale = _choose_interactive_placement(
        base, text=text, color=color, background=background, font=font,
        preferred=_first_legal(old.get("placements")), old_rect=old_rect,
    )
    explicit = {
        "text": text,
        "placements": [placement],
        "persistent": True,
        "color": color,
        "background": background,
        "contrast_ratio": round(contrast_ratio(color, background), 3),
        "repair_basis": rationale,
        "repair_note": NOTICE,
    }
    updated = dict(descriptor)
    updated["previous_explicit"] = old
    updated["explicit"] = explicit
    replacements = {"scene.json": _json_bytes(updated)}
    proposed_members = dict(members)
    proposed_members["scene.json"] = replacements["scene.json"]
    return {
        "before_png": _png_bytes(_interface_with_old_only(members, old)),
        "after_png": _png_bytes(labeled),
        "proposed_zip": _zip_members(proposed_members),
        "explicit": explicit,
        "position_rationale": rationale,
        "changes": _interactive_changes(old, explicit),
    }


def _choose_interactive_placement(image: Image.Image, *, text: str, color: str, background: str,
                                  font: Path, preferred: str | None,
                                  old_rect: tuple[int, int, int, int] | None) -> tuple[Image.Image, str, str]:
    probe, probe_spec = draw_explicit_label(
        image, text=text, position="bottom_edge", color=color, background=background, font_path=font,
    )
    box_w, box_h = int(probe_spec["box"][2]), int(probe_spec["box"][3])
    margin = int(probe_spec["margin"])
    width, height = image.size
    details: dict[str, dict] = {}
    rects: dict[str, tuple[int, int, int, int]] = {}
    for placement, position in _PLACEMENT_POSITION.items():
        x, y = _place(position, width, height, box_w, box_h, margin)
        rect = (x, y, box_w, box_h)
        rects[placement] = rect
        details[placement] = {
            "stddev": _stddev(image, rect),
            "overlap": _overlap_ratio(rect, list(old_rect) if old_rect else None),
        }
    order = list(_PLACEMENT_POSITION)
    legal = preferred in INTERACTIVE_PLACEMENTS
    best = min(order, key=lambda item: (details[item]["overlap"], details[item]["stddev"], order.index(item)))
    keep = (
        legal
        and details[preferred]["overlap"] <= details[best]["overlap"] + 1e-9
        and details[preferred]["stddev"] <= QUIET_STDDEV
        and details[preferred]["stddev"] <= details[best]["stddev"] + 1e-6
    )
    chosen = preferred if keep else best
    if chosen == "interface_bottom":
        labeled = probe
    else:
        labeled, _spec = draw_explicit_label(
            image, text=text, position=_PLACEMENT_POSITION[chosen],
            color=color, background=background, font_path=font,
        )
    return labeled, chosen, _placement_rationale(preferred, chosen, details, legal)


def _read_scene(path: Path) -> tuple[str, dict, dict[str, bytes]]:
    if not zipfile.is_zipfile(path):
        raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", "显式提示预览只接受虚拟场景包或交互场景包。")
    try:
        with zipfile.ZipFile(path) as archive:
            if "scene.json" not in archive.namelist():
                raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", "场景包里没有 scene.json。")
            descriptor = json.loads(archive.read("scene.json").decode("utf-8"))
            members = {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            }
    except ExplicitPreviewError:
        raise
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, OSError) as exc:
        raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", f"场景包无法读取：{exc}") from exc
    if not isinstance(descriptor, dict):
        raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", "scene.json 不是对象。")
    kind = descriptor.get("scene_type")
    if kind == "virtual" and "start_frame.png" in members:
        issues = explicit_element_errors(str((descriptor.get("explicit") or {}).get("text") or ""))
        position = (descriptor.get("explicit") or {}).get("position")
        if not issues and position in VIRTUAL_POSITIONS and _virtual_pixels_ok(descriptor, members["start_frame.png"]):
            raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", "当前显式提示没有需要预览的问题。")
        return "virtual-scene", descriptor, members
    if kind == "interactive":
        if _interactive_ok(descriptor.get("explicit") or {}):
            raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", "当前显式提示没有需要预览的问题。")
        return "interactive-scene", descriptor, members
    raise ExplicitPreviewError("EXPLICIT_PREVIEW_UNAVAILABLE", "显式提示预览只接受虚拟场景包或交互场景包。")


def _virtual_pixels_ok(descriptor: dict, frame: bytes) -> bool:
    """字高和像素都已经符合记录时，不再做预览。读不了则交给后面的修复判断。"""
    spec = descriptor.get("explicit") or {}
    try:
        from .overlay import overlay_matches
        with Image.open(io.BytesIO(frame)) as image:
            image.load()
            min_side = min(image.size)
            font = ImageFont.truetype(str(spec["font_path"]), size=int(spec["font_size"]))
            bbox = font.getbbox(str(spec.get("text") or ""))
            ratio = ((bbox[3] - bbox[1]) / min_side) if min_side else 0
            if ratio + 1e-9 < 0.05 or spec.get("min_side") != min_side:
                return False
            matched, _message = overlay_matches(image, spec)
            return matched
    except Exception:
        return False


def _interactive_ok(explicit: dict) -> bool:
    text = str(explicit.get("text") or "")
    placements = list(explicit.get("placements") or [])
    if explicit_element_errors(text) or not text.strip():
        return False
    if not placements or any(item not in INTERACTIVE_PLACEMENTS for item in placements):
        return False
    if explicit.get("persistent") is not True:
        return False
    try:
        return contrast_ratio(str(explicit.get("color") or ""), str(explicit.get("background") or "")) + 1e-9 >= MIN_CONTRAST
    except Exception:
        return False


def _repair_text(current: str) -> str:
    text = " ".join(str(current or "").split())
    if text and not explicit_element_errors(text):
        return text
    if "人工智能" not in text and "AI" not in text:
        text = f"{text} 人工智能".strip()
    if "生成" not in text and "合成" not in text:
        text = f"{text} 生成合成".strip()
    return text or "人工智能生成合成"


def _repair_colors(color, background) -> tuple[str, str]:
    try:
        if contrast_ratio(str(color or ""), str(background or "")) + 1e-9 >= MIN_CONTRAST:
            return str(color), str(background)
    except Exception:
        pass
    return _DEFAULT_COLOR, _DEFAULT_BACKGROUND


def _stddev(image: Image.Image, rect: tuple[int, int, int, int]) -> float:
    x, y, width, height = rect
    crop = image.convert("L").crop((x, y, x + width, y + height))
    pixels = crop.tobytes()
    if not pixels:
        return 0.0
    mean = sum(pixels) / len(pixels)
    variance = sum((pixel - mean) ** 2 for pixel in pixels) / len(pixels)
    return variance ** 0.5


def _overlap_ratio(rect: tuple[int, int, int, int], old: list | None) -> float:
    if not old:
        return 0.0
    ax, ay, aw, ah = rect
    bx, by, bw, bh = (int(value) for value in old)
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1 or aw <= 0 or ah <= 0:
        return 0.0
    return ((x2 - x1) * (y2 - y1)) / float(aw * ah)


def _position_rationale(preferred: str | None, chosen: str, details: dict, legal: bool) -> str:
    chosen_name = _POSITION_ZH[chosen]
    quiet = details[chosen]["stddev"]
    if legal and chosen == preferred:
        return (
            f"原位置{_POSITION_ZH[preferred]}的画面起伏为 {details[preferred]['stddev']:.1f}，"
            f"没有挡住关键内容，也没有压住原来的提示，因此新提示仍放在该边或角。"
            f"原提示保留，画面上会同时看到新旧两条。"
        )
    if not legal:
        lead = f"原位置「{preferred or '未设置'}」不在画面的边或角，不能作为第 5.5 条的合规位置。"
    elif details[preferred]["overlap"] > 0:
        lead = f"原位置{_POSITION_ZH[preferred]}上已有提示，再叠一条会挡住原来的文字。"
    else:
        lead = (
            f"原位置{_POSITION_ZH[preferred]}的画面起伏为 {details[preferred]['stddev']:.1f}，"
            f"高于 {QUIET_STDDEV:.0f}，会挡住较丰富的内容。"
        )
    return (
        f"{lead}在 8 个边或角里，{chosen_name}最不遮挡（起伏 {quiet:.1f}），因此新提示放在这里。"
        f"原提示保留，画面上会同时看到新旧两条。"
    )


def _placement_rationale(preferred: str | None, chosen: str, details: dict, legal: bool) -> str:
    chosen_name = _PLACEMENT_ZH[chosen]
    quiet = details[chosen]["stddev"]
    if legal and chosen == preferred:
        return (
            f"原放置方式「{_PLACEMENT_ZH[preferred]}」没有挡住关键内容，新提示仍放在这里，并改为持续显示。"
            f"原提示保留在 previous_explicit，界面上应同时展示新旧两条。"
        )
    if not legal:
        lead = f"原放置方式「{preferred or '未设置'}」不在第 5.6 条允许的位置。"
    elif details[preferred]["overlap"] > 0:
        lead = "原位置上已有提示，新提示若叠在同一处会挡住它。"
    else:
        lead = (
            f"原放置方式「{_PLACEMENT_ZH[preferred]}」的画面起伏为 {details[preferred]['stddev']:.1f}，"
            f"会挡住较丰富的内容。"
        )
    return (
        f"{lead}在内容附近、界面顶部、底部和背景里，{chosen_name}最不遮挡（起伏 {quiet:.1f}），因此新提示放在这里。"
        f"原提示保留在 previous_explicit，界面上应同时展示新旧两条。"
    )


def _virtual_changes(old: dict, spec: dict) -> list[dict]:
    return [
        {
            "file": "start_frame.png",
            "field": "overlay",
            "before": old.get("position"),
            "after": spec.get("position"),
            "action": "在起始画面上另叠一条合规提示，不擦除原来的提示像素",
        },
        {
            "file": "scene.json",
            "field": "explicit.text",
            "before": old.get("text"),
            "after": spec.get("text"),
            "action": f"提示文字改为「{spec.get('text')}」",
        },
        {
            "file": "scene.json",
            "field": "explicit.position",
            "before": old.get("position"),
            "after": spec.get("position"),
            "action": f"新提示位置为{_POSITION_ZH.get(spec.get('position'), spec.get('position'))}",
        },
        {
            "file": "scene.json",
            "field": "explicit.font_size",
            "before": old.get("font_size"),
            "after": spec.get("font_size"),
            "action": "字高按最短边的 5% 重新计算",
        },
        {
            "file": "scene.json",
            "field": "previous_explicit",
            "before": None,
            "after": old.get("text"),
            "action": "原来的不合格提示写入 previous_explicit，不从画面删除",
        },
    ]


def _interactive_changes(old: dict, explicit: dict) -> list[dict]:
    return [
        {
            "file": "scene.json",
            "field": "explicit.text",
            "before": old.get("text"),
            "after": explicit.get("text"),
            "action": f"提示文字改为「{explicit.get('text')}」",
        },
        {
            "file": "scene.json",
            "field": "explicit.placements",
            "before": old.get("placements"),
            "after": explicit.get("placements"),
            "action": f"新提示放在{_PLACEMENT_ZH.get(explicit['placements'][0], explicit['placements'][0])}",
        },
        {
            "file": "scene.json",
            "field": "explicit.persistent",
            "before": old.get("persistent"),
            "after": True,
            "action": "交互提示改为在服务过程中持续显示",
        },
        {
            "file": "scene.json",
            "field": "explicit.color",
            "before": [old.get("color"), old.get("background")],
            "after": [explicit.get("color"), explicit.get("background")],
            "action": "文字与底色对比度调整到不低于 3:1",
        },
        {
            "file": "scene.json",
            "field": "previous_explicit",
            "before": None,
            "after": old.get("text"),
            "action": "原来的不合格提示保留在 previous_explicit，不删除",
        },
    ]


def _interface_canvas(members: dict[str, bytes]) -> Image.Image:
    canvas = Image.new("RGB", (960, 540), (244, 241, 232))
    content = _content_image(members)
    if content is not None:
        fitted = content.convert("RGB")
        fitted.thumbnail((640, 360))
        x = (960 - fitted.width) // 2
        y = (540 - fitted.height) // 2
        canvas.paste(fitted, (x, y))
    return canvas


def _interface_with_old_only(members: dict[str, bytes], old: dict) -> Image.Image:
    canvas = _interface_canvas(members)
    _draw_old_chip(canvas, old)
    return canvas


def _content_image(members: dict[str, bytes]) -> Image.Image | None:
    for name, data in members.items():
        if name == "scene.json":
            continue
        suffix = Path(name).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg"}:
            try:
                with Image.open(io.BytesIO(data)) as opened:
                    return opened.convert("RGB")
            except Exception:
                return None
    return None


def _draw_old_chip(image: Image.Image, old: dict) -> tuple[int, int, int, int] | None:
    text = " ".join(str(old.get("text") or "").split())
    if not text:
        return None
    from .explicit import parse_hex_color
    plate = parse_hex_color("#8A8A8A")
    ink = parse_hex_color("#666666")
    font = ImageFont.load_default()
    for candidate in (
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ):
        if Path(candidate).is_file():
            font = ImageFont.truetype(candidate, size=18)
            break
    bbox = font.getbbox(text)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 6
    box_w, box_h = text_w + pad * 2, text_h + pad * 2
    placements = list(old.get("placements") or [])
    position = _PLACEMENT_POSITION.get(placements[0], "top_edge") if placements else "top_edge"
    if position not in VIRTUAL_POSITIONS:
        position = "top_edge"
    try:
        x, y = _place(position, image.width, image.height, box_w, box_h, 8)
    except Exception:
        x, y = 8, 8
    draw = ImageDraw.Draw(image)
    draw.rectangle([x, y, x + box_w - 1, y + box_h - 1], fill=plate)
    draw.text((x + pad - bbox[0], y + pad - bbox[1]), text, font=font, fill=ink)
    return (x, y, box_w, box_h)


def _first_legal(placements) -> str | None:
    for item in list(placements or []):
        if item in INTERACTIVE_PLACEMENTS:
            return item
    return None


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _zip_members(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            archive.writestr(name, members[name])
    return buffer.getvalue()


def _with_original_xmp(original_png: bytes, rendered_png: bytes, tools: ToolPaths) -> bytes:
    """像素用新图，XMP 仍从原起始画面拷回，避免显式修复丢掉隐式标识。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source.png"
        dest = root / "dest.png"
        source.write_bytes(original_png)
        dest.write_bytes(rendered_png)
        command = [tools.exiftool]
        if tools.exiftool_config:
            command.extend(["-config", tools.exiftool_config])
        command.extend(["-overwrite_original", "-TagsFromFile", str(source), "-XMP:all", str(dest)])
        proc = subprocess.run(command, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ExplicitPreviewError(
                "METADATA_WRITE_FAILED",
                f"无法保留原图上的隐式标识：{proc.stderr[-300:]}",
            )
        return dest.read_bytes()

