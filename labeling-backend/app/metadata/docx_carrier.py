from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional
from xml.etree import ElementTree as ET

from app.metadata.html_carrier import ExistingMetadataPolicy
from app.schemas.validation import (
    serialize_aigc_document,
    validate_aigc_business_rules,
    validate_aigc_document,
)


_CONTENT_TYPES_PART = "[Content_Types].xml"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_TRANSITIONAL_OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_STRICT_OFFICE_REL_NS = "http://purl.oclc.org/ooxml/officeDocument/relationships"
_TRANSITIONAL_CUSTOM_XML_PROPS_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/customXmlDataProps"
)
_STRICT_CUSTOM_XML_PROPS_NS = (
    "http://purl.oclc.org/ooxml/officeDocument/customXmlDataProps"
)
_OFFICE_PROFILES = {
    f"{_TRANSITIONAL_OFFICE_REL_NS}/officeDocument": (
        "transitional",
        _TRANSITIONAL_OFFICE_REL_NS,
        _TRANSITIONAL_CUSTOM_XML_PROPS_NS,
    ),
    f"{_STRICT_OFFICE_REL_NS}/officeDocument": (
        "strict",
        _STRICT_OFFICE_REL_NS,
        _STRICT_CUSTOM_XML_PROPS_NS,
    ),
}
_CUSTOM_XML_REL_TYPES = {
    f"{_TRANSITIONAL_OFFICE_REL_NS}/customXml",
    f"{_STRICT_OFFICE_REL_NS}/customXml",
}
_CUSTOM_XML_PROPS_REL_TYPES = {
    f"{_TRANSITIONAL_OFFICE_REL_NS}/customXmlProps",
    f"{_STRICT_OFFICE_REL_NS}/customXmlProps",
}
_CUSTOM_XML_PROPS_NAMESPACES = {
    _TRANSITIONAL_CUSTOM_XML_PROPS_NS,
    _STRICT_CUSTOM_XML_PROPS_NS,
}
_AIGC_NS = "urn:marktool:gb45438:2025:aigc-metadata:v1"
_AIGC_ROOT_TAG = f"{{{_AIGC_NS}}}AIGCMetadata"
_AIGC_VALUE_TAG = f"{{{_AIGC_NS}}}AIGC"
_DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document.main+xml"
)
_CUSTOM_XML_PROPS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"
)


class DocxMetadataError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


@dataclass(frozen=True)
class DocxAigcRecord:
    property_name: str
    raw_value: str
    document: Any
    aigc: Optional[dict[str, Any]]
    parse_error: Optional[str]
    part_name: str
    element_index: int
    canonical_carrier: bool
    carrier_error: Optional[str]


@dataclass(frozen=True)
class DocxInspection:
    records: tuple[DocxAigcRecord, ...]
    owned_parts: frozenset[str]
    has_digital_signature: bool
    protected_content_sha256: str
    ooxml_conformance: str
    main_document_part: str


@dataclass(frozen=True)
class DocxValidationResult:
    read_back_succeeded: bool
    schema_valid: bool
    single_aigc_record: bool
    package_integrity_valid: bool
    content_integrity_valid: bool


@dataclass(frozen=True)
class DocxMetadataWriteResult:
    output_path: str
    detected_format: str
    mime_type: str
    carrier: str
    adapter_version: str
    input_sha256: str
    output_sha256: str
    embedded_metadata: dict[str, Any]
    validation: DocxValidationResult


@dataclass(frozen=True)
class _Package:
    infos: tuple[zipfile.ZipInfo, ...]
    entries: dict[str, bytes]
    main_document_part: str
    document_rels_part: str
    ooxml_conformance: str
    office_relationship_namespace: str
    custom_xml_props_namespace: str


class DocxMetadataAdapter:
    """以 OOXML Custom XML Part 承载 AIGC 七字段。"""

    format_name = "DOCX"
    mime_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    carrier = "ooxml-custom-xml-aigc-v1"
    adapter_version = "docx-custom-xml-preserving-v1"

    def __init__(
        self,
        max_file_bytes: int = 25 * 1024 * 1024,
        max_entries: int = 4096,
        max_uncompressed_bytes: int = 200 * 1024 * 1024,
        max_part_bytes: int = 64 * 1024 * 1024,
        max_compression_ratio: int = 200,
    ):
        self.max_file_bytes = max_file_bytes
        self.max_entries = max_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_part_bytes = max_part_bytes
        self.max_compression_ratio = max_compression_ratio

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _normalize_policy(policy: ExistingMetadataPolicy | str) -> ExistingMetadataPolicy:
        try:
            return ExistingMetadataPolicy(policy)
        except ValueError as exc:
            raise DocxMetadataError(
                "INVALID_EXISTING_METADATA_POLICY",
                "已有标识策略只能是 reject 或 replace",
            ) from exc

    @staticmethod
    def _safe_part_name(name: str) -> bool:
        if not name or "\\" in name or name.startswith(("/", "\\")):
            return False
        path = PurePosixPath(name)
        return not any(part in {"", ".", ".."} for part in path.parts)

    @staticmethod
    def _parse_xml(data: bytes, part_name: str) -> ET.Element:
        prefix = data[:4096].upper()
        if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
            raise DocxMetadataError(
                "DOCX_XML_UNSAFE", f"{part_name} 包含不允许的 DTD 或实体声明"
            )
        try:
            return ET.fromstring(data)
        except ET.ParseError as exc:
            raise DocxMetadataError(
                "DOCX_XML_INVALID", f"{part_name} 不是合法 XML"
            ) from exc

    @staticmethod
    def _serialize_xml(root: ET.Element, *, default_namespace: Optional[str] = None) -> bytes:
        if default_namespace:
            ET.register_namespace("", default_namespace)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _rels_part_name(source_part: str) -> str:
        return posixpath.join(
            posixpath.dirname(source_part),
            "_rels",
            posixpath.basename(source_part) + ".rels",
        )

    def _read_package(self, path: Path) -> _Package:
        if not path.is_file():
            raise DocxMetadataError("FILE_NOT_FOUND", "DOCX 文件不存在")
        size = path.stat().st_size
        if size == 0:
            raise DocxMetadataError("UNSUPPORTED_MEDIA_TYPE", "DOCX 文件不能为空")
        if size > self.max_file_bytes:
            raise DocxMetadataError(
                "FILE_TOO_LARGE", f"DOCX 文件超过 {self.max_file_bytes} 字节上限"
            )
        if not zipfile.is_zipfile(path):
            raise DocxMetadataError(
                "UNSUPPORTED_MEDIA_TYPE", "文件内容不是有效的 OOXML ZIP 容器"
            )

        try:
            with zipfile.ZipFile(path, "r") as archive:
                infos = tuple(archive.infolist())
                if len(infos) > self.max_entries:
                    raise DocxMetadataError(
                        "DOCX_PACKAGE_LIMIT_EXCEEDED", "DOCX ZIP 部件数量超过安全上限"
                    )
                names = [info.filename for info in infos]
                if len(set(names)) != len(names) or len(
                    {name.casefold() for name in names}
                ) != len(names):
                    raise DocxMetadataError(
                        "DOCX_DUPLICATE_PARTS", "DOCX ZIP 包含重复或大小写冲突的部件名"
                    )
                if any(not self._safe_part_name(name) for name in names):
                    raise DocxMetadataError(
                        "DOCX_UNSAFE_PART_NAME", "DOCX ZIP 包含不安全的部件路径"
                    )

                total = 0
                for info in infos:
                    if info.flag_bits & 0x1:
                        raise DocxMetadataError(
                            "DOCX_ENCRYPTED_UNSUPPORTED", "当前不支持加密的 DOCX ZIP 部件"
                        )
                    if info.file_size > self.max_part_bytes:
                        raise DocxMetadataError(
                            "DOCX_PACKAGE_LIMIT_EXCEEDED", "DOCX 单个部件超过安全上限"
                        )
                    total += info.file_size
                    if total > self.max_uncompressed_bytes:
                        raise DocxMetadataError(
                            "DOCX_PACKAGE_LIMIT_EXCEEDED", "DOCX 解压后总体积超过安全上限"
                        )
                    if info.file_size and info.compress_size == 0:
                        raise DocxMetadataError(
                            "DOCX_SUSPICIOUS_COMPRESSION", "DOCX 部件压缩信息异常"
                        )
                    if (
                        info.compress_size
                        and info.file_size / info.compress_size > self.max_compression_ratio
                    ):
                        raise DocxMetadataError(
                            "DOCX_SUSPICIOUS_COMPRESSION", "DOCX 部件压缩比超过安全上限"
                        )
                entries = {info.filename: archive.read(info) for info in infos}
                if archive.testzip() is not None:
                    raise DocxMetadataError(
                        "DOCX_PACKAGE_CORRUPT", "DOCX ZIP 部件 CRC 校验失败"
                    )
        except DocxMetadataError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise DocxMetadataError(
                "DOCX_PACKAGE_CORRUPT", "无法完整读取 DOCX ZIP 容器"
            ) from exc

        required = {_CONTENT_TYPES_PART, "_rels/.rels"}
        if not required.issubset(entries):
            raise DocxMetadataError(
                "UNSUPPORTED_MEDIA_TYPE", "ZIP 容器缺少 DOCX 必需的 OOXML 部件"
            )
        root_rels = self._parse_xml(entries["_rels/.rels"], "_rels/.rels")
        office_document_relationships = [
            relationship
            for relationship in root_rels
            if relationship.get("Type") in _OFFICE_PROFILES
            and relationship.get("TargetMode", "Internal") == "Internal"
        ]
        if len(office_document_relationships) != 1:
            raise DocxMetadataError(
                "UNSUPPORTED_MEDIA_TYPE",
                "OOXML 包必须通过唯一内部关系指向 Word 主文档部件",
            )
        office_relationship = office_document_relationships[0]
        main_document_part = posixpath.normpath(
            office_relationship.get("Target", "").lstrip("/")
        )
        if (
            not self._safe_part_name(main_document_part)
            or main_document_part not in entries
        ):
            raise DocxMetadataError(
                "UNSUPPORTED_MEDIA_TYPE", "Word 主文档关系指向无效部件"
            )

        profile = _OFFICE_PROFILES[office_relationship.get("Type")]
        content_types = self._parse_xml(
            entries[_CONTENT_TYPES_PART], _CONTENT_TYPES_PART
        )
        main_types = {
            item.get("ContentType")
            for item in content_types
            if item.tag == f"{{{_CONTENT_TYPES_NS}}}Override"
            and item.get("PartName") == f"/{main_document_part}"
        }
        if _DOCX_MAIN_CONTENT_TYPE not in main_types:
            raise DocxMetadataError(
                "UNSUPPORTED_MEDIA_TYPE", "OOXML 容器不是无宏的 Word DOCX 文档"
            )
        self._parse_xml(entries[main_document_part], main_document_part)
        return _Package(
            infos=infos,
            entries=entries,
            main_document_part=main_document_part,
            document_rels_part=self._rels_part_name(main_document_part),
            ooxml_conformance=profile[0],
            office_relationship_namespace=profile[1],
            custom_xml_props_namespace=profile[2],
        )

    @staticmethod
    def _relationship_target(source_part: str, target: str) -> Optional[str]:
        if not target or ":" in target.split("/", 1)[0] or target.startswith("/"):
            return None
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_part), target)
        )
        if resolved.startswith("../") or resolved == "..":
            return None
        return resolved

    def _document_custom_xml_targets(self, package: _Package) -> set[str]:
        if package.document_rels_part not in package.entries:
            return set()
        root = self._parse_xml(
            package.entries[package.document_rels_part],
            package.document_rels_part,
        )
        targets: set[str] = set()
        for relationship in root:
            if relationship.get("Type") != (
                f"{package.office_relationship_namespace}/customXml"
            ):
                continue
            if relationship.get("TargetMode", "Internal") != "Internal":
                continue
            target = self._relationship_target(
                package.main_document_part, relationship.get("Target", "")
            )
            if target is not None:
                targets.add(target)
        return targets

    @staticmethod
    def _record(
        raw_value: str,
        part_name: str,
        index: int,
        canonical: bool,
        carrier_error: Optional[str],
    ) -> DocxAigcRecord:
        document: Any = None
        aigc = None
        parse_error = None
        try:
            document = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            parse_error = f"AIGC 元数据不是合法 JSON: {exc.msg}"
        else:
            if isinstance(document, dict) and isinstance(document.get("AIGC"), dict):
                aigc = document["AIGC"]
        return DocxAigcRecord(
            property_name="AIGC",
            raw_value=raw_value,
            document=document,
            aigc=aigc,
            parse_error=parse_error,
            part_name=part_name,
            element_index=index,
            canonical_carrier=canonical,
            carrier_error=carrier_error,
        )

    def _existing_item_ids(self, entries: dict[str, bytes]) -> list[str]:
        item_ids: list[str] = []
        for part_name, data in entries.items():
            if not (
                part_name.startswith("customXml/")
                and part_name.endswith(".xml")
                and "/_rels/" not in part_name
            ):
                continue
            root = self._parse_xml(data, part_name)
            for namespace in _CUSTOM_XML_PROPS_NAMESPACES:
                if root.tag != f"{{{namespace}}}datastoreItem":
                    continue
                item_id = root.get(f"{{{namespace}}}itemID", "")
                if item_id:
                    item_ids.append(item_id.casefold())
        return item_ids

    def _scan_records(
        self, package: _Package
    ) -> tuple[tuple[DocxAigcRecord, ...], frozenset[str]]:
        entries = package.entries
        related_targets = self._document_custom_xml_targets(package)
        content_types_root = self._parse_xml(
            entries[_CONTENT_TYPES_PART], _CONTENT_TYPES_PART
        )
        content_types = {
            (item.get("PartName") or "").lstrip("/"): item.get("ContentType")
            for item in content_types_root
            if item.tag == f"{{{_CONTENT_TYPES_NS}}}Override"
        }
        records: list[DocxAigcRecord] = []
        owned_data_parts: set[str] = set()
        carrier_details: dict[str, tuple[bool, Optional[str], set[str]]] = {}
        all_item_ids = self._existing_item_ids(entries)
        item_id_counts = Counter(all_item_ids)
        for part_name in sorted(entries):
            if not (
                part_name.startswith("customXml/")
                and part_name.endswith(".xml")
                and "/_rels/" not in part_name
            ):
                continue
            root = self._parse_xml(entries[part_name], part_name)
            canonical_part = root.tag == _AIGC_ROOT_TAG
            if canonical_part:
                owned_data_parts.add(part_name)
                rels_part = self._rels_part_name(part_name)
                owned_support = {rels_part}
                errors: list[str] = []
                direct_children = list(root)
                direct_aigc_ids = {
                    id(element)
                    for element in direct_children
                    if element.tag == _AIGC_VALUE_TAG
                }
                nested_aigc = [
                    element
                    for element in root.iter()
                    if element is not root
                    and element.tag == _AIGC_VALUE_TAG
                    and id(element) not in direct_aigc_ids
                ]
                unexpected_children = [
                    element
                    for element in direct_children
                    if element.tag != _AIGC_VALUE_TAG
                ]
                if nested_aigc:
                    errors.append("AIGC 元素不是项目载体根元素的直接子元素")
                if unexpected_children:
                    errors.append("AIGC 项目载体包含不支持的额外元素")
                if part_name not in related_targets:
                    errors.append("AIGC Custom XML 未由 Word 主文档关系引用")
                if rels_part not in entries:
                    errors.append("AIGC Custom XML 缺少属性部件关系")
                else:
                    rels_root = self._parse_xml(entries[rels_part], rels_part)
                    props_relationships = [
                        relationship
                        for relationship in rels_root
                        if relationship.get("Type") in _CUSTOM_XML_PROPS_REL_TYPES
                        and relationship.get("TargetMode", "Internal") == "Internal"
                    ]
                    if len(props_relationships) != 1:
                        errors.append("AIGC Custom XML 必须关联唯一属性部件")
                    else:
                        props_relationship = props_relationships[0]
                        expected_type = (
                            f"{package.office_relationship_namespace}/customXmlProps"
                        )
                        if props_relationship.get("Type") != expected_type:
                            errors.append("Custom XML 属性关系与 OOXML 类型不一致")
                        props_part = self._relationship_target(
                            part_name, props_relationship.get("Target", "")
                        )
                        if (
                            props_part is None
                            or not props_part.startswith("customXml/")
                            or props_part not in entries
                        ):
                            errors.append("Custom XML 属性关系指向无效部件")
                        elif content_types.get(props_part) != (
                            _CUSTOM_XML_PROPS_CONTENT_TYPE
                        ):
                            errors.append("Custom XML 属性部件内容类型无效")
                        else:
                            props_root = self._parse_xml(entries[props_part], props_part)
                            expected_root = (
                                f"{{{package.custom_xml_props_namespace}}}datastoreItem"
                            )
                            item_id_attribute = (
                                f"{{{package.custom_xml_props_namespace}}}itemID"
                            )
                            if props_root.tag != expected_root:
                                errors.append("Custom XML 属性部件根元素或命名空间无效")
                            else:
                                item_id = props_root.get(item_id_attribute, "")
                                if not re.fullmatch(
                                    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-"
                                    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                                    r"[0-9A-Fa-f]{12}\}",
                                    item_id,
                                ):
                                    errors.append("Custom XML itemID 不是规范 GUID")
                                else:
                                    if item_id_counts[item_id.casefold()] > 1:
                                        errors.append(
                                            "Custom XML itemID 与包内其他数据部件重复"
                                        )
                            owned_support.add(props_part)
                carrier_details[part_name] = (
                    not errors,
                    "; ".join(errors) if errors else None,
                    owned_support,
                )

        for part_name in sorted(entries):
            if not (
                part_name.startswith("customXml/")
                and part_name.endswith(".xml")
                and "/_rels/" not in part_name
            ):
                continue
            root = self._parse_xml(entries[part_name], part_name)
            canonical_part = root.tag == _AIGC_ROOT_TAG
            direct_aigc_ids = {
                id(element) for element in list(root) if element.tag == _AIGC_VALUE_TAG
            }
            carrier_valid, carrier_error, _ = carrier_details.get(
                part_name, (False, "非本项目规范 Custom XML 载体", set())
            )
            for index, element in enumerate(root.iter(), start=1):
                local_name = element.tag.rsplit("}", 1)[-1]
                if local_name.casefold() != "aigc":
                    continue
                raw_value = element.text or ""
                canonical = (
                    canonical_part
                    and element.tag == _AIGC_VALUE_TAG
                    and id(element) in direct_aigc_ids
                    and carrier_valid
                )
                records.append(
                    self._record(
                        raw_value,
                        part_name,
                        index,
                        canonical,
                        carrier_error,
                    )
                )

        owned_parts = set(owned_data_parts)
        for data_part in owned_data_parts:
            _, _, support = carrier_details[data_part]
            owned_parts.update(part for part in support if part in entries)
        return tuple(records), frozenset(owned_parts)

    @staticmethod
    def _canonical_element(element: ET.Element) -> str:
        attributes = "".join(
            f" {key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in sorted(element.attrib.items())
        )
        text = (element.text or "").strip()
        children = "".join(
            DocxMetadataAdapter._canonical_element(child) for child in element
        )
        return f"<{element.tag}{attributes}>{text}{children}</{element.tag}>"

    def _normalized_support_xml(
        self,
        package: _Package,
        part_name: str,
        data: bytes,
        owned_parts: frozenset[str],
    ) -> Optional[bytes]:
        root = self._parse_xml(data, part_name)
        if part_name == _CONTENT_TYPES_PART:
            for child in list(root):
                if child.tag != f"{{{_CONTENT_TYPES_NS}}}Override":
                    continue
                target = (child.get("PartName") or "").lstrip("/")
                if target in owned_parts:
                    root.remove(child)
        elif part_name == package.document_rels_part:
            for child in list(root):
                if child.get("Type") not in _CUSTOM_XML_REL_TYPES:
                    continue
                target = self._relationship_target(
                    package.main_document_part, child.get("Target", "")
                )
                if target in owned_parts:
                    root.remove(child)
            if len(root) == 0:
                return None
        return self._canonical_element(root).encode("utf-8")

    def _protected_digest(
        self,
        package: _Package,
        owned_parts: frozenset[str],
    ) -> str:
        digest = hashlib.sha256()
        entries = package.entries
        for part_name in sorted(entries):
            if part_name in owned_parts:
                continue
            data = entries[part_name]
            if part_name in {_CONTENT_TYPES_PART, package.document_rels_part}:
                normalized = self._normalized_support_xml(
                    package, part_name, data, owned_parts
                )
                if normalized is None:
                    continue
                data = normalized
            digest.update(part_name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(data).digest())
        return digest.hexdigest()

    def inspect(self, file_path: str | Path) -> DocxInspection:
        package = self._read_package(Path(file_path).resolve())
        records, owned_parts = self._scan_records(package)
        has_signature = any(
            name.casefold().startswith("_xmlsignatures/")
            for name in package.entries
        ) or any(
            name.endswith(".rels") and b"digital-signature" in data.lower()
            for name, data in package.entries.items()
        )
        return DocxInspection(
            records=records,
            owned_parts=owned_parts,
            has_digital_signature=has_signature,
            protected_content_sha256=self._protected_digest(
                package, owned_parts
            ),
            ooxml_conformance=package.ooxml_conformance,
            main_document_part=package.main_document_part,
        )

    def read_records(self, file_path: str | Path) -> list[DocxAigcRecord]:
        return list(self.inspect(file_path).records)

    def inspect_existing_metadata(self, file_path: str | Path) -> list[DocxAigcRecord]:
        return self.read_records(file_path)

    @staticmethod
    def _remove_owned_relationships(
        root: ET.Element,
        owned_parts: frozenset[str],
        main_document_part: str,
    ) -> None:
        for child in list(root):
            if child.get("Type") not in _CUSTOM_XML_REL_TYPES:
                continue
            target = DocxMetadataAdapter._relationship_target(
                main_document_part, child.get("Target", "")
            )
            if target in owned_parts:
                root.remove(child)

    @staticmethod
    def _remove_owned_content_types(
        root: ET.Element, owned_parts: frozenset[str]
    ) -> None:
        for child in list(root):
            if child.tag != f"{{{_CONTENT_TYPES_NS}}}Override":
                continue
            if (child.get("PartName") or "").lstrip("/") in owned_parts:
                root.remove(child)

    @staticmethod
    def _next_names(entries: dict[str, bytes]) -> tuple[str, str, str]:
        existing = {name.casefold() for name in entries}
        for index in range(1, 10000):
            data_part = f"customXml/aigcMetadata{index}.xml"
            props_part = f"customXml/aigcMetadataProps{index}.xml"
            rels_part = f"customXml/_rels/aigcMetadata{index}.xml.rels"
            if not any(
                name.casefold() in existing
                for name in (data_part, props_part, rels_part)
            ):
                return data_part, props_part, rels_part
        raise DocxMetadataError(
            "DOCX_PART_NAME_EXHAUSTED", "无法为 AIGC Custom XML 分配安全部件名"
        )

    @staticmethod
    def _next_relationship_id(root: ET.Element) -> str:
        existing = {child.get("Id") for child in root}
        for index in range(1, 10000):
            candidate = f"rIdAigcMetadata{index}"
            if candidate not in existing:
                return candidate
        raise DocxMetadataError(
            "DOCX_RELATIONSHIP_ID_EXHAUSTED", "无法为 AIGC 部件分配关系编号"
        )

    def _new_item_id(self, entries: dict[str, bytes]) -> str:
        existing = set(self._existing_item_ids(entries))
        for _ in range(100):
            candidate = "{" + str(uuid.uuid4()).upper() + "}"
            if candidate.casefold() not in existing:
                return candidate
        raise DocxMetadataError(
            "DOCX_ITEM_ID_EXHAUSTED", "无法为 Custom XML 分配唯一 itemID"
        )

    @staticmethod
    def _new_zip_info(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        return info

    def _updated_entries(
        self,
        package: _Package,
        inspection: DocxInspection,
        document: dict[str, Any],
    ) -> tuple[dict[str, bytes], tuple[str, str, str]]:
        entries = dict(package.entries)
        for part_name in inspection.owned_parts:
            entries.pop(part_name, None)

        content_types = self._parse_xml(entries[_CONTENT_TYPES_PART], _CONTENT_TYPES_PART)
        self._remove_owned_content_types(content_types, inspection.owned_parts)

        if package.document_rels_part in entries:
            document_rels = self._parse_xml(
                entries[package.document_rels_part], package.document_rels_part
            )
            self._remove_owned_relationships(
                document_rels,
                inspection.owned_parts,
                package.main_document_part,
            )
        else:
            document_rels = ET.Element(f"{{{_RELATIONSHIPS_NS}}}Relationships")

        # 即使旧 AIGC 部件即将删除，也不复用其名称，避免 ZIP 中的历史
        # ZipInfo 与新部件发生混淆。
        data_part, props_part, rels_part = self._next_names(package.entries)
        relation_id = self._next_relationship_id(document_rels)
        ET.SubElement(
            document_rels,
            f"{{{_RELATIONSHIPS_NS}}}Relationship",
            {
                "Id": relation_id,
                "Type": f"{package.office_relationship_namespace}/customXml",
                "Target": posixpath.relpath(
                    data_part, posixpath.dirname(package.main_document_part)
                ),
            },
        )

        for part_name, content_type in (
            (data_part, "application/xml"),
            (props_part, _CUSTOM_XML_PROPS_CONTENT_TYPE),
        ):
            ET.SubElement(
                content_types,
                f"{{{_CONTENT_TYPES_NS}}}Override",
                {"PartName": f"/{part_name}", "ContentType": content_type},
            )

        serialized = serialize_aigc_document(document)
        ET.register_namespace("aigc", _AIGC_NS)
        item_root = ET.Element(_AIGC_ROOT_TAG)
        ET.SubElement(item_root, _AIGC_VALUE_TAG).text = serialized

        item_id = self._new_item_id(entries)
        ET.register_namespace("ds", package.custom_xml_props_namespace)
        props_root = ET.Element(
            f"{{{package.custom_xml_props_namespace}}}datastoreItem",
            {f"{{{package.custom_xml_props_namespace}}}itemID": item_id},
        )
        ET.SubElement(
            props_root, f"{{{package.custom_xml_props_namespace}}}schemaRefs"
        )

        item_rels = ET.Element(f"{{{_RELATIONSHIPS_NS}}}Relationships")
        ET.SubElement(
            item_rels,
            f"{{{_RELATIONSHIPS_NS}}}Relationship",
            {
                "Id": "rId1",
                "Type": f"{package.office_relationship_namespace}/customXmlProps",
                "Target": posixpath.basename(props_part),
            },
        )

        entries[_CONTENT_TYPES_PART] = self._serialize_xml(
            content_types, default_namespace=_CONTENT_TYPES_NS
        )
        entries[package.document_rels_part] = self._serialize_xml(
            document_rels, default_namespace=_RELATIONSHIPS_NS
        )
        entries[data_part] = self._serialize_xml(item_root)
        entries[props_part] = self._serialize_xml(props_root)
        entries[rels_part] = self._serialize_xml(
            item_rels, default_namespace=_RELATIONSHIPS_NS
        )
        return entries, (data_part, props_part, rels_part)

    def _write_package(
        self,
        temporary: Path,
        package: _Package,
        entries: dict[str, bytes],
        new_parts: tuple[str, str, str],
        removed_parts: frozenset[str],
    ) -> None:
        original_names = {info.filename for info in package.infos}
        with zipfile.ZipFile(temporary, "w") as archive:
            for info in package.infos:
                if info.filename in removed_parts:
                    continue
                archive.writestr(info, entries[info.filename])
            for part_name in (package.document_rels_part, *new_parts):
                if part_name in original_names:
                    continue
                archive.writestr(self._new_zip_info(part_name), entries[part_name])

    def verify(
        self,
        source_path: str | Path,
        output_path: str | Path,
        expected_document: dict[str, Any],
    ) -> dict[str, Any]:
        source = self.inspect(source_path)
        output = self.inspect(output_path)
        if len(output.records) != 1:
            raise DocxMetadataError(
                "AIGC_DUPLICATE_RECORDS",
                f"写入后检出 {len(output.records)} 份 AIGC 元数据",
            )
        record = output.records[0]
        if not record.canonical_carrier:
            raise DocxMetadataError(
                "METADATA_READBACK_FAILED", "写入后的 AIGC 元数据不在规范载体中"
            )
        if record.parse_error is not None:
            raise DocxMetadataError("METADATA_READBACK_FAILED", record.parse_error)
        schema_errors = validate_aigc_document(record.document)
        if schema_errors:
            raise DocxMetadataError(
                "METADATA_READBACK_FAILED",
                "回读的 AIGC 元数据未通过七字段校验",
                schema_errors,
            )
        if record.document != expected_document:
            raise DocxMetadataError(
                "METADATA_READBACK_FAILED", "回读的 AIGC 元数据与计划写入对象不一致"
            )
        if source.protected_content_sha256 != output.protected_content_sha256:
            raise DocxMetadataError(
                "CONTENT_INTEGRITY_FAILED", "写入前后的 DOCX 非 AIGC 部件发生变化"
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
    ) -> DocxMetadataWriteResult:
        schema_errors = validate_aigc_document(document)
        if schema_errors:
            raise DocxMetadataError(
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
            raise DocxMetadataError(
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
            raise DocxMetadataError(
                "AIGC_INITIAL_RELATION_INVALID",
                "首次写入时生产字段与传播字段必须一致",
                relationship_errors,
            )

        policy_value = self._normalize_policy(policy)
        source = Path(source_path).resolve()
        output = Path(output_path).resolve()
        if source == output:
            raise DocxMetadataError(
                "OUTPUT_PATH_INVALID", "结果文件必须与原文件分开保存"
            )
        if output.exists():
            raise DocxMetadataError("OUTPUT_FILE_EXISTS", "结果文件已存在，拒绝覆盖")

        if not source.is_file():
            raise DocxMetadataError("FILE_NOT_FOUND", "DOCX 文件不存在")
        if source.stat().st_size > self.max_file_bytes:
            raise DocxMetadataError(
                "FILE_TOO_LARGE", f"DOCX 文件超过 {self.max_file_bytes} 字节上限"
            )
        source_data = source.read_bytes()
        source_sha256 = self._sha256_bytes(source_data)
        package = self._read_package(source)
        if self._sha256_bytes(source.read_bytes()) != source_sha256:
            raise DocxMetadataError(
                "CONTENT_INTEGRITY_FAILED", "读取期间原 DOCX 文件发生变化"
            )
        inspection = self.inspect(source)
        if self._sha256_bytes(source.read_bytes()) != source_sha256:
            raise DocxMetadataError(
                "CONTENT_INTEGRITY_FAILED", "检查期间原 DOCX 文件发生变化"
            )
        if inspection.has_digital_signature:
            raise DocxMetadataError(
                "DOCX_RESIGN_REQUIRED",
                "DOCX 包含 Office 数字签名；修改前必须具备重新签名能力",
            )
        unsupported_records = [
            record for record in inspection.records if not record.canonical_carrier
        ]
        if unsupported_records:
            raise DocxMetadataError(
                "AIGC_CARRIER_UNSUPPORTED",
                "发现非本项目规范载体中的 AIGC 数据，不能安全自动改写",
            )
        if inspection.records and policy_value is ExistingMetadataPolicy.REJECT:
            raise DocxMetadataError(
                "AIGC_METADATA_EXISTS",
                f"原文件已存在 {len(inspection.records)} 份 AIGC 元数据",
            )

        if stage_callback:
            stage_callback("writing_metadata")
        entries, new_parts = self._updated_entries(package, inspection, document)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}.", suffix=output.suffix or ".docx", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self._write_package(
                temporary,
                package,
                entries,
                new_parts,
                inspection.owned_parts,
            )
            shutil.copystat(source, temporary)
            if stage_callback:
                stage_callback("verifying_metadata")
            embedded = self.verify(source, temporary, document)
            if self._sha256_bytes(source.read_bytes()) != source_sha256:
                raise DocxMetadataError(
                    "CONTENT_INTEGRITY_FAILED", "处理期间原 DOCX 文件发生变化"
                )
            output_sha256 = self._sha256_bytes(temporary.read_bytes())
            if stage_callback:
                stage_callback("publishing_output")
            os.replace(temporary, output)
            return DocxMetadataWriteResult(
                output_path=str(output),
                detected_format=self.format_name,
                mime_type=self.mime_type,
                carrier=self.carrier,
                adapter_version=self.adapter_version,
                input_sha256=source_sha256,
                output_sha256=output_sha256,
                embedded_metadata=embedded,
                validation=DocxValidationResult(
                    read_back_succeeded=True,
                    schema_valid=True,
                    single_aigc_record=True,
                    package_integrity_valid=True,
                    content_integrity_valid=True,
                ),
            )
        finally:
            temporary.unlink(missing_ok=True)
