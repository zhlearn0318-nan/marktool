import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

from app.metadata.exiftool_client import (
    ExifToolClient,
    ExifToolExecutionError,
    ExifToolNotFoundError,
)
from app.metadata.identifier_registry import (
    DuplicateIdentifierError,
    IdentifierRegistry,
    SQLiteIdentifierRegistry,
)
from app.metadata.xmp_reader import read_aigc_records
from app.schemas.validation import (
    serialize_aigc_document,
    validate_aigc_business_rules,
    validate_aigc_document,
)


class ExistingMetadataPolicy(str, Enum):
    REJECT = "reject"
    REPLACE = "replace"


class ImageMetadataError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


@dataclass(frozen=True)
class ImageValidationResult:
    read_back_succeeded: bool
    schema_valid: bool
    single_aigc_record: bool
    media_integrity_valid: bool


@dataclass(frozen=True)
class ImageMetadataWriteResult:
    output_path: str
    detected_format: str
    mime_type: str
    carrier: str
    adapter_version: str
    input_sha256: str
    output_sha256: str
    embedded_metadata: dict
    validation: ImageValidationResult


@dataclass(frozen=True)
class _ImageSnapshot:
    format_name: str
    mime_type: str
    size: tuple[int, int]
    mode: str
    pixel_sha256: str


class BaseImageMetadataAdapter:
    format_name: str
    mime_type: str
    carrier = "xmp-aigc-v1"
    adapter_version = "jpeg-png-xmp-exiftool-v1"

    def write(self, client: ExifToolClient, file_path: str, serialized: str) -> None:
        client.write_aigc(file_path, serialized)


class JpegXmpAdapter(BaseImageMetadataAdapter):
    format_name = "JPEG"
    mime_type = "image/jpeg"


class PngXmpAdapter(BaseImageMetadataAdapter):
    format_name = "PNG"
    mime_type = "image/png"


class ImageMetadataService:
    """JPEG/PNG AIGC 元数据写入、替换和写后校验服务。"""

    def __init__(
        self,
        exiftool: Optional[ExifToolClient] = None,
        identifier_registry: Optional[IdentifierRegistry] = None,
    ):
        self.exiftool = exiftool or ExifToolClient()
        self.identifier_registry = (
            identifier_registry or SQLiteIdentifierRegistry.from_environment()
        )
        self._adapters = {
            "JPEG": JpegXmpAdapter(),
            "PNG": PngXmpAdapter(),
        }

    @staticmethod
    def _sha256(file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _inspect_image(file_path: Path) -> _ImageSnapshot:
        try:
            with Image.open(file_path) as image:
                image.load()
                format_name = (image.format or "").upper()
                if format_name not in {"JPEG", "PNG"}:
                    raise ImageMetadataError(
                        "UNSUPPORTED_MEDIA_TYPE",
                        "当前图片适配器只支持 JPEG/JPG 和 PNG",
                    )
                pixel_digest = hashlib.sha256()
                pixel_digest.update(image.mode.encode("ascii", "replace"))
                pixel_digest.update(str(image.size).encode("ascii"))
                pixel_digest.update(image.tobytes())
                return _ImageSnapshot(
                    format_name=format_name,
                    mime_type="image/jpeg" if format_name == "JPEG" else "image/png",
                    size=image.size,
                    mode=image.mode,
                    pixel_sha256=pixel_digest.hexdigest(),
                )
        except ImageMetadataError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageMetadataError(
                "UNSUPPORTED_MEDIA_TYPE",
                "文件内容不是可正常解码的 JPEG 或 PNG 图片",
            ) from exc

    @staticmethod
    def _normalize_policy(policy: ExistingMetadataPolicy | str) -> ExistingMetadataPolicy:
        try:
            return ExistingMetadataPolicy(policy)
        except ValueError as exc:
            raise ImageMetadataError(
                "INVALID_EXISTING_METADATA_POLICY",
                "已有标识策略只能是 reject 或 replace",
            ) from exc

    @staticmethod
    def _read_records(file_path: Path):
        try:
            return read_aigc_records(str(file_path))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                "无法从图片回读 AIGC 元数据",
            ) from exc

    def _verify_metadata(self, file_path: Path, expected: dict) -> dict:
        records = self._read_records(file_path)
        if not records:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                "写入后未找到 AIGC 元数据",
            )
        if len(records) != 1:
            raise ImageMetadataError(
                "AIGC_DUPLICATE_RECORDS",
                f"写入后检出 {len(records)} 份 AIGC 元数据",
            )

        record = records[0]
        if record.parse_error:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                record.parse_error,
            )
        schema_errors = validate_aigc_document(record.document)
        if schema_errors:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                "回读的 AIGC 元数据未通过七字段校验",
                schema_errors,
            )
        if record.document != expected:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                "回读的 AIGC 元数据与计划写入对象不一致",
            )

        # 独立路径：由 ExifTool 直接读目标属性，再与公共 XMP 读取器交叉验证。
        try:
            external_values = self.exiftool.read_known_aigc_values(str(file_path))
            external_documents = [json.loads(value) for value in external_values]
        except (ExifToolExecutionError, json.JSONDecodeError) as exc:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                "ExifTool 独立回读 AIGC 元数据失败",
            ) from exc
        if external_documents != [expected]:
            raise ImageMetadataError(
                "METADATA_READBACK_FAILED",
                "ExifTool 独立回读结果与计划写入对象不一致",
            )
        return record.document

    def write(
        self,
        source_path: str,
        output_path: str,
        document: dict,
        policy: ExistingMetadataPolicy | str = ExistingMetadataPolicy.REJECT,
        initial_write: bool = True,
    ) -> ImageMetadataWriteResult:
        errors = validate_aigc_document(document)
        if errors:
            raise ImageMetadataError(
                "AIGC_SCHEMA_INVALID",
                "AIGC 元数据不符合 GB 45438—2025 附录 E 结构",
                errors,
            )
        character_errors = validate_aigc_business_rules(
            document,
            strict_characters=True,
            require_initial_relationships=False,
        )
        if character_errors:
            raise ImageMetadataError(
                "AIGC_CHARACTER_INVALID",
                "AIGC 字段含 GB 45438—2025 首期严格范围之外的字符",
                character_errors,
            )
        relationship_errors = validate_aigc_business_rules(
            document,
            strict_characters=False,
            require_initial_relationships=initial_write,
        )
        if relationship_errors:
            raise ImageMetadataError(
                "AIGC_INITIAL_RELATION_INVALID",
                "首次写入时生产字段与传播字段必须一致",
                relationship_errors,
            )
        policy_value = self._normalize_policy(policy)

        source = Path(source_path).resolve()
        output = Path(output_path).resolve()
        if source == output:
            raise ImageMetadataError(
                "OUTPUT_PATH_INVALID",
                "结果文件必须与原文件分开保存",
            )
        if output.exists():
            raise ImageMetadataError(
                "OUTPUT_FILE_EXISTS",
                "结果文件已存在，拒绝覆盖",
            )

        source_snapshot = self._inspect_image(source)
        adapter = self._adapters[source_snapshot.format_name]
        source_hash = self._sha256(source)
        existing_records = self._read_records(source)
        if existing_records and policy_value is ExistingMetadataPolicy.REJECT:
            raise ImageMetadataError(
                "AIGC_METADATA_EXISTS",
                f"原文件已存在 {len(existing_records)} 份 AIGC 元数据",
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}.",
            suffix=output.suffix or ".tmp",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        reservation = None

        try:
            shutil.copy2(source, temporary)
            try:
                reservation = self.identifier_registry.reserve(
                    document,
                    source_snapshot.pixel_sha256,
                )
            except DuplicateIdentifierError as exc:
                field = "ProduceID" if exc.role == "producer" else "PropagateID"
                raise ImageMetadataError(
                    "AIGC_IDENTIFIER_DUPLICATE",
                    f"{field} 已被同一提供者用于其他内容",
                ) from exc
            if existing_records and policy_value is ExistingMetadataPolicy.REPLACE:
                try:
                    self.exiftool.remove_known_aigc(str(temporary))
                except (ExifToolExecutionError, ExifToolNotFoundError) as exc:
                    raise ImageMetadataError(
                        "METADATA_WRITE_FAILED",
                        "旧 AIGC 元数据移除失败",
                    ) from exc
                if self._read_records(temporary):
                    raise ImageMetadataError(
                        "METADATA_WRITE_FAILED",
                        "仍有无法安全移除的旧 AIGC 元数据，已停止替换",
                    )

            serialized = serialize_aigc_document(document)
            try:
                adapter.write(self.exiftool, str(temporary), serialized)
            except (ExifToolExecutionError, ExifToolNotFoundError) as exc:
                raise ImageMetadataError(
                    "METADATA_WRITE_FAILED",
                    "ExifTool 未能写入 AIGC 元数据",
                ) from exc

            embedded = self._verify_metadata(temporary, document)
            output_snapshot = self._inspect_image(temporary)
            if (
                source_snapshot.format_name != output_snapshot.format_name
                or source_snapshot.size != output_snapshot.size
                or source_snapshot.mode != output_snapshot.mode
                or source_snapshot.pixel_sha256 != output_snapshot.pixel_sha256
            ):
                raise ImageMetadataError(
                    "MEDIA_INTEGRITY_FAILED",
                    "写入前后的图片格式、尺寸或像素内容不一致",
                )
            if self._sha256(source) != source_hash:
                raise ImageMetadataError(
                    "MEDIA_INTEGRITY_FAILED",
                    "处理期间原文件发生变化",
                )

            output_hash = self._sha256(temporary)
            os.replace(temporary, output)
            result = ImageMetadataWriteResult(
                output_path=str(output),
                detected_format=source_snapshot.format_name,
                mime_type=adapter.mime_type,
                carrier=adapter.carrier,
                adapter_version=adapter.adapter_version,
                input_sha256=source_hash,
                output_sha256=output_hash,
                embedded_metadata=embedded,
                validation=ImageValidationResult(
                    read_back_succeeded=True,
                    schema_valid=True,
                    single_aigc_record=True,
                    media_integrity_valid=True,
                ),
            )
            try:
                reservation.commit()
                reservation = None
            except Exception as exc:
                output.unlink(missing_ok=True)
                raise ImageMetadataError(
                    "IDENTIFIER_REGISTRY_FAILED",
                    "结果文件发布后未能完成编号登记，结果已撤回",
                ) from exc
            return result
        finally:
            if reservation is not None:
                reservation.rollback()
            if temporary.exists():
                temporary.unlink()
