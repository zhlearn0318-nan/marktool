from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

from app.schemas.validation import (
    serialize_aigc_document,
    validate_aigc_business_rules,
    validate_aigc_document,
)


class ExistingMetadataPolicy(str, Enum):
    REJECT = "reject"
    REPLACE = "replace"


class HtmlMetadataError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


@dataclass(frozen=True)
class HtmlAigcRecord:
    property_name: str
    raw_value: str
    document: Any
    aigc: Optional[dict[str, Any]]
    parse_error: Optional[str]
    start_offset: int
    end_offset: int
    inside_head: bool


@dataclass(frozen=True)
class HtmlValidationResult:
    read_back_succeeded: bool
    schema_valid: bool
    single_aigc_record: bool
    content_integrity_valid: bool


@dataclass(frozen=True)
class HtmlMetadataWriteResult:
    output_path: str
    detected_format: str
    mime_type: str
    carrier: str
    adapter_version: str
    input_sha256: str
    output_sha256: str
    embedded_metadata: dict[str, Any]
    validation: HtmlValidationResult


@dataclass(frozen=True)
class HtmlInspection:
    encoding: str
    newline: str
    text: str
    records: tuple[HtmlAigcRecord, ...]
    head_end_offset: int
    has_c2pa_manifest: bool
    protected_content_sha256: str


@dataclass(frozen=True)
class _CandidateTag:
    property_name: str
    raw_value: Optional[str]
    parse_error: Optional[str]
    start_offset: int
    end_offset: int
    inside_head: bool


class _HtmlScanner(HTMLParser):
    """只定位标签，不重新序列化 HTML。"""

    _AIGC_NAME_ATTRIBUTES = ("name", "property", "itemprop")
    _CHARSET_VALUE = re.compile(
        r"charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)", re.IGNORECASE
    )

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self._text = text
        self._line_offsets = [0]
        self._line_offsets.extend(
            index + 1 for index, character in enumerate(text) if character == "\n"
        )
        self.html_start_count = 0
        self.html_end_count = 0
        self.head_start_count = 0
        self.head_end_count = 0
        self.head_end_offset: Optional[int] = None
        self._inside_head = False
        self._template_depth = 0
        self.candidates: list[_CandidateTag] = []
        self.has_c2pa_manifest = False
        self.declared_charsets: list[str] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    @staticmethod
    def _attribute_map(attrs: list[tuple[str, Optional[str]]]):
        mapped: dict[str, list[Optional[str]]] = {}
        for key, value in attrs:
            mapped.setdefault(key.lower(), []).append(value)
        return mapped

    def _process_void_or_startend_tag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
        start: int,
        end: int,
    ) -> None:
        mapped = self._attribute_map(attrs)
        if tag == "meta" and self._template_depth == 0:
            for value in mapped.get("charset", []):
                if value:
                    self.declared_charsets.append(value.strip().lower())
            http_equiv_values = {
                value.strip().lower()
                for value in mapped.get("http-equiv", [])
                if value is not None
            }
            if "content-type" in http_equiv_values:
                for value in mapped.get("content", []):
                    match = self._CHARSET_VALUE.search(value or "")
                    if match:
                        self.declared_charsets.append(match.group(1).lower())

            matching_names: list[str] = []
            for key in self._AIGC_NAME_ATTRIBUTES:
                for value in mapped.get(key, []):
                    if value is not None and "aigc" in value.lower():
                        matching_names.append(value)
            if matching_names:
                content_values = mapped.get("content", [])
                parse_error = None
                raw_value: Optional[str] = None
                if len(matching_names) != 1:
                    parse_error = "AIGC 元数据名称属性不唯一"
                elif len(content_values) != 1 or content_values[0] is None:
                    parse_error = "AIGC meta 必须且只能包含一个 content 属性"
                else:
                    raw_value = content_values[0]
                self.candidates.append(
                    _CandidateTag(
                        property_name=matching_names[0],
                        raw_value=raw_value,
                        parse_error=parse_error,
                        start_offset=start,
                        end_offset=end,
                        inside_head=self._inside_head,
                    )
                )

        if tag == "script":
            values = mapped.get("type", [])
            if any(
                value is not None and value.strip().lower() == "application/c2pa"
                for value in values
            ):
                self.has_c2pa_manifest = True
        elif tag == "link":
            rel_values = mapped.get("rel", [])
            if any(
                value is not None
                and "c2pa-manifest" in value.lower().split()
                for value in rel_values
            ):
                self.has_c2pa_manifest = True

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        start = self._offset()
        raw = self.get_starttag_text() or ""
        end = start + len(raw)
        if tag == "html":
            self.html_start_count += 1
        elif tag == "head":
            self.head_start_count += 1
            self._inside_head = True
        elif tag == "template":
            self._template_depth += 1
        self._process_void_or_startend_tag(tag, attrs, start, end)

    def handle_startendtag(self, tag: str, attrs):
        tag = tag.lower()
        start = self._offset()
        raw = self.get_starttag_text() or ""
        self._process_void_or_startend_tag(tag, attrs, start, start + len(raw))

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "template":
            self._template_depth = max(0, self._template_depth - 1)
        elif tag == "html":
            self.html_end_count += 1
        elif tag == "head":
            self.head_end_count += 1
            self.head_end_offset = self._offset()
            self._inside_head = False


class HtmlMetadataAdapter:
    """HTML AIGC 元数据读取、写入、整体替换和写后验证。"""

    format_name = "HTML"
    mime_type = "text/html"
    carrier = "html-meta-aigc-v1"
    adapter_version = "html-meta-byte-preserving-v1"

    _SUPPORTED_ENCODINGS = {
        "utf-8": "utf-8",
        "utf8": "utf-8",
        "gb18030": "gb18030",
    }

    def __init__(self, max_file_bytes: int = 25 * 1024 * 1024):
        self.max_file_bytes = max_file_bytes

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _normalize_policy(policy: ExistingMetadataPolicy | str) -> ExistingMetadataPolicy:
        try:
            return ExistingMetadataPolicy(policy)
        except ValueError as exc:
            raise HtmlMetadataError(
                "INVALID_EXISTING_METADATA_POLICY",
                "已有标识策略只能是 reject 或 replace",
            ) from exc

    def _read_bytes(self, path: Path) -> bytes:
        if not path.is_file():
            raise HtmlMetadataError("FILE_NOT_FOUND", "HTML 文件不存在")
        size = path.stat().st_size
        if size == 0:
            raise HtmlMetadataError("UNSUPPORTED_MEDIA_TYPE", "HTML 文件不能为空")
        if size > self.max_file_bytes:
            raise HtmlMetadataError(
                "FILE_TOO_LARGE", f"HTML 文件超过 {self.max_file_bytes} 字节上限"
            )
        return path.read_bytes()

    def _decode(self, data: bytes) -> tuple[str, str]:
        if data.startswith(
            (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")
        ):
            raise HtmlMetadataError(
                "HTML_ENCODING_UNSUPPORTED", "首期 HTML 适配器暂不支持 UTF-16/UTF-32"
            )

        has_utf8_bom = data.startswith(b"\xef\xbb\xbf")
        prescan = _HtmlScanner(data[:8192].decode("latin-1"))
        prescan.feed(data[:8192].decode("latin-1"))
        prescan.close()
        declared_values = list(dict.fromkeys(prescan.declared_charsets))
        if len(declared_values) > 1:
            raise HtmlMetadataError(
                "HTML_ENCODING_CONFLICT", "HTML 包含相互冲突的 charset 声明"
            )
        declared = declared_values[0] if declared_values else None
        if declared is not None and declared not in self._SUPPORTED_ENCODINGS:
            raise HtmlMetadataError(
                "HTML_ENCODING_UNSUPPORTED",
                f"首期 HTML 适配器暂不支持字符编码 {declared}",
            )
        if has_utf8_bom and declared not in {None, "utf-8", "utf8"}:
            raise HtmlMetadataError(
                "HTML_ENCODING_CONFLICT", "HTML BOM 与 charset 声明不一致"
            )

        codec = "utf-8-sig" if has_utf8_bom else self._SUPPORTED_ENCODINGS.get(
            declared or "utf-8", "utf-8"
        )
        try:
            return data.decode(codec, "strict"), codec
        except UnicodeDecodeError as exc:
            message = (
                "HTML 内容与声明的字符编码不一致"
                if declared is not None or has_utf8_bom
                else "HTML 不是有效 UTF-8；非 UTF-8 文件必须声明受支持的 charset"
            )
            raise HtmlMetadataError("HTML_ENCODING_INVALID", message) from exc

    @staticmethod
    def _record_from_candidate(candidate: _CandidateTag) -> HtmlAigcRecord:
        raw_value = candidate.raw_value or ""
        parse_error = candidate.parse_error
        document: Any = None
        aigc = None
        if parse_error is None:
            try:
                document = json.loads(raw_value)
            except json.JSONDecodeError as exc:
                parse_error = f"AIGC 元数据不是合法 JSON: {exc.msg}"
            else:
                if isinstance(document, dict) and isinstance(document.get("AIGC"), dict):
                    aigc = document["AIGC"]
        return HtmlAigcRecord(
            property_name=candidate.property_name,
            raw_value=raw_value,
            document=document,
            aigc=aigc,
            parse_error=parse_error,
            start_offset=candidate.start_offset,
            end_offset=candidate.end_offset,
            inside_head=candidate.inside_head,
        )

    @staticmethod
    def _without_records(text: str, records: tuple[HtmlAigcRecord, ...]) -> str:
        if not records:
            return text
        pieces: list[str] = []
        cursor = 0
        for record in sorted(records, key=lambda item: item.start_offset):
            if record.start_offset < cursor:
                raise HtmlMetadataError(
                    "HTML_STRUCTURE_INVALID", "HTML 中的 AIGC 元数据范围发生重叠"
                )
            pieces.append(text[cursor:record.start_offset])
            cursor = record.end_offset
        pieces.append(text[cursor:])
        return "".join(pieces)

    @classmethod
    def _protected_digest(
        cls, text: str, records: tuple[HtmlAigcRecord, ...]
    ) -> str:
        protected = cls._without_records(text, records)
        return hashlib.sha256(protected.encode("utf-8")).hexdigest()

    def inspect(self, file_path: str | Path) -> HtmlInspection:
        path = Path(file_path).resolve()
        data = self._read_bytes(path)
        text, encoding = self._decode(data)
        scanner = _HtmlScanner(text)
        try:
            scanner.feed(text)
            scanner.close()
        except Exception as exc:
            raise HtmlMetadataError(
                "HTML_STRUCTURE_INVALID", "无法安全解析 HTML 结构"
            ) from exc

        if scanner.html_start_count != 1 or scanner.html_end_count != 1:
            raise HtmlMetadataError(
                "UNSUPPORTED_MEDIA_TYPE", "文件不是包含唯一且完整 html 根元素的 HTML 文档"
            )
        if (
            scanner.head_start_count != 1
            or scanner.head_end_count != 1
            or scanner.head_end_offset is None
        ):
            raise HtmlMetadataError(
                "HTML_STRUCTURE_INVALID", "HTML 必须包含唯一且完整的 head 元素"
            )

        records = tuple(self._record_from_candidate(item) for item in scanner.candidates)
        newline = "\r\n" if "\r\n" in text else "\n"
        return HtmlInspection(
            encoding=encoding,
            newline=newline,
            text=text,
            records=records,
            head_end_offset=scanner.head_end_offset,
            has_c2pa_manifest=scanner.has_c2pa_manifest,
            protected_content_sha256=self._protected_digest(text, records),
        )

    def read_records(self, file_path: str | Path) -> list[HtmlAigcRecord]:
        return list(self.inspect(file_path).records)

    def inspect_existing_metadata(
        self, file_path: str | Path
    ) -> list[HtmlAigcRecord]:
        """与其他格式适配器保持一致的写入前候选记录检查入口。"""
        return self.read_records(file_path)

    @staticmethod
    def _build_meta(document: dict[str, Any]) -> str:
        serialized = serialize_aigc_document(document)
        escaped = html.escape(serialized, quote=True)
        return f'<meta name="AIGC" content="{escaped}">'

    @classmethod
    def _replace_records(
        cls,
        inspection: HtmlInspection,
        meta_tag: str,
    ) -> str:
        records = tuple(sorted(inspection.records, key=lambda item: item.start_offset))
        if not records:
            offset = inspection.head_end_offset
            return inspection.text[:offset] + meta_tag + inspection.text[offset:]

        pieces: list[str] = []
        cursor = 0
        inserted = False
        for record in records:
            pieces.append(inspection.text[cursor:record.start_offset])
            if not inserted:
                pieces.append(meta_tag)
                inserted = True
            cursor = record.end_offset
        pieces.append(inspection.text[cursor:])
        return "".join(pieces)

    def verify(
        self,
        source_path: str | Path,
        output_path: str | Path,
        expected_document: dict[str, Any],
    ) -> dict[str, Any]:
        source = self.inspect(source_path)
        output = self.inspect(output_path)
        if len(output.records) != 1:
            raise HtmlMetadataError(
                "AIGC_DUPLICATE_RECORDS",
                f"写入后检出 {len(output.records)} 份 AIGC 元数据",
            )
        record = output.records[0]
        if not record.inside_head:
            raise HtmlMetadataError(
                "METADATA_READBACK_FAILED", "写入后的 AIGC 元数据不在 head 中"
            )
        if record.parse_error is not None:
            raise HtmlMetadataError("METADATA_READBACK_FAILED", record.parse_error)
        schema_errors = validate_aigc_document(record.document)
        if schema_errors:
            raise HtmlMetadataError(
                "METADATA_READBACK_FAILED",
                "回读的 AIGC 元数据未通过七字段校验",
                schema_errors,
            )
        if record.document != expected_document:
            raise HtmlMetadataError(
                "METADATA_READBACK_FAILED", "回读的 AIGC 元数据与计划写入对象不一致"
            )
        if source.protected_content_sha256 != output.protected_content_sha256:
            raise HtmlMetadataError(
                "CONTENT_INTEGRITY_FAILED",
                "写入前后的 HTML 非 AIGC 内容发生变化",
            )
        return record.document

    def write(
        self,
        source_path: str,
        output_path: str,
        document: dict[str, Any],
        policy: ExistingMetadataPolicy | str = ExistingMetadataPolicy.REJECT,
        initial_write: bool = True,
        stage_callback: Optional[Callable[[str], None]] = None,
    ) -> HtmlMetadataWriteResult:
        schema_errors = validate_aigc_document(document)
        if schema_errors:
            raise HtmlMetadataError(
                "AIGC_SCHEMA_INVALID",
                "AIGC 元数据不符合 GB 45438—2025 附录 E 结构",
                schema_errors,
            )
        character_errors = validate_aigc_business_rules(
            document,
            strict_characters=True,
            require_initial_relationships=False,
        )
        if character_errors:
            raise HtmlMetadataError(
                "AIGC_CHARACTER_INVALID",
                "AIGC 字段含首期严格字符范围之外的字符",
                character_errors,
            )
        relationship_errors = validate_aigc_business_rules(
            document,
            strict_characters=False,
            require_initial_relationships=initial_write,
        )
        if relationship_errors:
            raise HtmlMetadataError(
                "AIGC_INITIAL_RELATION_INVALID",
                "首次写入时生产字段与传播字段必须一致",
                relationship_errors,
            )
        try:
            meta_tag = self._build_meta(document)
        except ValueError as exc:
            raise HtmlMetadataError(
                "AIGC_LENGTH_INVALID", "AIGC 元数据超过项目写入长度上限"
            ) from exc

        policy_value = self._normalize_policy(policy)
        source = Path(source_path).resolve()
        output = Path(output_path).resolve()
        if source == output:
            raise HtmlMetadataError(
                "OUTPUT_PATH_INVALID", "结果文件必须与原文件分开保存"
            )
        if output.exists():
            raise HtmlMetadataError("OUTPUT_FILE_EXISTS", "结果文件已存在，拒绝覆盖")

        source_data = self._read_bytes(source)
        source_sha256 = self._sha256_bytes(source_data)
        inspection = self.inspect(source)
        outside_records = [item for item in inspection.records if not item.inside_head]
        if outside_records:
            raise HtmlMetadataError(
                "AIGC_CARRIER_INVALID",
                "发现位于 head 之外的 AIGC meta，无法安全判断标识状态",
            )
        if inspection.has_c2pa_manifest:
            raise HtmlMetadataError(
                "C2PA_RESIGN_REQUIRED",
                "HTML 已关联 C2PA 清单；修改文件前必须具备重新签名能力",
            )
        if inspection.records and policy_value is ExistingMetadataPolicy.REJECT:
            raise HtmlMetadataError(
                "AIGC_METADATA_EXISTS",
                f"原文件已存在 {len(inspection.records)} 份 AIGC 元数据",
            )

        if stage_callback:
            stage_callback("writing_metadata")
        updated_text = self._replace_records(inspection, meta_tag)
        try:
            updated_data = updated_text.encode(inspection.encoding, "strict")
        except UnicodeEncodeError as exc:
            raise HtmlMetadataError(
                "HTML_ENCODING_INVALID", "AIGC 元数据无法使用原 HTML 编码保存"
            ) from exc

        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}.",
            suffix=output.suffix or ".html",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_bytes(updated_data)
            shutil.copystat(source, temporary)
            if stage_callback:
                stage_callback("verifying_metadata")
            embedded = self.verify(source, temporary, document)
            if self._sha256_bytes(self._read_bytes(source)) != source_sha256:
                raise HtmlMetadataError(
                    "CONTENT_INTEGRITY_FAILED", "处理期间原 HTML 文件发生变化"
                )
            output_sha256 = self._sha256_bytes(updated_data)
            if stage_callback:
                stage_callback("publishing_output")
            os.replace(temporary, output)
            return HtmlMetadataWriteResult(
                output_path=str(output),
                detected_format=self.format_name,
                mime_type=self.mime_type,
                carrier=self.carrier,
                adapter_version=self.adapter_version,
                input_sha256=source_sha256,
                output_sha256=output_sha256,
                embedded_metadata=embedded,
                validation=HtmlValidationResult(
                    read_back_succeeded=True,
                    schema_valid=True,
                    single_aigc_record=True,
                    content_integrity_valid=True,
                ),
            )
        finally:
            temporary.unlink(missing_ok=True)
