import hashlib
import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, UnidentifiedImageError

from app.metadata.exiftool_client import ExifToolClient, ExifToolNotFoundError
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.image_adapter import ImageMetadataError, ImageMetadataService
from app.metadata.job_store import (
    CreateJobResult,
    IdempotencyConflictError,
    SQLiteMetadataJobStore,
)
from app.metadata.xmp_reader import read_aigc_records
from app.schemas.metadata_jobs import MetadataLabelRequest
from app.schemas.validation import (
    validate_aigc_business_rules,
    validate_aigc_document,
)


logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


class MetadataJobRequestError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: Optional[list[dict[str, str]]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or []


@dataclass(frozen=True)
class MetadataJobConfig:
    storage_root: Path
    database_path: Path
    identifier_database_path: Path
    max_upload_bytes: int = 25 * 1024 * 1024
    retention_hours: int = 168
    worker_count: int = 2
    exiftool_timeout_seconds: int = 30

    @classmethod
    def from_environment(cls) -> "MetadataJobConfig":
        backend_root = Path(__file__).resolve().parents[2]
        data_root = backend_root / "data"
        return cls(
            storage_root=Path(
                os.getenv("AIGC_JOB_STORAGE_DIR", data_root / "metadata_label_files")
            ).expanduser(),
            database_path=Path(
                os.getenv("AIGC_JOB_DB_PATH", data_root / "metadata_label_jobs.sqlite3")
            ).expanduser(),
            identifier_database_path=Path(
                os.getenv(
                    "AIGC_ID_REGISTRY_PATH", data_root / "aigc_identifiers.sqlite3"
                )
            ).expanduser(),
            max_upload_bytes=int(
                os.getenv("AIGC_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
            ),
            retention_hours=int(os.getenv("AIGC_JOB_RETENTION_HOURS", "168")),
            worker_count=int(os.getenv("AIGC_JOB_WORKERS", "2")),
            exiftool_timeout_seconds=int(
                os.getenv("AIGC_EXIFTOOL_TIMEOUT_SECONDS", "30")
            ),
        )


@dataclass(frozen=True)
class _InspectedUpload:
    format_name: str
    mime_type: str
    suffix: str


class MetadataLabelJobService:
    """图片元数据标注任务的接收、执行、查询与清理服务。"""

    def __init__(
        self,
        config: Optional[MetadataJobConfig] = None,
        *,
        image_service: Optional[ImageMetadataService] = None,
    ):
        self.config = config or MetadataJobConfig.from_environment()
        self.storage_root = self.config.storage_root.resolve()
        self.input_dir = self.storage_root / "originals"
        self.output_dir = self.storage_root / "outputs"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteMetadataJobStore(str(self.config.database_path))
        self._image_service = image_service
        self._image_service_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, self.config.worker_count),
            thread_name_prefix="aigc-metadata-job",
        )
        self._purge_expired()
        for job_id in self.store.recover_incomplete(iso_utc(utc_now())):
            self._schedule(job_id)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _get_image_service(self) -> ImageMetadataService:
        if self._image_service is None:
            with self._image_service_lock:
                if self._image_service is None:
                    client = ExifToolClient(
                        timeout_seconds=self.config.exiftool_timeout_seconds
                    )
                    registry = SQLiteIdentifierRegistry(
                        str(self.config.identifier_database_path)
                    )
                    self._image_service = ImageMetadataService(client, registry)
        return self._image_service

    @staticmethod
    def _inspect_upload(data: bytes) -> _InspectedUpload:
        try:
            with Image.open(BytesIO(data)) as image:
                image.load()
                format_name = (image.format or "").upper()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MetadataJobRequestError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "当前图片标注接口只支持可正常解码的 JPEG/JPG 和 PNG。",
            ) from exc
        if format_name == "JPEG":
            return _InspectedUpload("JPEG", "image/jpeg", ".jpg")
        if format_name == "PNG":
            return _InspectedUpload("PNG", "image/png", ".png")
        raise MetadataJobRequestError(
            415,
            "UNSUPPORTED_MEDIA_TYPE",
            "当前图片标注接口只支持 JPEG/JPG 和 PNG。",
        )

    @staticmethod
    def _field_errors(errors: list[str]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for error in errors:
            field, separator, reason = error.partition(":")
            result.append(
                {
                    "field": field.strip(),
                    "reason": reason.strip() if separator else error,
                }
            )
        return result

    @classmethod
    def _validate_request(cls, request: MetadataLabelRequest) -> dict:
        document = request.aigc_document()
        schema_errors = validate_aigc_document(document)
        if schema_errors:
            raise MetadataJobRequestError(
                422,
                "AIGC_SCHEMA_INVALID",
                "AIGC 元数据不符合 GB 45438—2025 附录 E 结构。",
                cls._field_errors(schema_errors),
            )
        character_errors = validate_aigc_business_rules(
            document,
            strict_characters=True,
            require_initial_relationships=False,
        )
        if character_errors:
            raise MetadataJobRequestError(
                422,
                "AIGC_CHARACTER_INVALID",
                "AIGC 字段含首期严格字符范围之外的字符。",
                cls._field_errors(character_errors),
            )
        relationship_errors = validate_aigc_business_rules(
            document,
            strict_characters=False,
            require_initial_relationships=True,
        )
        if relationship_errors:
            raise MetadataJobRequestError(
                422,
                "AIGC_INITIAL_RELATION_INVALID",
                "首次写入时传播字段必须与生产字段一致。",
                cls._field_errors(relationship_errors),
            )
        return document

    @staticmethod
    def _safe_original_name(filename: Optional[str]) -> str:
        basename = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", basename).strip(" .")
        return (cleaned or "upload")[:160]

    @classmethod
    def _output_name(cls, original_name: str, suffix: str) -> str:
        safe = cls._safe_original_name(original_name)
        stem = Path(safe).stem[:120] or "image"
        return f"{stem}_labeled{suffix}"

    @staticmethod
    def _request_fingerprint(
        input_sha256: str, request_payload: dict
    ) -> str:
        digest = hashlib.sha256()
        digest.update(input_sha256.encode("ascii"))
        digest.update(
            json.dumps(
                request_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return digest.hexdigest()

    def create_job(
        self,
        *,
        filename: Optional[str],
        data: bytes,
        request: MetadataLabelRequest,
        idempotency_key: Optional[str] = None,
    ) -> CreateJobResult:
        self._purge_expired()
        if not data:
            raise MetadataJobRequestError(
                400, "INVALID_MULTIPART", "上传文件不能为空。"
            )
        if len(data) > self.config.max_upload_bytes:
            raise MetadataJobRequestError(
                413,
                "FILE_TOO_LARGE",
                f"文件超过当前 {self.config.max_upload_bytes} 字节上限。",
            )
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key or len(idempotency_key) > 200:
                raise MetadataJobRequestError(
                    400,
                    "INVALID_MULTIPART",
                    "Idempotency-Key 必须是 1 至 200 个字符。",
                )

        document = self._validate_request(request)
        inspection = self._inspect_upload(data)
        if request.modality != "image":
            raise MetadataJobRequestError(
                422,
                "MODALITY_MISMATCH",
                "请求声明的模态与实际 JPEG/PNG 图片不一致。",
                [{"field": "modality", "reason": "JPEG/PNG 必须声明为 image。"}],
            )

        now = iso_utc(utc_now())
        job_id = f"job_{uuid.uuid4().hex}"
        request_id = f"req_{uuid.uuid4().hex}"
        physical_id = uuid.uuid4().hex
        input_path = self.input_dir / f"{physical_id}{inspection.suffix}"
        output_path = self.output_dir / f"{uuid.uuid4().hex}{inspection.suffix}"
        original_name = self._safe_original_name(filename)
        input_sha256 = hashlib.sha256(data).hexdigest()
        request_payload = request.model_dump()
        fingerprint = self._request_fingerprint(input_sha256, request_payload)

        input_path.write_bytes(data)
        try:
            records = read_aigc_records(str(input_path))
            if records and request.existing_metadata_policy == "reject":
                raise MetadataJobRequestError(
                    409,
                    "AIGC_METADATA_EXISTS",
                    f"文件已存在 {len(records)} 份可识别的 AIGC 元数据；如需替换必须明确选择 replace。",
                )
            values = {
                "job_id": job_id,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
                "request_fingerprint": fingerprint,
                "status": "queued",
                "stage": "queued",
                "progress": None,
                "created_at": now,
                "updated_at": now,
                "original_file_name": original_name,
                "detected_mime_type": inspection.mime_type,
                "input_size_bytes": len(data),
                "input_sha256": input_sha256,
                "input_path": str(input_path),
                "output_path": str(output_path),
                "output_file_name": self._output_name(original_name, inspection.suffix),
                "request_json": request_payload,
            }
            created = self.store.create(values)
        except IdempotencyConflictError as exc:
            input_path.unlink(missing_ok=True)
            raise MetadataJobRequestError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "同一 Idempotency-Key 已用于不同文件或参数。",
            ) from exc
        except Exception:
            input_path.unlink(missing_ok=True)
            raise

        if not created.created:
            input_path.unlink(missing_ok=True)
        else:
            self._schedule(job_id)
        return created

    def _schedule(self, job_id: str) -> None:
        self._executor.submit(self._run_job, job_id)

    def _run_job(self, job_id: str) -> None:
        now = iso_utc(utc_now())
        if not self.store.claim(job_id, now):
            return
        record = self.store.get(job_id)
        if record is None:
            return
        try:
            request = MetadataLabelRequest.model_validate(record["request"])
            self.store.update_stage(job_id, "writing_metadata", iso_utc(utc_now()))
            result = self._get_image_service().write(
                record["input_path"],
                record["output_path"],
                request.aigc_document(),
                policy=request.existing_metadata_policy,
                initial_write=True,
            )
            self.store.update_stage(job_id, "verifying_metadata", iso_utc(utc_now()))
            validation = {
                "read_back_succeeded": result.validation.read_back_succeeded,
                "schema_valid": result.validation.schema_valid,
                "single_aigc_record": result.validation.single_aigc_record,
                "media_integrity_valid": result.validation.media_integrity_valid,
            }
            completed = utc_now()
            expires = completed + timedelta(hours=self.config.retention_hours)
            output_path = Path(result.output_path)
            self.store.succeed(
                job_id,
                timestamp=iso_utc(completed),
                expires_at=iso_utc(expires),
                output_mime_type=result.mime_type,
                output_size_bytes=output_path.stat().st_size,
                output_sha256=result.output_sha256,
                carrier=result.carrier,
                adapter_version=result.adapter_version,
                embedded_metadata=result.embedded_metadata,
                validation=validation,
            )
        except ImageMetadataError as exc:
            self._mark_failed(job_id, exc.code, str(exc), exc.details)
        except ExifToolNotFoundError:
            self._mark_failed(
                job_id,
                "METADATA_WRITE_FAILED",
                "服务器未配置可用的 ExifTool，无法写入图片元数据。",
            )
        except Exception:
            logger.exception("metadata label job failed: job_id=%s", job_id)
            self._mark_failed(
                job_id,
                "INTERNAL_ERROR",
                "任务处理发生未分类错误，请根据 request_id 排查服务器日志。",
            )

    def _mark_failed(
        self,
        job_id: str,
        code: str,
        message: str,
        details: Optional[list[str]] = None,
    ) -> None:
        completed = utc_now()
        self.store.fail(
            job_id,
            timestamp=iso_utc(completed),
            expires_at=iso_utc(
                completed + timedelta(hours=self.config.retention_hours)
            ),
            code=code,
            message=message,
            retryable=False,
            details=details,
        )

    def accepted_payload(self, record: dict) -> dict:
        return {
            "request_id": record["request_id"],
            "job_id": record["job_id"],
            "status": record["status"],
            "stage": record["stage"],
            "progress": record["progress"],
            "created_at": record["created_at"],
            "links": {
                "self": f"/api/v1/metadata-label-jobs/{record['job_id']}",
                "output": (
                    f"/api/v1/metadata-label-jobs/{record['job_id']}/output"
                    if record["status"] == "succeeded"
                    else None
                ),
            },
        }

    def get_job(self, job_id: str) -> Optional[dict]:
        self._purge_expired()
        record = self.store.get(job_id)
        return self._public_payload(record) if record else None

    @staticmethod
    def _public_payload(record: dict) -> dict:
        job_id = record["job_id"]
        succeeded = record["status"] == "succeeded"
        failed = record["status"] == "failed"
        output_url = f"/api/v1/metadata-label-jobs/{job_id}/output"
        output = None
        if succeeded:
            output = {
                "file_name": record["output_file_name"],
                "mime_type": record["output_mime_type"],
                "size_bytes": record["output_size_bytes"],
                "sha256": record["output_sha256"],
                "carrier": record["carrier"],
                "download_url": output_url,
                "expires_at": record["expires_at"],
            }
        error = None
        if failed:
            error = {
                "code": record["error_code"],
                "message": record["error_message"],
                "retryable": record["error_retryable"],
            }
        return {
            "request_id": record["request_id"],
            "job_id": job_id,
            "status": record["status"],
            "stage": record["stage"],
            "progress": record["progress"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "input": {
                "original_file_name": record["original_file_name"],
                "detected_mime_type": record["detected_mime_type"],
                "size_bytes": record["input_size_bytes"],
                "sha256": record["input_sha256"],
            },
            "output": output,
            "embedded_metadata": record["embedded_metadata"] if succeeded else None,
            "validation": record["validation"] if succeeded else None,
            "error": error,
            "links": {
                "self": f"/api/v1/metadata-label-jobs/{job_id}",
                "output": output_url if succeeded else None,
            },
        }

    def download_record(self, job_id: str) -> Optional[dict]:
        self._purge_expired()
        return self.store.get(job_id)

    def _purge_expired(self) -> None:
        for record in self.store.expired(iso_utc(utc_now())):
            for key in ("input_path", "output_path"):
                candidate = Path(record[key]).resolve()
                try:
                    candidate.relative_to(self.storage_root)
                except ValueError:
                    continue
                if candidate.is_file():
                    candidate.unlink(missing_ok=True)
            self.store.delete(record["job_id"])
