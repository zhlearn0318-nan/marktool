"""把虚拟场景显式标识画到起始画面上，并按同一参数重绘以核对像素。

第 5.5 条要求文字位于起始画面的边或角，高度不低于画面最短边的 5%。
本模块用 Pillow 绘制，校验时按记录的字号、字体和色块重绘，不依赖 OCR。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..labeling_error import LabelingError
from .explicit import MIN_TEXT_HEIGHT_RATIO, VIRTUAL_POSITIONS, parse_hex_color

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


def find_font(explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise LabelingError("EXPLICIT_LAYOUT_FAILED", f"找不到指定字体: {explicit}")
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    raise LabelingError(
        "EXPLICIT_LAYOUT_FAILED",
        "未找到可绘制中文的字体。请安装文泉驿微米黑、Noto Sans CJK 或微软雅黑，"
        "或通过 font_path 指定字体文件。",
    )


def _place(position: str, width: int, height: int, box_w: int, box_h: int,
           margin: int) -> tuple[int, int]:
    if position == "top_left":
        return margin, margin
    if position == "top_right":
        return width - box_w - margin, margin
    if position == "bottom_left":
        return margin, height - box_h - margin
    if position == "bottom_right":
        return width - box_w - margin, height - box_h - margin
    if position == "top_edge":
        return (width - box_w) // 2, margin
    if position == "bottom_edge":
        return (width - box_w) // 2, height - box_h - margin
    if position == "left_edge":
        return margin, (height - box_h) // 2
    if position == "right_edge":
        return width - box_w - margin, (height - box_h) // 2
    raise LabelingError("EXPLICIT_LABEL_INVALID", "虚拟场景显式标识应位于画面的边或角")


def draw_explicit_label(image: Image.Image, *, text: str, position: str,
                        color: str, background: str, font_path: Path) -> tuple[Image.Image, dict]:
    """返回画好标识的 RGB 图，以及可复现这次绘制的参数。"""
    if position not in VIRTUAL_POSITIONS:
        raise LabelingError("EXPLICIT_LABEL_INVALID", "虚拟场景显式标识应位于画面的边或角")
    canvas = image.convert("RGB")
    width, height = canvas.size
    min_side = min(width, height)
    if min_side < 32:
        raise LabelingError("EXPLICIT_LAYOUT_FAILED", "起始画面过小，无法放下不低于 5% 最短边的提示文字。")
    need = MIN_TEXT_HEIGHT_RATIO * min_side
    size = max(8, int(round(need)))
    chosen: tuple[int, tuple[int, int, int, int], int, int] | None = None
    for _ in range(48):
        font = ImageFont.truetype(str(font_path), size=size)
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_h >= need:
            chosen = (size, bbox, text_w, text_h)
            break
        size += max(1, int(need - text_h))
    if chosen is None:
        raise LabelingError("EXPLICIT_LAYOUT_FAILED", "无法把提示文字放大到最短边的 5%。")
    font_size, bbox, text_w, text_h = chosen
    pad = max(4, text_h // 5)
    margin = max(4, int(round(0.02 * min_side)))
    box_w = text_w + pad * 2
    box_h = text_h + pad * 2
    if box_w + margin * 2 > width or box_h + margin * 2 > height:
        raise LabelingError(
            "EXPLICIT_LAYOUT_FAILED",
            "起始画面放不下满足 5% 高度的提示文字，请使用更大的画面。",
        )
    origin_x, origin_y = _place(position, width, height, box_w, box_h, margin)
    if origin_x < 0 or origin_y < 0 or origin_x + box_w > width or origin_y + box_h > height:
        raise LabelingError("EXPLICIT_LAYOUT_FAILED", "提示文字超出起始画面。")
    draw = ImageDraw.Draw(canvas)
    foreground = parse_hex_color(color)
    plate = parse_hex_color(background)
    draw.rectangle(
        [origin_x, origin_y, origin_x + box_w - 1, origin_y + box_h - 1],
        fill=plate,
    )
    font = ImageFont.truetype(str(font_path), size=font_size)
    draw.text(
        (origin_x + pad - bbox[0], origin_y + pad - bbox[1]),
        text, font=font, fill=foreground,
    )
    spec = {
        "text": text,
        "position": position,
        "position_class": "corner" if position in {
            "top_left", "top_right", "bottom_left", "bottom_right"
        } else "edge",
        "color": color,
        "background": background,
        "font_path": str(font_path),
        "font_size": font_size,
        "pad": pad,
        "margin": margin,
        "box": [origin_x, origin_y, box_w, box_h],
        "text_height": text_h,
        "min_side": min_side,
        "height_ratio": text_h / min_side,
        "origin": [bbox[0], bbox[1]],
        "clause": "5.5",
    }
    return canvas, spec


def render_patch(spec: dict) -> Image.Image:
    box_w = int(spec["box"][2])
    box_h = int(spec["box"][3])
    patch = Image.new("RGB", (box_w, box_h), parse_hex_color(spec["background"]))
    font = ImageFont.truetype(str(spec["font_path"]), size=int(spec["font_size"]))
    origin_x, origin_y = spec["origin"]
    ImageDraw.Draw(patch).text(
        (int(spec["pad"]) - int(origin_x), int(spec["pad"]) - int(origin_y)),
        spec["text"], font=font, fill=parse_hex_color(spec["color"]),
    )
    return patch


def overlay_matches(image: Image.Image, spec: dict) -> tuple[bool, str]:
    """起始画面上的色块是否与记录参数重绘的结果一致。"""
    try:
        x, y, box_w, box_h = (int(value) for value in spec["box"])
        crop = image.convert("RGB").crop((x, y, x + box_w, y + box_h))
        patch = render_patch(spec)
    except (KeyError, OSError, ValueError, LabelingError) as exc:
        return False, f"无法按记录参数重绘显式标识: {exc}"
    if crop.size != patch.size or crop.tobytes() != patch.tobytes():
        return False, "起始画面上的提示文字与记录的显式标识不一致"
    return True, "起始画面上的提示文字与记录的显式标识一致"
