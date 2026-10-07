"""修复前的"第二遍读取"：按模态给出**独立于 ExifTool** 的另一条读取路径。

修复会自动改写文件，所以动手前必须确认"这份文件里到底有几处标识"。图片靠两条
独立读取路径（项目自己的 XMP 解析器 + ExifTool）互相印证；视频与文档没有现成的
第二条，就在本模块里造：

| 模态 | 主读取器 | 第二读取器 |
|---|---|---|
| 视频 MP4 | ExifTool | 字节扫描 XMP 元素（``raw_xmp_scan``）+ ffprobe 原生容器标签 |
| PDF | ExifTool | 字节扫描 XMP 元素（``raw_xmp_scan``） |
| Markdown | 项目 YAML 解析器 | **没有**（见 ``MARKDOWN_CROSS_READ``） |

**比对的是"值多重集"，不是"标签名"**：两条路径对同一处标识的叫法必然不同
（``XMP:AIGC`` vs ``<aigc:AIGC>`` vs ``comment``），比名字只会得到恒定的分歧。
值一致即视为读到同一份内容。

**为什么 MP4 要用两个第二读取器**：ffprobe 看不见 XMP uuid box（实测：标准
XMP 标识对 ffprobe 完全不可见），字节扫描则看不见 QuickTime 原生标签。
MP4 的两类载体各只有一条独立路径能看见，合起来才覆盖得住。
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Sequence

from ..core import aigc
from ..core.raw_xmp_scan import scan_aigc_elements
from ..core.reader import AIGCRecord
from .compliance import (ComplianceIssue, CrossReaderResult,
                         canonical_aigc_value)

# Markdown 只有一套 YAML 解析器（``markdown_carrier``）：ExifTool 不解析
# Markdown frontmatter，做不出真正独立的第二读法。这是**诚实的降级**——
# 报 not_applicable（已知没有）而不是 not_run（该做没做），前者不被规划器拦截。
MARKDOWN_CROSS_READ = CrossReaderResult(
    status="not_applicable",
    detail="Markdown 只有项目自带的一套 YAML 解析器，不存在独立的第二读取路径",
)


def _diverged(detail: str) -> tuple[CrossReaderResult, list[ComplianceIssue]]:
    return (
        CrossReaderResult(status="diverged", detail=detail),
        [ComplianceIssue(
            code="AIGC_READERS_DIVERGED", category="carrier", level="error",
            detail="两条读取路径结果不一致，无法可靠判断唯一性",
        )],
    )


def _compare(project: Sequence[str], external: Sequence[str],
             external_name: str,
             ) -> tuple[CrossReaderResult, list[ComplianceIssue]]:
    """值多重集比对。用 Counter 而不是 set：两处**相同**的标识也是两处。"""
    project_values = Counter(canonical_aigc_value(v) for v in project)
    external_values = Counter(canonical_aigc_value(v) for v in external)
    if project_values != external_values:
        return _diverged(
            f"ExifTool 读到 {sum(project_values.values())} 处标识，"
            f"{external_name}读到 {sum(external_values.values())} 处")
    return CrossReaderResult(
        status="matched",
        detail=f"{external_name} 与 ExifTool 读到的标识一致",
    ), []


# ---- 视频 ------------------------------------------------------------------

def _ffprobe_aigc_values(path: str | Path, ffprobe: str) -> tuple[list[str], str | None]:
    """ffprobe 眼里的 AIGC 值（只看容器原生标签，看不到 XMP uuid box）。"""
    try:
        completed = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format_tags",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], str(exc)
    if completed.returncode != 0:
        return [], completed.stderr.strip()[:200] or "ffprobe 探测失败"
    try:
        tags = json.loads(completed.stdout or "{}").get("format", {}).get("tags", {})
    except json.JSONDecodeError as exc:
        return [], f"ffprobe 输出不是合法 JSON: {exc}"
    if not isinstance(tags, dict):
        return [], None
    # encoder / major_brand 之类也在这个块里，用"能不能解析成七字段"筛掉
    return [str(v) for v in tags.values() if aigc.parse_aigc(str(v)) is not None], None


def cross_read_mp4(path: str | Path, records: Sequence[AIGCRecord], *,
                   ffprobe: str = "ffprobe",
                   ) -> tuple[CrossReaderResult, list[ComplianceIssue]]:
    """MP4 第二遍读取：字节扫描 XMP + ffprobe 原生标签，对上 ExifTool 的记录。"""
    external = scan_aigc_elements(path)
    ffprobe_values, ffprobe_error = _ffprobe_aigc_values(path, ffprobe)
    if ffprobe_error:
        # ffprobe 缺席/失败不代表文件有问题，但少了半个第二读取器就不该自动改写：
        # 与图片侧"ExifTool 交叉读取不可用"同样处理。
        return (
            CrossReaderResult(status="unavailable",
                              detail=f"ffprobe 第二遍读取不可用: {ffprobe_error}"),
            [ComplianceIssue(
                code="CROSS_READ_UNAVAILABLE", category="tool", level="warning",
                detail="ffprobe 第二遍读取不可用，不能确认容器原生标签里的标识份数",
            )],
        )
    return _compare([r.raw for r in records], external + ffprobe_values,
                    "字节扫描 + ffprobe")


# ---- PDF -------------------------------------------------------------------

def cross_read_pdf(path: str | Path, records: Sequence[AIGCRecord],
                   ) -> tuple[CrossReaderResult, list[ComplianceIssue]]:
    """PDF 第二遍读取：字节扫描 XMP 元素，对上 ExifTool 的记录。

    扫描对**加密 PDF** 会一无所获（元数据流被加密），此时报的是"两条读取路径
    不一致"而不是"文件里没有标识"——正好把这类文件挡在自动修复之外，与
    ``PDF_ENCRYPTED`` 的不可修判定一致。
    """
    return _compare([r.raw for r in records], scan_aigc_elements(path),
                    "字节扫描")
