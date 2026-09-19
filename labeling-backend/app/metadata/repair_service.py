import hashlib
import json
import logging
import os
import re
import threading
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from PIL import Image, UnidentifiedImageError

from app.metadata.compliance import (
    ComplianceConclusion,
    ComplianceInspectionError,
    MetadataComplianceInspector,
)
from app.metadata.exiftool_client import ExifToolClient, ExifToolNotFoundError
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.image_adapter import ImageMetadataError, ImageMetadataService
from app.metadata.timeutil import iso_utc, utc_now
from app.metadata.repair_planner import (
    RepairPlanDraft,
    RepairPlanningError,
    RepairPlanner,
    TrustedRepairInput,
    WriteContext,
)
from app.metadata.repair_store import (
    RepairPlanConflictError,
    SQLiteMetadataRepairStore,
)


logger = logging.getLogger(__name__)


class MetadataRepairRequestError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[list[str]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class MetadataRepairConfig:
    storage_root: Path
    database_path: Path
    audit_root: Path
    identifier_database_path: Path
    max_upload_bytes: int = 25 * 1024 * 1024
    max_image_width: int = 16384
    max_image_height: int = 16384
    max_image_pixels: int = 40_000_000
    plan_retention_hours: int = 24
    file_retention_days: int = 180
    worker_count: int = 1
    exiftool_timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if self.worker_count != 1:
            raise ValueError("首期 SQLite 修复任务要求 AIGC_REPAIR_WORKERS=1")

    @classmethod
    def from_environment(cls) -> "MetadataRepairConfig":
        backend_root = Path(__file__).resolve().parents[2]
        data_root = backend_root / "data"
        return cls(
            storage_root=Path(
                os.getenv("AIGC_REPAIR_STORAGE_DIR", data_root / "metadata_repair_files")
            ).expanduser(),
            database_path=Path(
                os.getenv("AIGC_REPAIR_DB_PATH", data_root / "metadata_repairs.sqlite3")
            ).expanduser(),
            audit_root=Path(
                os.getenv("AIGC_REPAIR_AUDIT_DIR", data_root / "metadata_repair_audit")
            ).expanduser(),
            identifier_database_path=Path(
                os.getenv(
                    "AIGC_ID_REGISTRY_PATH", data_root / "aigc_identifiers.sqlite3"
                )
            ).expanduser(),
            max_upload_bytes=int(
                os.getenv("AIGC_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
            ),
            max_image_width=int(os.getenv("AIGC_MAX_IMAGE_WIDTH", "16384")),
            max_image_height=int(os.getenv("AIGC_MAX_IMAGE_HEIGHT", "16384")),
            max_image_pixels=int(os.getenv("AIGC_MAX_IMAGE_PIXELS", "40000000")),
            plan_retention_hours=int(os.getenv("AIGC_REPAIR_PLAN_HOURS", "24")),
            file_retention_days=int(os.getenv("AIGC_REPAIR_FILE_DAYS", "180")),
            worker_count=int(os.getenv("AIGC_REPAIR_WORKERS", "1")),
            exiftool_timeout_seconds=int(
                os.getenv("AIGC_EXIFTOOL_TIMEOUT_SECONDS", "30")
            ),
        )


@dataclass(frozen=True)
class _UploadInfo:
    format_name: str
    mime_type: str
    suffix: str
    pixel_sha256: str


class MetadataRepairService:
    """修复计划、显式确认、异步执行和长期审计的协调服务。"""

    def __init__(
        self,
        config: Optional[MetadataRepairConfig] = None,
        *,
        image_service: Optional[ImageMetadataService] = None,
        inspector: Optional[MetadataComplianceInspector] = None,
    ):
        self.config = config or MetadataRepairConfig.from_environment()
        self.storage_root = self.config.storage_root.resolve()
        self.input_dir = self.storage_root / "originals"
        self.output_dir = self.storage_root / "outputs"
        self.audit_root = self.config.audit_root.resolve()
        for directory in (self.input_dir, self.output_dir, self.audit_root):
            directory.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteMetadataRepairStore(str(self.config.database_path))
        self._registry = SQLiteIdentifierRegistry(
            str(self.config.identifier_database_path)
        )
        self._image_service = image_service
        self._inspector = inspector
        self._service_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=self.config.worker_count,
            thread_name_prefix="aigc-metadata-repair",
        )
        self._purge_expired()
        for job_id in self.store.recover_incomplete(iso_utc(utc_now())):
            self._schedule(job_id)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _get_tools(self) -> tuple[ImageMetadataService, MetadataComplianceInspector]:
        with self._service_lock:
            if self._image_service is None or self._inspector is None:
                client = ExifToolClient(
                    timeout_seconds=self.config.exiftool_timeout_seconds
                )
                if self._image_service is None:
                    self._image_service = ImageMetadataService(
                        client, self._registry,
                        max_image_width=self.config.max_image_width,
                        max_image_height=self.config.max_image_height,
                        max_image_pixels=self.config.max_image_pixels,
                    )
                if self._inspector is None:
                    self._inspector = MetadataComplianceInspector(
                        exiftool=client, identifier_registry=self._registry
                    )
        return self._image_service, self._inspector

    def _inspect_upload(self, data: bytes) -> _UploadInfo:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    width, height = image.size
                    if (
                        width > self.config.max_image_width
                        or height > self.config.max_image_height
                        or width * height > self.config.max_image_pixels
                    ):
                        raise MetadataRepairRequestError(
                            413,
                            "IMAGE_DIMENSIONS_EXCEEDED",
                            "图片尺寸或总像素数超过项目安全上限",
                        )
                    image.load()
                    format_name = (image.format or "").upper()
                    digest = hashlib.sha256()
                    digest.update(image.mode.encode("ascii", "replace"))
                    digest.update(str(image.size).encode("ascii"))
                    digest.update(image.tobytes())
        except MetadataRepairRequestError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise MetadataRepairRequestError(
                413, "IMAGE_DIMENSIONS_EXCEEDED", "图片触发了解码安全限制"
            ) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MetadataRepairRequestError(
                415, "UNSUPPORTED_MEDIA_TYPE", "只支持可正常解码的 JPEG/JPG 和 PNG"
            ) from exc
        if format_name == "JPEG":
            return _UploadInfo("JPEG", "image/jpeg", ".jpg", digest.hexdigest())
        if format_name == "PNG":
            return _UploadInfo("PNG", "image/png", ".png", digest.hexdigest())
        raise MetadataRepairRequestError(
            415, "UNSUPPORTED_MEDIA_TYPE", "只支持 JPEG/JPG 和 PNG"
        )

    @staticmethod
    def _safe_name(filename: Optional[str]) -> str:
        basename = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", basename).strip(" .")
        return (cleaned or "upload")[:160]

    @classmethod
    def _output_name(cls, original_name: str, suffix: str) -> str:
        stem = Path(cls._safe_name(original_name)).stem[:120] or "image"
        return f"{stem}_repaired{suffix}"

    @staticmethod
    def _plan_hash(input_sha256: str, inspection: dict, draft: dict) -> str:
        canonical = json.dumps(
            {
                "input_sha256": input_sha256,
                "inspection": inspection,
                "draft": draft,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _write_audit_artifact(
        self,
        *,
        plan_id: str,
        job_id: Optional[str],
        artifact_type: str,
        content: bytes,
        timestamp: str,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        directory = self.audit_root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.json"
        if not path.exists():
            temporary = directory / f".{digest}.{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(content)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        record = {
            "artifact_id": f"artifact_{uuid.uuid4().hex}",
            "plan_id": plan_id,
            "job_id": job_id,
            "artifact_type": artifact_type,
            "content_sha256": digest,
            "storage_path": str(path),
            "size_bytes": len(content),
            "created_at": timestamp,
        }
        self.store.add_artifact(record)
        return {
            "artifact_type": artifact_type,
            "sha256": digest,
            "size_bytes": len(content),
        }

    def create_plan(
        self,
        *,
        filename: Optional[str],
        data: bytes,
        trusted_input: Optional[TrustedRepairInput] = None,
    ) -> dict[str, Any]:
        self._purge_expired()
        if not data:
            raise MetadataRepairRequestError(400, "EMPTY_FILE", "上传文件不能为空")
        if len(data) > self.config.max_upload_bytes:
            raise MetadataRepairRequestError(
                413,
                "FILE_TOO_LARGE",
                f"文件超过当前 {self.config.max_upload_bytes} 字节上限",
            )
        upload = self._inspect_upload(data)
        now_dt = utc_now()
        now = iso_utc(now_dt)
        plan_id = f"plan_{uuid.uuid4().hex}"
        request_id = f"req_{uuid.uuid4().hex}"
        input_path = self.input_dir / f"{uuid.uuid4().hex}{upload.suffix}"
        input_path.write_bytes(data)
        try:
            try:
                _, inspector = self._get_tools()
            except ExifToolNotFoundError as exc:
                raise MetadataRepairRequestError(
                    503,
                    "METADATA_TOOL_UNAVAILABLE",
                    "ExifTool 当前不可用，不能可靠生成修复计划",
                ) from exc
            try:
                inspection = inspector.inspect(str(input_path))
            except ComplianceInspectionError as exc:
                raise MetadataRepairRequestError(
                    422, exc.code, str(exc)
                ) from exc
            try:
                draft = RepairPlanner().plan(inspection, trusted_input)
            except RepairPlanningError as exc:
                raise MetadataRepairRequestError(
                    422, exc.code, str(exc), exc.details
                ) from exc
            inspection_payload = inspection.model_dump(mode="json")
            draft_payload = draft.model_dump(mode="json")
            input_sha256 = hashlib.sha256(data).hexdigest()
            plan_hash = self._plan_hash(
                input_sha256, inspection_payload, draft_payload
            )
            record = self.store.create_plan(
                {
                    "plan_id": plan_id,
                    "request_id": request_id,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                    "expires_at": iso_utc(
                        now_dt + timedelta(hours=self.config.plan_retention_hours)
                    ),
                    "input_expires_at": iso_utc(
                        now_dt + timedelta(days=self.config.file_retention_days)
                    ),
                    "original_file_name": self._safe_name(filename),
                    "detected_mime_type": upload.mime_type,
                    "input_size_bytes": len(data),
                    "input_sha256": input_sha256,
                    "pixel_sha256": upload.pixel_sha256,
                    "input_path": str(input_path),
                    "inspection_json": inspection_payload,
                    "draft_json": draft_payload,
                    "trusted_input_json": (
                        trusted_input.model_dump(mode="json")
                        if trusted_input is not None
                        else None
                    ),
                    "plan_hash": plan_hash,
                }
            )
            artifact_payload = {
                "inspection": inspection_payload,
                "raw_aigc_records": [
                    {
                        "property_name": item.property_name,
                        "packet_location": item.packet_location,
                        "raw_value": item.raw_value,
                        "parse_error": item.parse_error,
                    }
                    for item in inspection.raw_records
                ],
                "draft": draft_payload,
            }
            artifact = self._write_audit_artifact(
                plan_id=plan_id,
                job_id=None,
                artifact_type="inspection_and_original_labels",
                content=json.dumps(
                    artifact_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                timestamp=now,
            )
            self.store.append_event(
                event_id=f"event_{uuid.uuid4().hex}",
                plan_id=plan_id,
                job_id=None,
                event_type="repair_plan_created",
                timestamp=now,
                payload={
                    "input_sha256": input_sha256,
                    "pixel_sha256": upload.pixel_sha256,
                    "plan_hash": plan_hash,
                    "conclusion": inspection.conclusion.value,
                    "repairability": draft.repairability.value,
                    "audit_artifact": artifact,
                },
            )
            return self.plan_payload(record)
        except Exception:
            if self.store.get_plan(plan_id) is None:
                input_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def plan_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": record["request_id"],
            "plan_id": record["plan_id"],
            "job_id": record.get("job_id"),
            "status": record["status"],
            "created_at": record["created_at"],
            "expires_at": record["expires_at"],
            "input": {
                "original_file_name": record["original_file_name"],
                "detected_mime_type": record["detected_mime_type"],
                "size_bytes": record["input_size_bytes"],
                "sha256": record["input_sha256"],
                "pixel_sha256": record["pixel_sha256"],
                "available": record.get("input_purged_at") is None,
                "expires_at": record.get("input_expires_at"),
            },
            "inspection": record["inspection"],
            "repair_plan": record["draft"],
            "plan_hash": record["plan_hash"],
            "confirmation": {
                "required": bool(record["draft"].get("executable")),
                "identity_verified": bool(record.get("identity_verified", False)),
                "method": record.get("confirmation_method"),
                "operator_label": record.get("operator_label"),
            },
            "links": {
                "self": f"/api/v1/metadata-repair-plans/{record['plan_id']}",
                "create_job": "/api/v1/metadata-repair-jobs",
                "job": (
                    f"/api/v1/metadata-repair-jobs/{record['job_id']}"
                    if record.get("job_id") else None
                ),
            },
        }

    def get_plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        self._purge_expired()
        record = self.store.get_plan(plan_id)
        return self.plan_payload(record) if record else None

    def confirm_plan(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        confirmed: bool,
        operator_label: str,
    ) -> dict[str, Any]:
        self._purge_expired()
        if confirmed is not True:
            raise MetadataRepairRequestError(
                422, "REPAIR_CONFIRMATION_REQUIRED", "必须明确确认修复计划"
            )
        operator_label = operator_label.strip()
        if not operator_label or len(operator_label) > 200:
            raise MetadataRepairRequestError(
                422,
                "OPERATOR_LABEL_INVALID",
                "operator_label 必须是 1 至 200 个字符",
            )
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise MetadataRepairRequestError(404, "REPAIR_PLAN_NOT_FOUND", "修复计划不存在")
        input_path = Path(plan["input_path"])
        if not input_path.is_file():
            raise MetadataRepairRequestError(
                409, "REPAIR_INPUT_MISSING", "修复计划引用的原文件已不可用"
            )
        if hashlib.sha256(input_path.read_bytes()).hexdigest() != plan["input_sha256"]:
            raise MetadataRepairRequestError(
                409, "REPAIR_INPUT_CHANGED", "原文件已变化，必须重新生成修复计划"
            )
        suffix = ".jpg" if plan["detected_mime_type"] == "image/jpeg" else ".png"
        output_path = self.output_dir / f"{uuid.uuid4().hex}{suffix}"
        job_id = f"repair_job_{uuid.uuid4().hex}"
        request_id = f"req_{uuid.uuid4().hex}"
        timestamp = iso_utc(utc_now())
        confirmation_payload = {
            "plan_hash": plan_hash,
            "confirmation_method": "manual_api",
            "operator_label": operator_label,
            "identity_verified": False,
        }
        try:
            record = self.store.confirm_and_create_job(
                plan_id=plan_id,
                expected_plan_hash=plan_hash,
                job_id=job_id,
                request_id=request_id,
                timestamp=timestamp,
                operator_label=operator_label,
                output_path=str(output_path),
                output_file_name=self._output_name(plan["original_file_name"], suffix),
                confirmation_event_id=f"event_{uuid.uuid4().hex}",
                confirmation_payload=confirmation_payload,
            )
        except RepairPlanConflictError as exc:
            raise MetadataRepairRequestError(
                409, "REPAIR_PLAN_CONFLICT", str(exc)
            ) from exc
        self._schedule(job_id)
        return self.accepted_payload(record)

    def _schedule(self, job_id: str) -> None:
        self._executor.submit(self._run_job, job_id)

    def _run_job(self, job_id: str) -> None:
        timestamp = iso_utc(utc_now())
        if not self.store.claim_job(job_id, timestamp):
            return
        job = self.store.get_job(job_id)
        if job is None:
            return
        plan = self.store.get_plan(job["plan_id"])
        if plan is None:
            self.store.fail_job(
                job_id,
                timestamp=timestamp,
                files_expires_at=iso_utc(
                    utc_now() + timedelta(days=self.config.file_retention_days)
                ),
                code="REPAIR_PLAN_NOT_FOUND",
                message="任务引用的修复计划不存在",
                retryable=False,
            )
            return
        input_path = Path(plan["input_path"])
        output_path = Path(job["output_path"])
        draft = RepairPlanDraft.model_validate(plan["draft"])
        document = draft.proposed_document
        if document is None:
            self.store.fail_job(
                job_id,
                timestamp=timestamp,
                files_expires_at=iso_utc(
                    utc_now() + timedelta(days=self.config.file_retention_days)
                ),
                code="REPAIR_PLAN_NOT_EXECUTABLE",
                message="修复计划没有可执行的七字段结果",
                retryable=False,
            )
            return
        self.store.append_event(
            event_id=f"event_{uuid.uuid4().hex}",
            plan_id=plan["plan_id"],
            job_id=job_id,
            event_type="repair_started",
            timestamp=timestamp,
            payload={"input_sha256": plan["input_sha256"]},
        )
        try:
            image_service, inspector = self._get_tools()

            def update_stage(stage: str) -> None:
                self.store.update_stage(job_id, stage, iso_utc(utc_now()))

            if output_path.is_file():
                result = image_service.recover_published(
                    str(input_path),
                    str(output_path),
                    document,
                    expected_input_sha256=plan["input_sha256"],
                )
            else:
                result = image_service.write(
                    str(input_path),
                    str(output_path),
                    document,
                    policy="replace",
                    initial_write=(
                        draft.write_context is WriteContext.INITIAL_GENERATION
                    ),
                    stage_callback=update_stage,
                )
            post = inspector.inspect(
                str(output_path),
                initial_write=(
                    True if draft.write_context is WriteContext.INITIAL_GENERATION else None
                ),
            )
            if post.conclusion is not ComplianceConclusion.COMPLIANT:
                raise ImageMetadataError(
                    "REPAIR_POSTCHECK_FAILED",
                    "修复结果未通过独立国标合规检查",
                    post.reason_codes,
                )
            if post.aigc_metadata != document["AIGC"]:
                raise ImageMetadataError(
                    "REPAIR_POSTCHECK_FAILED",
                    "修复结果七字段与确认计划不一致",
                )
            validation = {
                "read_back_succeeded": result.validation.read_back_succeeded,
                "schema_valid": result.validation.schema_valid,
                "single_aigc_record": result.validation.single_aigc_record,
                "media_integrity_valid": result.validation.media_integrity_valid,
                "post_repair_conclusion": post.conclusion.value,
                "project_policy_accepted": post.project_policy.accepted,
                "pixel_sha256_unchanged": post.pixel_sha256 == plan["pixel_sha256"],
            }
            if not validation["pixel_sha256_unchanged"]:
                raise ImageMetadataError(
                    "MEDIA_INTEGRITY_FAILED", "修复前后图片像素指纹不一致"
                )
            completed_dt = utc_now()
            completed = iso_utc(completed_dt)
            files_expires_at = iso_utc(
                completed_dt + timedelta(days=self.config.file_retention_days)
            )
            repaired_artifact = self._write_audit_artifact(
                plan_id=plan["plan_id"],
                job_id=job_id,
                artifact_type="repaired_metadata_and_validation",
                content=json.dumps(
                    {
                        "proposed_document": document,
                        "post_repair_inspection": post.model_dump(mode="json"),
                        "validation": validation,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                timestamp=completed,
            )
            self.store.succeed_job_with_event(
                job_id,
                plan_id=plan["plan_id"],
                event_id=f"event_{uuid.uuid4().hex}",
                timestamp=completed,
                files_expires_at=files_expires_at,
                output_mime_type=result.mime_type,
                output_size_bytes=output_path.stat().st_size,
                output_sha256=result.output_sha256,
                validation=validation,
                event_payload={
                    "output_sha256": result.output_sha256,
                    "files_expires_at": files_expires_at,
                    "validation": validation,
                    "audit_artifact": repaired_artifact,
                },
            )
        except (ImageMetadataError, ComplianceInspectionError) as exc:
            logger.warning(
                "metadata repair failed: job_id=%s code=%s",
                job_id,
                getattr(exc, "code", "REPAIR_FAILED"),
            )
            output_path.unlink(missing_ok=True)
            completed_dt = utc_now()
            completed = iso_utc(completed_dt)
            code = getattr(exc, "code", "REPAIR_FAILED")
            details = getattr(exc, "details", [])
            self.store.fail_job(
                job_id,
                timestamp=completed,
                files_expires_at=iso_utc(
                    completed_dt + timedelta(days=self.config.file_retention_days)
                ),
                code=code,
                message=str(exc),
                retryable=False,
                details=details,
            )
            self.store.append_event(
                event_id=f"event_{uuid.uuid4().hex}",
                plan_id=plan["plan_id"],
                job_id=job_id,
                event_type="repair_failed",
                timestamp=completed,
                payload={"code": code, "message": str(exc), "details": details},
            )
        except Exception as exc:
            logger.exception("metadata repair internal failure: job_id=%s", job_id)
            output_path.unlink(missing_ok=True)
            completed_dt = utc_now()
            completed = iso_utc(completed_dt)
            self.store.fail_job(
                job_id,
                timestamp=completed,
                files_expires_at=iso_utc(
                    completed_dt + timedelta(days=self.config.file_retention_days)
                ),
                code="REPAIR_INTERNAL_ERROR",
                message="修复任务内部错误",
                retryable=False,
            )
            self.store.append_event(
                event_id=f"event_{uuid.uuid4().hex}",
                plan_id=plan["plan_id"],
                job_id=job_id,
                event_type="repair_failed",
                timestamp=completed,
                payload={"code": "REPAIR_INTERNAL_ERROR", "exception": type(exc).__name__},
            )

    @staticmethod
    def accepted_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": record["request_id"],
            "job_id": record["job_id"],
            "plan_id": record["plan_id"],
            "status": record["status"],
            "stage": record["stage"],
            "progress": record["progress"],
            "created_at": record["created_at"],
            "links": {
                "self": f"/api/v1/metadata-repair-jobs/{record['job_id']}",
                "output": f"/api/v1/metadata-repair-jobs/{record['job_id']}/output",
            },
        }

    def job_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        plan = self.store.get_plan(record["plan_id"])
        succeeded = record["status"] == "succeeded"
        failed = record["status"] == "failed"
        files_available = succeeded and record["stage"] != "files_purged"
        output = None
        if succeeded:
            output = {
                "file_name": record["output_file_name"],
                "mime_type": record["output_mime_type"],
                "size_bytes": record["output_size_bytes"],
                "sha256": record["output_sha256"],
                "available": files_available,
                "download_url": (
                    f"/api/v1/metadata-repair-jobs/{record['job_id']}/output"
                    if files_available else None
                ),
                "expires_at": record["files_expires_at"],
            }
        error = None
        if failed:
            error = {
                "code": record["error_code"],
                "message": record["error_message"],
                "retryable": record["error_retryable"],
                "details": record["error_details"],
            }
        return {
            "request_id": record["request_id"],
            "job_id": record["job_id"],
            "plan_id": record["plan_id"],
            "status": record["status"],
            "stage": record["stage"],
            "progress": record["progress"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "input": {
                "original_file_name": plan["original_file_name"] if plan else None,
                "sha256": plan["input_sha256"] if plan else None,
            },
            "confirmation": {
                "method": plan.get("confirmation_method") if plan else None,
                "operator_label": plan.get("operator_label") if plan else None,
                "identity_verified": plan.get("identity_verified", False) if plan else False,
            },
            "output": output,
            "validation": record["validation"] if succeeded else None,
            "error": error,
            "links": {
                "self": f"/api/v1/metadata-repair-jobs/{record['job_id']}",
                "output": (
                    f"/api/v1/metadata-repair-jobs/{record['job_id']}/output"
                    if files_available else None
                ),
            },
        }

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        self._purge_expired()
        record = self.store.get_job(job_id)
        return self.job_payload(record) if record else None

    def download_record(self, job_id: str) -> Optional[dict[str, Any]]:
        self._purge_expired()
        return self.store.get_job(job_id)

    def _purge_expired(self) -> None:
        timestamp = iso_utc(utc_now())
        for plan in self.store.expired_pending_plans(timestamp):
            self.store.mark_plan_expired(plan["plan_id"], timestamp)
        for plan in self.store.plans_with_expired_inputs(timestamp):
            path = Path(plan["input_path"]).resolve()
            try:
                path.relative_to(self.storage_root)
            except ValueError:
                continue
            path.unlink(missing_ok=True)
            self.store.mark_plan_input_purged(plan["plan_id"], timestamp)
            self.store.append_event(
                event_id=f"event_{uuid.uuid4().hex}",
                plan_id=plan["plan_id"],
                job_id=None,
                event_type="repair_input_purged",
                timestamp=timestamp,
                payload={
                    "reason": "180-day repair input retention expired",
                    "audit_records_retained": True,
                },
            )
        for job in self.store.jobs_with_expired_files(timestamp):
            plan = self.store.get_plan(job["plan_id"])
            candidates = [Path(job["output_path"]).resolve()]
            if plan:
                candidates.append(Path(plan["input_path"]).resolve())
            for path in candidates:
                try:
                    path.relative_to(self.storage_root)
                except ValueError:
                    continue
                path.unlink(missing_ok=True)
            self.store.mark_files_purged(job["job_id"], timestamp)
            self.store.mark_plan_input_purged(job["plan_id"], timestamp)
            self.store.append_event(
                event_id=f"event_{uuid.uuid4().hex}",
                plan_id=job["plan_id"],
                job_id=job["job_id"],
                event_type="repair_files_purged",
                timestamp=timestamp,
                payload={
                    "reason": "180-day repair file retention expired",
                    "audit_records_retained": True,
                },
            )
