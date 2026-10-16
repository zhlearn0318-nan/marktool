"""独立于 ExifTool 的 XMP 字节扫描——修复前的"第二遍读取"。

**为什么需要它**：图片的修复前复核有两条独立读取路径（项目自己的 XMP 包解析器
+ ExifTool），"两条都同意"才允许自动改写。视频与 PDF 的原生读取器**就是**
ExifTool 本身，照搬图片的做法等于用同一条路径核对它自己。所以这里真的再造
一条：不问任何工具，直接在文件字节里找 XMP 包中的 ``<aigc:AIGC>`` 元素。

**能力边界**（会写进桌面文档 repair-multimodal-validation.md，不是实现缺陷）：
- 只看得到以**明文 XML** 存放的标识。压缩过的元数据流（例如加密 PDF 的
  XMP 流）这里必然扫不到——那时报"扫不到"，由调用方按"读取器分歧"处理，
  而不是假装"文件里没有标识"。
- 不看容器结构：扫的是字节，不是 box/对象。所以它的产出是"文件里出现了几处
  标识文本"，与"容器里几处标识"的差异正是要发现的东西。

扫描顺序即文件顺序，便于与 ExifTool 的记录一一对照。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

# 标准载体 aigc:AIGC 与旧载体 aigc:metadata（见 app/metadata/xmp_reader.py）。
# 两种都扫：只扫标准载体的话，一份"标准 + 旧"双载体的文件在这里会少算一份，
# 反倒被报成读取器分歧。
_ELEMENT = re.compile(
    rb"<(aigc:(?:AIGC|metadata))(?:\s[^>]*)?>(.*?)</\1\s*>", re.DOTALL)

_CHUNK_SIZE = 1 << 20      # 1 MiB
_OVERLAP = 1 << 16         # 64 KiB：兜住跨块边界的元素（标识文本远小于此）


def scan_aigc_elements(path: str | Path) -> list[str]:
    """按文件顺序返回所有 ``<aigc:...>`` 元素的文本值（已做 XML 反转义）。

    跨块边界的元素靠重叠窗口兜住，并按**绝对偏移**去重——否则同一个元素会在
    相邻两个窗口里各命中一次，把"一处标识"算成两处。
    """
    values: list[str] = []
    seen: set[int] = set()
    consumed = 0
    tail = b""
    with open(path, "rb") as stream:
        while True:
            block = stream.read(_CHUNK_SIZE)
            if not block:
                break
            window = tail + block
            window_start = consumed - len(tail)
            for match in _ELEMENT.finditer(window):
                position = window_start + match.start()
                if position in seen:
                    continue
                seen.add(position)
                values.append(_decode(match.group(2)))
            consumed += len(block)
            tail = window[-_OVERLAP:]
    return values


def _decode(raw: bytes) -> str:
    """XML 实体还原。ExifTool 写 XMP 时把引号写成 ``&quot;``，不还原就解析不出 JSON。"""
    return html.unescape(raw.decode("utf-8", "replace")).strip()
