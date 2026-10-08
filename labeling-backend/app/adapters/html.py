"""HTML 文件元数据隐式标识适配器。

载体为 ``head/meta[name=AIGC]``。实际的字节保真解析、编码校验和 C2PA
保护由 :mod:`app.metadata.html_carrier` 完成；本类只把它接入公共适配器契约。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from ..core.jobs import MODALITY_TEXT
from ..core.reader import AIGCRecord
from ..metadata import html_carrier
from .base import AdapterError, BaseAdapter, FileContentError, MediaReport


class HtmlAdapter(BaseAdapter):
    carrier_id = "html-meta-aigc-v1"
    modality = MODALITY_TEXT
    supported_mimes = ("text/html",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._carrier = html_carrier.HtmlMetadataAdapter()

    @staticmethod
    def _file_error(exc: html_carrier.HtmlMetadataError) -> FileContentError:
        detail = f"{exc}"
        if exc.details:
            detail += ": " + "; ".join(exc.details)
        return FileContentError(detail)

    def _inspect(self, path: str | Path) -> html_carrier.HtmlInspection:
        try:
            return self._carrier.inspect(path)
        except html_carrier.HtmlMetadataError as exc:
            raise self._file_error(exc) from None

    @staticmethod
    def _to_record(record: html_carrier.HtmlAigcRecord) -> AIGCRecord:
        canonical = record.inside_head and record.property_name.casefold() == "aigc"
        tag_key = "html-meta:AIGC" if canonical else "html-meta-invalid:AIGC"
        return AIGCRecord(
            tag_key=tag_key,
            raw=record.raw_value,
            aigc=record.aigc,
            location="HTML head/meta[name=AIGC]" if canonical
            else "HTML 非规范 AIGC meta",
        )

    def detect_existing(self, path: str | Path) -> list[AIGCRecord]:
        return [self._to_record(record) for record in self._inspect(path).records]

    def preflight(self, path: str | Path) -> html_carrier.HtmlInspection:
        """写前安全门：C2PA 引用和非规范载体只能只读检测，不能自动改写。"""
        inspection = self._inspect(path)
        self._ensure_mutable(inspection)
        return inspection

    @staticmethod
    def _ensure_mutable(inspection: html_carrier.HtmlInspection) -> None:
        if inspection.has_c2pa_manifest:
            raise FileContentError(
                "HTML 存在 C2PA manifest 引用；修改文件会使来源声明失效，需重新签名"
            )
        invalid = [
            record for record in inspection.records
            if not (record.inside_head and record.property_name.casefold() == "aigc")
        ]
        if invalid:
            raise FileContentError(
                "发现非规范位置或名称的 AIGC meta，不能安全自动改写"
            )

    @staticmethod
    def _encode(text: str, encoding: str) -> bytes:
        try:
            return text.encode(encoding, "strict")
        except UnicodeEncodeError as exc:
            raise FileContentError("写入后的 AIGC 内容无法用原 HTML 字符编码保存") from exc

    @staticmethod
    def _publish(src: Path, dst: Path, data: bytes) -> None:
        if src.resolve() == dst.resolve():
            raise FileContentError("结果文件必须与原文件分开保存")
        dst.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{dst.stem}.", suffix=dst.suffix or ".html", dir=dst.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_bytes(data)
            shutil.copystat(src, temporary)
            os.replace(temporary, dst)
        finally:
            temporary.unlink(missing_ok=True)

    def write_metadata(self, src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
        source, output = Path(src), Path(dst)
        inspection = self.preflight(source)
        if inspection.records:
            raise AdapterError("写入前仍存在 AIGC 标识；应先执行整体移除")
        try:
            meta = self._carrier._build_meta({"AIGC": aigc_obj})
            updated = self._carrier._replace_records(inspection, meta)
        except (html_carrier.HtmlMetadataError, ValueError) as exc:
            if isinstance(exc, html_carrier.HtmlMetadataError):
                raise self._file_error(exc) from None
            raise FileContentError("AIGC 元数据超过 HTML 载体写入上限") from None
        self._publish(source, output, self._encode(updated, inspection.encoding))
        records = self.detect_existing(output)
        if len(records) != 1 or records[0].aigc != aigc_obj:
            output.unlink(missing_ok=True)
            raise AdapterError("HTML 写入后回读结果与计划不一致")

    def remove_aigc(self, src: str | Path, dst: str | Path) -> None:
        source, output = Path(src), Path(dst)
        inspection = self.preflight(source)
        updated = self._carrier._without_records(inspection.text, inspection.records)
        self._publish(source, output, self._encode(updated, inspection.encoding))
        if self.detect_existing(output):
            output.unlink(missing_ok=True)
            raise AdapterError("移除旧 HTML 标识后仍能检出 AIGC 记录")

    @staticmethod
    def _shape(inspection: html_carrier.HtmlInspection) -> dict:
        return {
            "encoding": inspection.encoding,
            "newline": "CRLF" if inspection.newline == "\r\n" else "LF",
            "protected_content_sha256": inspection.protected_content_sha256,
        }

    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        try:
            before = self._shape(self._inspect(src))
            after = self._shape(self._inspect(dst))
        except FileContentError as exc:
            return MediaReport(passed=False, reason=f"HTML 不可读: {exc}")
        problems = [key for key in before if before[key] != after[key]]
        return MediaReport(
            passed=not problems,
            reason=("HTML 写入前后以下内容属性变化: " + ", ".join(problems))
            if problems else None,
            before=before,
            after=after,
        )

    def content_fingerprint(self, path: str | Path) -> tuple[str, str]:
        return self._inspect(path).protected_content_sha256, "body"
