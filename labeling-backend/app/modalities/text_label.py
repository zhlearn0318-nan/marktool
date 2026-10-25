"""文本文件的文件元数据隐式标识（GB 45438—2025 第 6.1 条、附录 E）。

文本没有可写的 XMP 容器。本模块把附录 E 字符串放在正文之后的记录分隔段里：
分隔符不会进入重组后的字幕正文，播放器读到的是去掉尾段的可见文本。
字段名关键词为 ``AIGC``，满足附录 E.a。

约定：正文不得包含字节序列 ``\\n\\x1eGB45438-AIGC\\x1e``。该序列只用作标识边界。
"""
from __future__ import annotations

from pathlib import Path

from ..core import aigc
from ..labeling_error import LabelingError

# 记录分隔符（0x1E）包住关键词，避免和普通换行、字幕时间轴混淆。
MARKER = b"\n\x1eGB45438-AIGC\x1e"


def parse_text_bytes(data: bytes) -> tuple[bytes, list[str]]:
    """拆出可见正文和全部隐式标识原文。标识按出现顺序返回。"""
    positions: list[int] = []
    start = 0
    while True:
        found = data.find(MARKER, start)
        if found < 0:
            break
        positions.append(found)
        start = found + len(MARKER)
    if not positions:
        return data, []
    visible = data[: positions[0]]
    raws: list[str] = []
    for index, offset in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(data)
        chunk = data[offset + len(MARKER) : end]
        if chunk.endswith(b"\n"):
            chunk = chunk[:-1]
        raws.append(chunk.decode("utf-8", "replace"))
    return visible, raws


def visible_text(path: str | Path) -> str:
    data = Path(path).read_bytes()
    visible, _ = parse_text_bytes(data)
    return visible.decode("utf-8")


def write_text_label(src: str | Path, dst: str | Path, aigc_obj: dict,
                     policy: str = "reject") -> None:
    """把一份附录 E 标识写到文本副本的尾段。不修改 ``src``。"""
    errors = aigc.validate_aigc(aigc_obj)
    if errors:
        raise LabelingError("AIGC_SCHEMA_INVALID", "AIGC 元数据不符合附录 E。", errors)
    data = Path(src).read_bytes()
    visible, raws = parse_text_bytes(data)
    # 正文区若再出现标记，parse 会把它切成标识。写入前要求正文本身不含标记。
    if MARKER in visible:
        raise LabelingError(
            "TEXT_MARKER_CONFLICT",
            "文本正文含有隐式标识分隔符，无法安全写入。",
        )
    if raws and policy == "reject":
        raise LabelingError(
            "AIGC_METADATA_EXISTS",
            "文本已存在 AIGC 隐式标识，且策略为拒绝。",
        )
    if policy not in ("reject", "replace"):
        raise LabelingError("AIGC_SCHEMA_INVALID", "existing_metadata_policy 必须为 reject 或 replace。")
    payload = visible + MARKER + aigc.serialize_aigc(aigc_obj).encode("ascii") + b"\n"
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def text_records(path: str | Path) -> list[dict]:
    """解析文本中的隐式标识，返回与读取器相近的记录字典。"""
    _, raws = parse_text_bytes(Path(path).read_bytes())
    records = []
    for raw in raws:
        records.append({
            "source": "text-trailer:AIGC",
            "raw": raw,
            "aigc": aigc.parse_aigc(raw),
            "location": "文本文件尾部 GB45438-AIGC 元数据段",
        })
    return records
