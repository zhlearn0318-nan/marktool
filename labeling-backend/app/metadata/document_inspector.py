"""文档（Markdown / PDF）隐式标识 · 合规检测器。

只读检测：扫描文档的全部 AIGC 候选 → 按 GB 45438—2025 附录 E 判定 → 输出与
MP4/图片**同构**的报告（conclusion / reason_code / issues / repairability /
c2pa_presence / media_status / confidence），前端无需为模态分叉（§4.5）。

判定逻辑与 MP4 共用 ``app/core/inspector_common`` 的同一份实现——本模块只提供
两件格式相关的东西：
1. 候选从哪来（MD 走 frontmatter 载体模块，PDF 走 ExifTool 全标签扫描）；
2. 哪种标签算"旧载体"（MD 的 HTML 注释 / PDF 的 Document Info 键）。

§2.4 的原则同样适用：媒体状态与元数据结论分离。加密 PDF、损坏 PDF 的
"读不到标识"不是确定结论，一律落 indeterminate 交人工，不当干净的 not_found。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..core import pdfstruct
from ..core.inspector_common import (
    C2PA_ABSENT,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONCLUSION_INDETERMINATE,
    CONCLUSION_NOT_FOUND,
    MEDIA_DEGRADED,
    MEDIA_OK,
    MEDIA_UNREADABLE,
    REPAIR_HUMAN,
    Issue,
    LegacyRule,
    bmff_not_applicable,
    build_report,
    candidate_entry,
    classify_records,
    exiftool_version,
    registry_block,
)
from ..core.reader import ReaderError, read_aigc_records
from . import markdown_carrier

DETECTOR_VERSION = "document-compliance-inspector/0.1.0"

MARKDOWN_MIME = "text/markdown"
PDF_MIME = "application/pdf"
DOCUMENT_MIMES = (MARKDOWN_MIME, PDF_MIME)

# 各格式的旧载体（只读不写；命中给 LEGACY_CARRIER，不翻转结论）
_LEGACY = {
    MARKDOWN_MIME: LegacyRule(detects=lambda tag: tag.startswith("html-comment"),
                              name="HTML 注释",
                              single_note="旧载体，仅提示，不影响国标结论"),
    PDF_MIME: LegacyRule(detects=lambda tag: tag.startswith("PDF"),
                         name="PDF Document Info 字典键",
                         single_note="旧载体，仅提示，不影响国标结论"),
}


class DocumentInspectError(Exception):
    """文件根本不是可读的文档（非 UTF-8 文本等）——由 API 转 415。"""


class DocumentComplianceInspector:
    """Markdown / PDF 的只读合规检测。"""

    detector_version = DETECTOR_VERSION

    def __init__(self, mime: str, exiftool: str = "exiftool",
                 exiftool_config: str | None = None):
        if mime not in DOCUMENT_MIMES:
            raise ValueError(f"不支持的文档类型: {mime}")
        self.mime = mime
        self._exiftool = exiftool
        self._exiftool_config = exiftool_config

    # ---- 对外入口 ----
    def inspect(self, path: str | Path, *, file_name: str | None = None,
                size_bytes: int | None = None, sha256: str | None = None,
                request_id: str | None = None,
                registry: Callable[[str], bool] | None = None) -> dict:
        path = Path(path)
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        if self.mime == PDF_MIME:
            return self._inspect_pdf(path, elapsed, file_name=file_name,
                                     size_bytes=size_bytes, sha256=sha256,
                                     request_id=request_id, registry=registry)
        return self._inspect_markdown(path, elapsed, file_name=file_name,
                                      size_bytes=size_bytes, sha256=sha256,
                                      request_id=request_id, registry=registry)

    # ---- Markdown ----
    def _inspect_markdown(self, path: Path, elapsed, *, file_name, size_bytes,
                          sha256, request_id, registry) -> dict:
        try:
            shape = markdown_carrier.describe(path)
        except markdown_carrier.MarkdownCarrierError as e:
            # 文件不是文本文件（非 UTF-8 / 含 NUL）：不是"结论"，是文件本身不可检
            raise DocumentInspectError(str(e)) from None

        media = {
            "media_status": MEDIA_OK,
            "format": "Markdown",
            "eol": "CRLF" if shape.eol == "\r\n" else "LF",
            "bom": shape.bom,
            "line_count": shape.line_count,
            "body_sha256": shape.body_sha256,
        }
        document = {
            "format": "markdown",
            "frontmatter_present": shape.has_frontmatter,
            "frontmatter_parseable": shape.frontmatter_parseable,
            "bom": shape.bom,
            "eol": "CRLF" if shape.eol == "\r\n" else "LF",
            "line_count": shape.line_count,
            "body_sha256": shape.body_sha256,
        }
        records = markdown_carrier.read_records(path)
        return self._verdict(path, records, media, document, elapsed,
                             file_name=file_name, size_bytes=size_bytes,
                             sha256=sha256, request_id=request_id,
                             registry=registry)

    # ---- PDF ----
    def _inspect_pdf(self, path: Path, elapsed, *, file_name, size_bytes,
                     sha256, request_id, registry) -> dict:
        try:
            probe = pdfstruct.scan(path)
        except OSError as e:
            raise DocumentInspectError(f"无法读取 PDF: {e}") from None

        try:
            probe.page_count = self._page_count(path)
        except (ReaderError, OSError):
            probe.page_count = None

        document = {"format": "pdf", **probe.to_dict()}

        # 加密：元数据整体不可读，"没有标识"无从确认（§2.4）
        if probe.encrypted:
            media = {"media_status": MEDIA_UNREADABLE, "format": "PDF",
                     "page_count": None, "encrypted": True, "signed": False,
                     "eof_marker_present": probe.has_eof}
            return build_report(
                bmff=bmff_not_applicable(), media=media, candidates=[],
                issues=[Issue("PDF_ENCRYPTED", "error",
                              "PDF 已加密，元数据不可读，无法确认是否存有 AIGC 标识", None)],
                conclusion=CONCLUSION_INDETERMINATE, reason_code="PDF_ENCRYPTED",
                repairability=REPAIR_HUMAN, c2pa=C2PA_ABSENT,
                confidence=CONFIDENCE_LOW,
                registry=registry_block(registry, None), file_name=file_name,
                size_bytes=size_bytes, sha256=sha256, request_id=request_id,
                elapsed_ms=elapsed(), detected_mime=self.mime,
                detector_version=self.detector_version,
                exiftool_version_value=exiftool_version(self._exiftool),
                extra={"document": document})

        try:
            records = read_aigc_records(str(path), exiftool=self._exiftool,
                                        config=self._exiftool_config, mime=self.mime)
        except ReaderError as e:
            media = {"media_status": MEDIA_UNREADABLE, "format": "PDF",
                     "page_count": probe.page_count, "encrypted": False,
                     "signed": probe.signed, "eof_marker_present": probe.has_eof}
            return build_report(
                bmff=bmff_not_applicable(), media=media, candidates=[],
                issues=[Issue("UNREADABLE_CARRIER", "error",
                              f"PDF 元数据无法读取: {e}", None)],
                conclusion=CONCLUSION_INDETERMINATE, reason_code="UNREADABLE_CARRIER",
                repairability=REPAIR_HUMAN, c2pa=C2PA_ABSENT,
                confidence=CONFIDENCE_LOW,
                registry=registry_block(registry, None), file_name=file_name,
                size_bytes=size_bytes, sha256=sha256, request_id=request_id,
                elapsed_ms=elapsed(), detected_mime=self.mime,
                detector_version=self.detector_version,
                exiftool_version_value=exiftool_version(self._exiftool),
                extra={"document": document})

        # 已签名：只读检测是安全的，但签名文档的完整性状态要如实标 degraded
        media = {"media_status": MEDIA_OK, "format": "PDF",
                 "page_count": probe.page_count, "encrypted": False,
                 "signed": probe.signed, "eof_marker_present": probe.has_eof}
        extra_issues: list[Issue] = []
        if probe.signed:
            media["media_status"] = MEDIA_DEGRADED
            extra_issues.append(Issue(
                "PDF_SIGNED_PRESENT", "warn",
                "PDF 含数字签名：本工具只读检测，未改动文件；但写入标识会使签名"
                "失效，故该文件不可打标", None))
        if not probe.has_eof:
            media["media_status"] = MEDIA_DEGRADED
            extra_issues.append(Issue(
                "UNREADABLE_CARRIER", "warn",
                "PDF 缺少 %%EOF 结束标记（截断或尾部损坏），元数据读取结果不足以"
                "作为\"无标识\"依据", None))
        return self._verdict(path, records, media, document, elapsed,
                             file_name=file_name, size_bytes=size_bytes,
                             sha256=sha256, request_id=request_id,
                             registry=registry, extra_issues=extra_issues,
                             unreliable_carrier=(not probe.has_eof))

    # ---- 共用判定 ----
    def _verdict(self, path: Path, records: list, media: dict, document: dict,
                 elapsed, *, file_name, size_bytes, sha256, request_id, registry,
                 extra_issues: list[Issue] | None = None,
                 unreliable_carrier: bool = False) -> dict:
        classified = classify_records(records, _LEGACY[self.mime])
        issues: list[Issue] = list(extra_issues or []) + list(classified["issues"])
        conclusion = classified["conclusion"]
        reason_code = classified["reason_code"]
        repairability = classified["repairability"]
        confidence = classified["confidence"]

        # §2.4 载体可靠性升级：读不到记录 + 载体不可信 → 不得当确定结论
        if conclusion == CONCLUSION_NOT_FOUND and unreliable_carrier:
            conclusion = CONCLUSION_INDETERMINATE
            reason_code = "UNREADABLE_CARRIER"
            repairability = REPAIR_HUMAN
            confidence = CONFIDENCE_LOW
            issues = [i for i in (extra_issues or [])] + [
                Issue("UNREADABLE_CARRIER", "error",
                      "文档载体不可靠，无法确认是否存有 AIGC 标识", None)]

        parsed_single = next((r.aigc for r in records
                              if len(records) == 1 and r.aigc is not None), None)
        produce_id = parsed_single.get("ProduceID") if parsed_single else None

        return build_report(
            bmff=bmff_not_applicable(), media=media,
            candidates=[candidate_entry(r) for r in records], issues=issues,
            conclusion=conclusion, reason_code=reason_code,
            repairability=repairability, c2pa=C2PA_ABSENT, confidence=confidence,
            registry=registry_block(registry, produce_id), file_name=file_name,
            size_bytes=size_bytes, sha256=sha256, request_id=request_id,
            elapsed_ms=elapsed(), detected_mime=self.mime,
            detector_version=self.detector_version,
            exiftool_version_value=exiftool_version(self._exiftool),
            extra={"document": document})

    def _page_count(self, path: Path) -> int | None:
        from ..core.reader import exiftool_tags
        tags = exiftool_tags(str(path), self._exiftool, self._exiftool_config)
        return pdfstruct.page_count_from_tags(tags)
