"""显式标识的要素、位置与可辨性校验。

虚拟场景使用第 5.5 条，交互场景使用第 5.6 条。两条都要求同时具备
人工智能要素（“人工智能”或“AI”）和生成合成要素（“生成”和/或“合成”）。
文字高度、边角位置只约束虚拟场景起始画面；交互场景约束放置方式和持续显示。
"""
from __future__ import annotations

import re

from ..labeling_error import LabelingError

VIRTUAL_POSITIONS = (
    "top_left", "top_right", "bottom_left", "bottom_right",
    "top_edge", "bottom_edge", "left_edge", "right_edge",
)
INTERACTIVE_PLACEMENTS = (
    "near_content",
    "interface_top",
    "interface_bottom",
    "interface_background",
)
# 标准写“字型应清晰可辨”，没有给出对比度数字。大号提示文字采用 3:1，
# 与 WCAG 大文本门槛一致，便于自动判定，也便于以后替换。
MIN_CONTRAST = 3.0
MIN_TEXT_HEIGHT_RATIO = 0.05
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def explicit_element_errors(text: str) -> list[str]:
    """第 5.5.b / 5.6.b 条（亦与 5.1.b、5.2.b、5.4.b 的要素相同）。"""
    errors: list[str] = []
    if "人工智能" not in text and "AI" not in text:
        errors.append("缺少人工智能要素：应包含“人工智能”或“AI”")
    if "生成" not in text and "合成" not in text:
        errors.append("缺少生成合成要素：应包含“生成”和/或“合成”")
    return errors


def parse_hex_color(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _HEX.match(value.strip()):
        raise LabelingError("EXPLICIT_LABEL_INVALID", "颜色需为 #RGB 或 #RRGGBB。")
    raw = value.strip()[1:]
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _channel(value: float) -> float:
    value = value / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: tuple[int, int, int]) -> float:
    red, green, blue = (_channel(part) for part in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    left = relative_luminance(parse_hex_color(foreground))
    right = relative_luminance(parse_hex_color(background))
    lighter, darker = max(left, right), min(left, right)
    return (lighter + 0.05) / (darker + 0.05)


def assert_contrast(foreground: str, background: str) -> float:
    ratio = contrast_ratio(foreground, background)
    if ratio < MIN_CONTRAST:
        raise LabelingError(
            "EXPLICIT_LABEL_INVALID",
            f"文字与背景对比度为 {ratio:.2f}:1，低于清晰可辨门槛 {MIN_CONTRAST}:1。",
        )
    return ratio


def assert_virtual_explicit(text: str, position: str, foreground: str, background: str) -> float:
    errors = explicit_element_errors(text)
    if not text.strip():
        errors.append("提示文字不能为空")
    if position not in VIRTUAL_POSITIONS:
        errors.append("虚拟场景显式标识应位于画面的边或角")
    if errors:
        raise LabelingError("EXPLICIT_LABEL_INVALID", "；".join(errors))
    return assert_contrast(foreground, background)


def assert_interactive_explicit(text: str, placements: list[str], persistent: bool,
                                foreground: str, background: str) -> float:
    errors = explicit_element_errors(text)
    if not text.strip():
        errors.append("提示文字不能为空")
    if not placements:
        errors.append("应至少选择一种放置方式")
    unknown = [item for item in placements if item not in INTERACTIVE_PLACEMENTS]
    if unknown:
        errors.append("放置方式须为内容附近，或界面顶部、底部、背景")
    if persistent is not True:
        errors.append("交互场景界面显式标识应持续显示")
    if errors:
        raise LabelingError("EXPLICIT_LABEL_INVALID", "；".join(errors))
    return assert_contrast(foreground, background)
