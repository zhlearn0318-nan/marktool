"""DOCX 文件元数据隐式标识适配器。

载体为 OOXML Custom XML Part。底层容器处理保留包内其余部件，公共适配器只负责
把读写、整体移除、完整性和内容指纹接入统一任务流水线。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from ..core.jobs import MODALITY_TEXT
from ..core.reader import AIGCRecord
from ..metadata import docx_carrier
from .base import AdapterError, BaseAdapter, FileContentError, MediaReport


class DocxAdapter(BaseAdapter):
    carrier_id = "ooxml-custom-xml-aigc-v1"
    modality = MODALITY_TEXT
    supported_mimes = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._carrier = docx_carrier.DocxMetadataAdapter()

    @staticmethod
    def _file_error(exc: docx_carrier.DocxMetadataError) -> FileContentError:
        detail = f"{exc}"
        if exc.details:
            detail += ": " + "; ".join(exc.details)
        return FileContentError(detail)

    def _inspect(self, path: str | Path) -> docx_carrier.DocxInspection:
        try:
            return self._carrier.inspect(path)
        except docx_carrier.DocxMetadataError as exc:
            raise self._file_error(exc) from None

    @staticmethod
    def _to_record(record: docx_carrier.DocxAigcRecord) -> AIGCRecord:
        tag_key = (
            "ooxml-customXml:AIGC" if record.canonical_carrier
            else "ooxml-customXml-invalid:AIGC"
        )
        return AIGCRecord(
            tag_key=tag_key,
            raw=record.raw_value,
            aigc=record.aigc,
            location=f"OOXML Custom XML Part ({record.part_name})",
        )

    def detect_existing(self, path: str | Path) -> list[AIGCRecord]:
        return [self._to_record(record) for record in self._inspect(path).records]

    def preflight(self, path: str | Path) -> docx_carrier.DocxInspection:
        """写前安全门：带 Office 签名或非规范载体的包只允许只读检测。"""
        inspection = self._inspect(path)
        self._ensure_mutable(inspection)
        return inspection

    @staticmethod
    def _ensure_mutable(inspection: docx_carrier.DocxInspection) -> None:
        if inspection.has_digital_signature:
            raise FileContentError(
                "DOCX 包含 Office 数字签名；修改前必须具备重新签名能力"
            )
        if any(not record.canonical_carrier for record in inspection.records):
            raise FileContentError(
                "发现非项目规范 Custom XML 载体中的 AIGC 数据，不能安全自动改写"
            )

    @staticmethod
    def _prepare_output(src: Path, dst: Path) -> None:
        if src.resolve() == dst.resolve():
            raise FileContentError("结果文件必须与原文件分开保存")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.unlink(missing_ok=True)

    def write_metadata(self, src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
        source, output = Path(src), Path(dst)
        self.preflight(source)
        self._prepare_output(source, output)
        try:
            self._carrier.write(
                str(source), str(output), {"AIGC": aigc_obj},
                policy=docx_carrier.ExistingMetadataPolicy.REJECT,
                initial_write=False,
            )
        except docx_carrier.DocxMetadataError as exc:
            output.unlink(missing_ok=True)
            raise self._file_error(exc) from None

    def remove_aigc(self, src: str | Path, dst: str | Path) -> None:
        source, output = Path(src), Path(dst)
        try:
            package = self._carrier._read_package(source.resolve())
            inspection = self.preflight(source)
            entries = dict(package.entries)
            for part_name in inspection.owned_parts:
                entries.pop(part_name, None)

            content_types = self._carrier._parse_xml(
                entries[docx_carrier._CONTENT_TYPES_PART],
                docx_carrier._CONTENT_TYPES_PART,
            )
            self._carrier._remove_owned_content_types(
                content_types, inspection.owned_parts
            )
            entries[docx_carrier._CONTENT_TYPES_PART] = self._carrier._serialize_xml(
                content_types, default_namespace=docx_carrier._CONTENT_TYPES_NS
            )

            if package.document_rels_part in entries:
                rels = self._carrier._parse_xml(
                    entries[package.document_rels_part], package.document_rels_part
                )
                self._carrier._remove_owned_relationships(
                    rels, inspection.owned_parts, package.main_document_part
                )
                entries[package.document_rels_part] = self._carrier._serialize_xml(
                    rels, default_namespace=docx_carrier._RELATIONSHIPS_NS
                )

            self._prepare_output(source, output)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output.stem}.", suffix=output.suffix or ".docx",
                dir=output.parent,
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                self._carrier._write_package(
                    temporary, package, entries, (), inspection.owned_parts
                )
                shutil.copystat(source, temporary)
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
        except docx_carrier.DocxMetadataError as exc:
            output.unlink(missing_ok=True)
            raise self._file_error(exc) from None
        if self.detect_existing(output):
            output.unlink(missing_ok=True)
            raise AdapterError("移除旧 DOCX 标识后仍能检出 AIGC 记录")

    @staticmethod
    def _shape(inspection: docx_carrier.DocxInspection) -> dict:
        return {
            "ooxml_conformance": inspection.ooxml_conformance,
            "main_document_part": inspection.main_document_part,
            "protected_content_sha256": inspection.protected_content_sha256,
        }

    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        try:
            before = self._shape(self._inspect(src))
            after = self._shape(self._inspect(dst))
        except FileContentError as exc:
            return MediaReport(passed=False, reason=f"DOCX 不可读: {exc}")
        problems = [key for key in before if before[key] != after[key]]
        return MediaReport(
            passed=not problems,
            reason=("DOCX 写入前后以下包属性变化: " + ", ".join(problems))
            if problems else None,
            before=before,
            after=after,
        )

    def content_fingerprint(self, path: str | Path) -> tuple[str, str]:
        return self._inspect(path).protected_content_sha256, "package"
