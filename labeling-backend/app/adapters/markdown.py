"""Markdown 元数据适配器（GB 45438-2025 隐式标识）。

- 载体：YAML frontmatter 顶层键 ``AIGC``（值 = 附录 E JSON 字符串），由
  ``app.metadata.markdown_carrier`` 承担实际读写——本类只是把容器差异接进
  ``BaseAdapter`` 的流水线契约（注册表 + 回读校验 + 媒体完整性）。
- 不写 HTML 注释：那是**旧载体**，只读不写；检测到时给 LEGACY_CARRIER info，
  ``replace`` 策略下由 ``remove_aigc`` 一并清除。
- 媒体完整性（§9.4）：Markdown 没有 ffprobe 可比的时长/轨道，取而代之的是
  「正文哈希」——正文是文本模态的媒体主体，写入标识不得改动它（对应图片的
  像素哈希）。同时校验 BOM 与行尾风格未被改写。
"""
from __future__ import annotations

from pathlib import Path

from ..core.jobs import MODALITY_TEXT
from ..core.reader import AIGCRecord
from ..metadata import markdown_carrier
from .base import AdapterError, BaseAdapter, FileContentError, MediaReport


class MarkdownAdapter(BaseAdapter):
    carrier_id = "md-frontmatter-aigc-v1"
    modality = MODALITY_TEXT
    supported_mimes = ("text/markdown",)

    def detect_existing(self, path: str | Path) -> list[AIGCRecord]:
        try:
            return markdown_carrier.read_records(path)
        except markdown_carrier.MarkdownCarrierError as e:
            raise FileContentError(str(e)) from None

    def write_metadata(self, src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
        try:
            markdown_carrier.write(src, dst, aigc_obj)
        except markdown_carrier.MarkdownCarrierError as e:
            raise FileContentError(str(e)) from None
        if not self.detect_existing(dst):
            raise AdapterError("写入后未能在结果文件中读到 AIGC 标识")

    def remove_aigc(self, src: str | Path, dst: str | Path) -> None:
        try:
            markdown_carrier.remove(src, dst)
        except markdown_carrier.MarkdownCarrierError as e:
            raise FileContentError(str(e)) from None
        remaining = self.detect_existing(dst)
        if remaining:
            raise AdapterError(f"移除旧标识后仍存在 {len(remaining)} 处 AIGC 记录")

    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        """写入前后正文哈希 / BOM / 行尾风格对比。任一探测失败或差异即判失败。"""
        try:
            before = self._shape(src)
            after = self._shape(dst)
        except markdown_carrier.MarkdownCarrierError as e:
            return MediaReport(passed=False, reason=f"Markdown 不可读: {e}")

        passed, reason = markdown_carrier.media_integrity_report(before, after)
        return MediaReport(passed=passed, reason=reason, before=before, after=after)

    def content_fingerprint(self, path: str | Path) -> tuple[str, str]:
        """正文 sha256 作为内容指纹（编号登记库用，§6.1 b）。

        ``body_sha256`` 是 frontmatter 之外、且**已剔除全部 AIGC 载体**的正文
        哈希，所以写入标识不会改变它——这正是它能当指纹的前提。
        """
        try:
            return self._shape(path)["body_sha256"], "body"
        except markdown_carrier.MarkdownCarrierError as e:
            raise FileContentError(str(e)) from None

    @staticmethod
    def _shape(path: str | Path) -> dict:
        shape = markdown_carrier.describe(path)
        return {
            "has_frontmatter": shape.has_frontmatter,
            "bom": shape.bom,
            "eol": "CRLF" if shape.eol == "\r\n" else "LF",
            "line_count": shape.line_count,
            "body_sha256": shape.body_sha256,
        }
