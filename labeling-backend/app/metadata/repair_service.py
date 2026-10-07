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

from app.adapters import AdapterError, get_adapter
from app.core import aigc as aigc_core
# 两个同名类：这个是视频检测器（产出统一报告 dict），下面 metadata.compliance 里
# 那个是图片检测器（产出 MetadataComplianceResult）。别名区分，免得又踩一次。
from app.core.inspector import MetadataComplianceInspector as VideoComplianceInspector
from app.core.mimetype import SNIFF_BYTES, detect_mime, suffix_for_mime
from app.metadata.compliance import (
    ComplianceConclusion,
    ComplianceInspectionError,
    MetadataComplianceInspector,
)
from app.metadata.cross_read import (MARKDOWN_CROSS_READ, cross_read_mp4,
                                     cross_read_pdf)
from app.metadata.document_inspector import (DOCUMENT_MIMES,
                                             DocumentComplianceInspector)
from app.metadata.exiftool_client import ExifToolClient, ExifToolNotFoundError
from app.metadata.identifier_registry import (SQLiteIdentifierRegistry,
                                              build_identifier_lookup)
from app.metadata.image_adapter import ImageMetadataError, ImageMetadataService
from app.metadata.repair_inspection import from_report
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
    # 视频/文档的修复走适配器注册表，需要与打标流水线同一套外部工具与配置，
    # 否则同一份文件在"打标"与"修复"两条路上会读到不同的标识（§14）。
    exiftool: str = "exiftool"
    ffprobe: str = "ffprobe"
    ffmpeg: str = "ffmpeg"
    exiftool_config: Optional[str] = None
    duration_tolerance_seconds: float = 0.1

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
            exiftool=os.getenv("AIGC_EXIFTOOL_PATH", "exiftool"),
            ffprobe=os.getenv("AIGC_FFPROBE_PATH", "ffprobe"),
            ffmpeg=os.getenv("AIGC_FFMPEG_PATH", "ffmpeg"),
            exiftool_config=os.getenv("AIGC_EXIFTOOL_CONFIG") or None,
        )


@dataclass(frozen=True)
class _UploadInfo:
    format_name: str
    mime_type: str
    suffix: str
    content_fingerprint: str
    fingerprint_kind: str

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")


# 修复台支持的格式。图片走自成一体的写入服务，其余走适配器注册表——
# 与打标流水线同一条分界线（``app/core/pipeline.py::_execute``），
# 两条路用同一套判定，同一份文件才不会得到两个答案。
_IMAGE_MIMES = frozenset({"image/jpeg", "image/png"})
_ADAPTER_MIMES = frozenset({"video/mp4"}) | set(DOCUMENT_MIMES)

# 各模态的人类可读格式名（报告的 detected_format / 界面文案）
_FORMAT_NAMES = {
    "image/jpeg": "JPEG", "image/png": "PNG", "video/mp4": "MP4",
    "text/markdown": "Markdown", "application/pdf": "PDF",
}


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

    def _detect_mime(self, data: bytes, filename: Optional[str]) -> str:
        mime = detect_mime(data[:SNIFF_BYTES], filename)
        if mime is None or mime not in _IMAGE_MIMES | _ADAPTER_MIMES:
            raise MetadataRepairRequestError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "修复台支持 JPEG / PNG / MP4 / Markdown(.md) / PDF",
            )
        return mime

    def _inspect_image_upload(self, data: bytes) -> _UploadInfo:
        """图片走 Pillow：既要判格式，也要在解码阶段挡住解压炸弹（§13）。"""
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
                415, "UNSUPPORTED_MEDIA_TYPE", "文件内容不是可正常解码的 JPEG/JPG 或 PNG"
            ) from exc
        if format_name not in {"JPEG", "PNG"}:
            raise MetadataRepairRequestError(
                415, "UNSUPPORTED_MEDIA_TYPE", "文件内容不是可正常解码的 JPEG/JPG 或 PNG"
            )
        mime = "image/jpeg" if format_name == "JPEG" else "image/png"
        return _UploadInfo(format_name, mime, suffix_for_mime(mime),
                           digest.hexdigest(), "pixel")

    def _inspect_upload(self, data: bytes, filename: Optional[str],
                        path: Path) -> _UploadInfo:
        """探明格式与内容指纹。指纹按模态取，但都在**写入标识前**算（§6.1 b）。

        视频/文档的指纹必须从文件算（ffprobe / 正文哈希 / 结构签名），所以这里
        要一个已经落盘的路径；图片的像素哈希不依赖元数据，直接从内存解码即可。
        """
        mime = self._detect_mime(data, filename)
        if mime in _IMAGE_MIMES:
            return self._inspect_image_upload(data)
        try:
            fingerprint, kind = self._adapter_for(mime).content_fingerprint(path)
        except AdapterError as exc:
            raise MetadataRepairRequestError(
                415, "UNSUPPORTED_MEDIA_TYPE", f"文件无法解析: {exc}"
            ) from exc
        return _UploadInfo(_FORMAT_NAMES.get(mime, mime), mime,
                           suffix_for_mime(mime), fingerprint, kind)

    def _adapter_for(self, mime: str):
        return get_adapter(mime, exiftool=self.config.exiftool,
                           ffprobe=self.config.ffprobe,
                           ffmpeg=self.config.ffmpeg,
                           exiftool_config=self.config.exiftool_config)

    def _unified_inspector(self, mime: str):
        """视频/文档的统一检测器——与 /compliance-inspect 用的是同一对。"""
        if mime in DOCUMENT_MIMES:
            return DocumentComplianceInspector(
                mime, exiftool=self.config.exiftool,
                exiftool_config=self.config.exiftool_config)
        return VideoComplianceInspector(
            exiftool=self.config.exiftool, ffprobe=self.config.ffprobe,
            ffmpeg=self.config.ffmpeg,
            exiftool_config=self.config.exiftool_config)

    def _cross_read(self, path: Path, upload: _UploadInfo, records):
        if upload.mime_type == "video/mp4":
            return cross_read_mp4(path, records, ffprobe=self.config.ffprobe)
        if upload.mime_type == "application/pdf":
            return cross_read_pdf(path, records)
        return MARKDOWN_CROSS_READ, []

    def _planned_inspection(self, path: Path, upload: _UploadInfo,
                            *, file_name: str, sha256: str):
        """规划器要的检测结果。图片直达，其余经转换层装配（§8.3）。

        装配而不是另写一套判定：规划器里那几处读法就是"能不能自动修"的判定
        逻辑本身，三种模态必须共用同一份，否则同一个文件会在检测页与修复台
        得到两个答案。
        """
        if upload.is_image:
            _, inspector = self._get_tools()
            return inspector.inspect(str(path))
        adapter = self._adapter_for(upload.mime_type)
        records = adapter.detect_existing(path)
        report = self._unified_inspector(upload.mime_type).inspect(
            str(path), file_name=file_name, size_bytes=path.stat().st_size,
            sha256=sha256,
            registry=build_identifier_lookup(
                str(self.config.identifier_database_path)))
        cross, issues = self._cross_read(path, upload, records)
        return from_report(
            report, records, content_fingerprint=upload.content_fingerprint,
            fingerprint_kind=upload.fingerprint_kind, cross_reader=cross,
            registry=self._registry, extra_issues=issues)

    @staticmethod
    def _safe_name(filename: Optional[str]) -> str:
        basename = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", basename).strip(" .")
        return (cleaned or "upload")[:160]

    @classmethod
    def _output_name(cls, original_name: str, suffix: str) -> str:
        stem = Path(cls._safe_name(original_name)).stem[:120] or "file"
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
        # 先探格式定后缀，把文件落盘，再算内容指纹——视频/文档的指纹必须从
        # 文件算（ffprobe / 正文哈希 / 结构签名），拿不到路径就算不出来。
        mime = self._detect_mime(data, filename)
        now_dt = utc_now()
        now = iso_utc(now_dt)
        plan_id = f"plan_{uuid.uuid4().hex}"
        request_id = f"req_{uuid.uuid4().hex}"
        input_path = self.input_dir / f"{uuid.uuid4().hex}{suffix_for_mime(mime)}"
        input_path.write_bytes(data)
        try:
            try:
                upload = self._inspect_upload(data, filename, input_path)
                if upload.is_image:
                    _, inspector = self._get_tools()
            except ExifToolNotFoundError as exc:
                raise MetadataRepairRequestError(
                    503,
                    "METADATA_TOOL_UNAVAILABLE",
                    "ExifTool 当前不可用，不能可靠生成修复计划",
                ) from exc
            try:
                inspection = self._planned_inspection(
                    input_path, upload,
                    file_name=self._safe_name(filename),
                    sha256=hashlib.sha256(data).hexdigest())
            except (ComplianceInspectionError, AdapterError) as exc:
                raise MetadataRepairRequestError(
                    422, getattr(exc, "code", "UNSUPPORTED_MEDIA_TYPE"), str(exc)
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
                    "content_fingerprint": upload.content_fingerprint,
                    "fingerprint_kind": upload.fingerprint_kind,
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
                    "content_fingerprint": upload.content_fingerprint,
                    "fingerprint_kind": upload.fingerprint_kind,
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
                "content_fingerprint": record["content_fingerprint"],
                "fingerprint_kind": record["fingerprint_kind"],
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
        # 后缀按真实 MIME 取：修复台现在服务三种模态，写死 jpg/png 会把 MP4
        # 的成品存成 .png（内容没错，但下载回来名字是错的）。
        suffix = suffix_for_mime(plan["detected_mime_type"])
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

    # ---- 两条写入路径：图片自成一体的服务，其余走适配器注册表 ----

    def _write_image(self, input_path: Path, output_path: Path, document: dict,
                     draft: RepairPlanDraft, plan: dict,
                     *, update_stage) -> dict[str, Any]:
        image_service, inspector = self._get_tools()
        if output_path.is_file():
            # 崩溃恢复：成品已在，不重写，直接接着做回读与复检
            result = image_service.recover_published(
                str(input_path), str(output_path), document,
                expected_input_sha256=plan["input_sha256"])
        else:
            result = image_service.write(
                str(input_path), str(output_path), document, policy="replace",
                initial_write=(draft.write_context is WriteContext.INITIAL_GENERATION),
                stage_callback=update_stage)
        post = inspector.inspect(
            str(output_path),
            initial_write=(
                True if draft.write_context is WriteContext.INITIAL_GENERATION else None))
        return {
            "mime_type": result.mime_type,
            "output_sha256": result.output_sha256,
            "post": post,
            "validation": {
                "read_back_succeeded": result.validation.read_back_succeeded,
                "schema_valid": result.validation.schema_valid,
                "single_aigc_record": result.validation.single_aigc_record,
                "media_integrity_valid": result.validation.media_integrity_valid,
                "content_fingerprint_unchanged":
                    post.content_fingerprint == plan["content_fingerprint"],
            },
        }

    def _write_with_adapter(self, input_path: Path, output_path: Path,
                            document: dict, draft: RepairPlanDraft, plan: dict,
                            mime: str, *, update_stage) -> dict[str, Any]:
        """视频/文档的修复写入与复检。

        写入顺序与打标流水线**逐字一致**（先整体移除旧标识，再写一份），否则
        同一份文件走"打标"和走"修复"会留下不同形态的成品。复检也必须用检测器
        而不是回读结果自证：回读只说"我写进去了什么"，检测说"这份文件合规吗"。
        """
        adapter = self._adapter_for(mime)
        submitted = document["AIGC"]
        update_stage("writing")
        if not output_path.is_file():
            staging = output_path.with_name(output_path.name + ".staging")
            staging.unlink(missing_ok=True)
            try:
                if adapter.detect_existing(input_path):
                    no_aigc = output_path.with_name(output_path.name + ".noaigc")
                    try:
                        adapter.remove_aigc(input_path, no_aigc)
                        adapter.write_metadata(no_aigc, staging, submitted)
                    finally:
                        no_aigc.unlink(missing_ok=True)
                else:
                    adapter.write_metadata(input_path, staging, submitted)
                os.replace(staging, output_path)
            finally:
                staging.unlink(missing_ok=True)

        # ---- 回读校验（§9.4）----
        update_stage("verifying_metadata")
        records = adapter.detect_existing(output_path)
        if len(records) != 1:
            raise AdapterError(f"修复后应恰好一份标识，实际 {len(records)} 份")
        read_back = records[0].aigc
        if read_back is None:
            raise AdapterError("修复后的标识无法解析为 JSON")
        if aigc_core.validate_aigc(read_back):
            raise AdapterError("修复后的标识未通过七字段 Schema 校验")
        if read_back != submitted:
            raise AdapterError("修复后的七字段与确认计划不一致")

        # ---- 媒体完整性（§9.4）----
        update_stage("verifying_media")
        media = adapter.media_integrity_check(
            input_path, output_path,
            duration_tolerance=self.config.duration_tolerance_seconds)
        if not media.passed:
            raise AdapterError(f"媒体完整性校验失败: {media.reason}")

        # ---- 复检：用检测器再判一次，而不是拿回读结果自证 ----
        update_stage("postchecking")
        fingerprint, kind = adapter.content_fingerprint(output_path)
        upload = _UploadInfo(_FORMAT_NAMES.get(mime, mime), mime,
                             suffix_for_mime(mime), fingerprint, kind)
        output_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        post = self._planned_inspection(
            output_path, upload, file_name=output_path.name,
            sha256=output_sha256)
        return {
            "mime_type": mime,
            "output_sha256": output_sha256,
            "post": post,
            "validation": {
                "read_back_succeeded": True,
                "schema_valid": True,
                "single_aigc_record": True,
                "media_integrity_valid": media.passed,
                "carrier_tag": records[0].tag_key,
                "content_fingerprint_unchanged":
                    fingerprint == plan["content_fingerprint"],
            },
        }

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
            def update_stage(stage: str) -> None:
                self.store.update_stage(job_id, stage, iso_utc(utc_now()))

            mime = plan["detected_mime_type"]
            if mime in _IMAGE_MIMES:
                result = self._write_image(
                    input_path, output_path, document, draft, plan,
                    update_stage=update_stage)
            else:
                result = self._write_with_adapter(
                    input_path, output_path, document, draft, plan, mime,
                    update_stage=update_stage)
            post, validation = result["post"], result["validation"]
            validation["post_repair_conclusion"] = post.conclusion.value
            validation["project_policy_accepted"] = post.project_policy.accepted
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
            if not validation["content_fingerprint_unchanged"]:
                raise ImageMetadataError(
                    "MEDIA_INTEGRITY_FAILED", "修复前后内容指纹不一致"
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
                output_mime_type=result["mime_type"],
                output_size_bytes=output_path.stat().st_size,
                output_sha256=result["output_sha256"],
                validation=validation,
                event_payload={
                    "output_sha256": result["output_sha256"],
                    "files_expires_at": files_expires_at,
                    "validation": validation,
                    "audit_artifact": repaired_artifact,
                },
            )
        except (ImageMetadataError, ComplianceInspectionError, AdapterError) as exc:
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
