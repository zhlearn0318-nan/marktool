"""虚拟场景标识（GB 45438—2025 第 5.5 条 + 第 6.1 条 / 附录 E）。

虚拟场景的合规载体是起始画面 PNG：
- 像素里有一条位于边或角、高度不低于最短边 5% 的文字提示（第 5.5 条）；
- 文件元数据里有且仅有一份 XMP-aigc 隐式标识（第 6.1.c 条、附录 E）。

场景描述 ``scene.json`` 只记录绘制参数和条款，不再放第二份 AIGC JSON。
标准允许在持续服务过程中的适当位置再提示；该要求记在 ``service_hint`` 中，
由运行时展示。本工具校验提示要素与位置，不渲染实时三维画面。
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from ..labeling_error import LabelingError
from ..modalities.dispatch import (
    all_passed, check_item, label_file, parse_file, prepare_aigc, verify_implicit,
)
from ..tools import ToolPaths
from .explicit import VIRTUAL_POSITIONS, assert_virtual_explicit, explicit_element_errors
from .overlay import draw_explicit_label, find_font, overlay_matches

SCENE_JSON = "scene.json"
FRAME_NAME = "start_frame.png"
PACKAGE_NAME = "virtual-scene.zip"


def _blank_frame(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", (width, height), (18, 32, 58))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    for band in range(12):
        top = int(height * band / 12)
        bottom = int(height * (band + 1) / 12)
        shade = 16 + band * 7
        draw.rectangle([0, top, width, bottom], fill=(shade, shade + 14, shade + 36))
    return image


def _load_start_frame(source: Path | None, ffmpeg: str, size: tuple[int, int]) -> tuple[Image.Image, dict]:
    if source is None:
        return _blank_frame(size), {"source_kind": "generated_frame", "source_name": None}
    suffix = source.suffix.lower()
    info = {"source_kind": "image", "source_name": source.name}
    if suffix in {".mp4", ".mov", ".m4v"} or _looks_like_mp4(source):
        frame = source.with_name(source.stem + ".frame0.png")
        from ..multimodal.ffmpeg_util import run_tool
        run_tool([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-frames:v", "1", str(frame),
        ])
        info = {
            "source_kind": "video_frame0",
            "source_name": source.name,
            "note": "起始画面取自视频第 1 帧。视频本体不放入场景包，以免包内出现第二份内容文件。",
        }
        with Image.open(frame) as opened:
            return opened.convert("RGB"), info
    with Image.open(source) as opened:
        return opened.convert("RGB"), info


def _looks_like_mp4(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return len(head) >= 12 and head[4:8] == b"ftyp"


def _service_hint(during_service: bool, service_position: str | None) -> dict:
    if not during_service:
        return {"enabled": False, "clause": "5.5.c", "note": "标准允许在持续服务过程中提示，本次未启用。"}
    position = service_position or "bottom_right"
    if position not in VIRTUAL_POSITIONS:
        raise LabelingError("EXPLICIT_LABEL_INVALID", "持续服务过程中的提示也应位于画面的边或角。")
    return {
        "enabled": True,
        "position": position,
        "clause": "5.5.c",
        "note": "持续服务过程中的提示由场景运行时按此位置展示。",
    }


def label_virtual_scene(
        output_dir: str | Path,
        aigc_obj: dict,
        *,
        text: str = "人工智能生成合成",
        position: str = "bottom_right",
        color: str = "#FFFFFF",
        background: str = "#102033",
        during_service: bool = False,
        service_position: str | None = None,
        source: str | Path | None = None,
        frame_size: tuple[int, int] = (960, 540),
        policy: str = "reject",
        tools: ToolPaths | None = None,
) -> dict:
    tools = tools or ToolPaths()
    prepared = prepare_aigc(aigc_obj)
    ratio = assert_virtual_explicit(text, position, color, background)
    font = find_font(tools.font_path)
    work = Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    frame, source_info = _load_start_frame(Path(source) if source else None, tools.ffmpeg, frame_size)
    labeled_image, spec = draw_explicit_label(
        frame, text=text, position=position, color=color, background=background, font_path=font,
    )
    rendered = work / "rendered.png"
    labeled_image.save(rendered, format="PNG")
    final_png = work / FRAME_NAME
    carrier = label_file("image", rendered, final_png, prepared, policy, tools)
    hint = _service_hint(during_service, service_position)
    descriptor = {
        "standard": "GB45438-2025",
        "scene_type": "virtual",
        "clause": "5.5",
        "source": source_info,
        "explicit": spec,
        "service_hint": hint,
        "implicit_carrier": {"file": FRAME_NAME, "carrier": carrier, "clause": "6.1"},
        "contrast_ratio": round(ratio, 3),
    }
    scene_path = work / SCENE_JSON
    scene_path.write_text(json.dumps(descriptor, ensure_ascii=False, indent=2), encoding="utf-8")
    package = work / PACKAGE_NAME
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(scene_path, SCENE_JSON)
        archive.write(final_png, FRAME_NAME)
    report = verify_virtual_scene(package, tools, expected_aigc=prepared)
    report["operation"] = "virtual_label"
    report["package"] = PACKAGE_NAME
    if not report["passed"]:
        raise LabelingError("VERIFY_FAILED", "虚拟场景写入后未通过校验。", report["checks"])
    return report


def _read_zip(path: Path) -> tuple[dict, bytes]:
    if not zipfile.is_zipfile(path):
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "虚拟场景校验需要场景包 zip。")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if SCENE_JSON not in names or FRAME_NAME not in names:
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "场景包应包含 scene.json 与 start_frame.png。")
        descriptor = json.loads(archive.read(SCENE_JSON).decode("utf-8"))
        frame = archive.read(FRAME_NAME)
        extras = [name for name in names if name not in {SCENE_JSON, FRAME_NAME} and not name.endswith("/")]
    if not isinstance(descriptor, dict) or descriptor.get("scene_type") != "virtual":
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "scene.json 不是虚拟场景描述。")
    if "AIGC" in descriptor:
        raise LabelingError("AIGC_DUPLICATE_RECORDS", "scene.json 不应再嵌一份 AIGC 对象。")
    return descriptor | {"_extra_members": extras}, frame


def verify_virtual_scene(path: str | Path, tools: ToolPaths | None = None,
                         expected_aigc: dict | None = None) -> dict:
    tools = tools or ToolPaths()
    descriptor, frame_bytes = _read_zip(Path(path))
    spec = descriptor.get("explicit") or {}
    checks: list[dict] = []
    element_errors = explicit_element_errors(str(spec.get("text") or ""))
    checks.append(check_item(
        "explicit_elements", "5.5.b", not element_errors,
        "显式标识同时包含人工智能要素和生成合成要素" if not element_errors else "；".join(element_errors),
    ))
    position = spec.get("position")
    checks.append(check_item(
        "explicit_position", "5.5.d", position in VIRTUAL_POSITIONS,
        "显式标识位于起始画面的边或角" if position in VIRTUAL_POSITIONS
        else "显式标识不在画面的边或角",
    ))
    try:
        with Image.open(io.BytesIO(frame_bytes)) as image:
            image.load()
            min_side = min(image.size)
            from PIL import ImageFont
            font = ImageFont.truetype(str(spec["font_path"]), size=int(spec["font_size"]))
            bbox = font.getbbox(str(spec.get("text") or ""))
            text_height = bbox[3] - bbox[1]
            ratio = (text_height / min_side) if min_side else 0
            height_ok = spec.get("min_side") == min_side and ratio + 1e-9 >= 0.05
            matched, match_message = overlay_matches(image, spec)
        checks.append(check_item(
            "explicit_text_height", "5.5.f", height_ok,
            "文字高度不低于画面最短边的 5%" if height_ok
            else f"文字高度比例为 {ratio:.3f}，低于 5%",
        ))
        checks.append(check_item("explicit_pixels", "5.5.a", matched, match_message))
    except Exception as exc:
        checks.append(check_item("explicit_pixels", "5.5", False, f"起始画面无法读取: {exc}"))
    hint = descriptor.get("service_hint") or {}
    if hint.get("enabled"):
        ok = hint.get("position") in VIRTUAL_POSITIONS
        checks.append(check_item(
            "service_hint", "5.5.c", ok,
            "持续服务提示位于边或角" if ok else "持续服务提示的位置不在边或角",
        ))
    else:
        checks.append(check_item(
            "service_hint", "5.5.c", True,
            "未启用持续服务提示；标准对此为可选。",
            severity="warning",
        ))
    extras = descriptor.get("_extra_members") or []
    checks.append(check_item(
        "package_single_content_file", "6.1.c", not extras,
        "场景包只包含起始画面这一份内容文件" if not extras
        else f"场景包含额外文件，可能引入第二份标识: {extras}",
    ))
    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    frame_path = Path(handle.name)
    handle.write(frame_bytes)
    handle.close()
    try:
        implicit = verify_implicit("image", frame_path, expected_aigc, tools)
    finally:
        frame_path.unlink(missing_ok=True)
    checks.extend(implicit["checks"])
    labels = implicit["labels"]
    passed = all_passed(checks)
    return {
        "standard": "GB45438-2025",
        "operation": "virtual_verify",
        "scene_type": "virtual",
        "passed": passed,
        "explicit": {
            "text": spec.get("text"),
            "position": position,
            "height_ratio": spec.get("height_ratio"),
        },
        "service_hint": {key: value for key, value in hint.items()},
        "implicit_label": labels[0] if len(labels) == 1 else None,
        "implicit_labels": labels,
        "checks": checks,
        "source": descriptor.get("source"),
    }


def parse_virtual_scene(path: str | Path, tools: ToolPaths | None = None) -> dict:
    tools = tools or ToolPaths()
    report = verify_virtual_scene(path, tools)
    return {
        "scene_type": "virtual",
        "explicit_text": (report.get("explicit") or {}).get("text"),
        "implicit_labels": report.get("implicit_labels") or [],
        "count": len(report.get("implicit_labels") or []),
    }

