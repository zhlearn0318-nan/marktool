import hashlib
import json
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from app.metadata.c2pa_presence import (
    C2PAPresence,
    C2PAPresenceResult,
    detect_c2pa_presence,
)
from app.metadata.exiftool_client import (
    ExifToolClient,
    ExifToolExecutionError,
    ExifToolNotFoundError,
)
from app.metadata.identifier_registry import IdentifierRegistry
from app.metadata.xmp_reader import AIGCRecord, has_extended_xmp, read_aigc_records
from app.schemas.validation import (
    validate_aigc_business_rules,
    validate_aigc_document,
    validate_project_policy,
)


class ComplianceConclusion(str, Enum):
    COMPLIANT = "compliant"
    NONCOMPLIANT = "noncompliant"
    NOT_FOUND = "not_found"
    INDETERMINATE = "indeterminate"


class Repairability(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    CONFIRMABLE = "confirmable"
    MANUAL_REVIEW = "manual_review"
    FORBIDDEN = "forbidden"


class ComplianceIssue(BaseModel):
    code: str
    category: Literal[
        "gb45438", "project_policy", "carrier", "provenance", "c2pa", "tool"
    ]
    level: Literal["info", "warning", "error"]
    detail: str
    standard_basis: Optional[str] = None


class CandidateEvidence(BaseModel):
    index: int
    property_name: str
    packet_location: str
    raw_value_sha256: str
    raw_value_preview: str
    parseable: bool
    parsed_document: Any = None
    parse_error: Optional[str] = None


class ProjectPolicyResult(BaseModel):
    accepted: bool
    errors: list[str] = Field(default_factory=list)


class CrossReaderResult(BaseModel):
    status: Literal["matched", "diverged", "not_run", "unavailable"]
    detail: Optional[str] = None


class SourceVerificationResult(BaseModel):
    status: Literal[
        "verified", "partially_verified", "unverified", "conflict", "not_applicable"
    ]
    details: list[str] = Field(default_factory=list)


class C2PAPresencePayload(BaseModel):
    status: C2PAPresence
    carrier: Optional[str] = None
    detail: Optional[str] = None

    @classmethod
    def from_result(cls, result: C2PAPresenceResult) -> "C2PAPresencePayload":
        return cls(status=result.status, carrier=result.carrier, detail=result.detail)


class MetadataComplianceResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    conclusion: ComplianceConclusion
    reason_codes: list[str]
    repairability: Repairability
    detected_format: str
    mime_type: str
    file_sha256: str
    pixel_sha256: str
    record_count: int
    aigc_metadata: Optional[dict[str, Any]] = None
    candidates: list[CandidateEvidence] = Field(default_factory=list)
    issues: list[ComplianceIssue] = Field(default_factory=list)
    project_policy: ProjectPolicyResult
    cross_reader: CrossReaderResult
    source_verification: SourceVerificationResult
    c2pa_presence: C2PAPresencePayload
    extended_xmp: bool = False
    raw_records: list[AIGCRecord] = Field(default_factory=list, exclude=True)


class ComplianceInspectionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MetadataComplianceInspector:
    """只读检查 JPEG/PNG 文件元数据，不修改输入文件。"""

    def __init__(
        self,
        *,
        exiftool: Optional[ExifToolClient] = None,
        identifier_registry: Optional[IdentifierRegistry] = None,
    ):
        self.exiftool = exiftool
        self.identifier_registry = identifier_registry

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _inspect_image(path: Path) -> tuple[str, str, str]:
        try:
            with Image.open(path) as image:
                image.load()
                format_name = (image.format or "").upper()
                if format_name not in {"JPEG", "PNG"}:
                    raise ComplianceInspectionError(
                        "UNSUPPORTED_MEDIA_TYPE",
                        "当前合规检查只支持可正常解码的 JPEG/JPG 和 PNG",
                    )
                digest = hashlib.sha256()
                digest.update(image.mode.encode("ascii", "replace"))
                digest.update(str(image.size).encode("ascii"))
                digest.update(image.tobytes())
                mime_type = "image/jpeg" if format_name == "JPEG" else "image/png"
                return format_name, mime_type, digest.hexdigest()
        except ComplianceInspectionError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ComplianceInspectionError(
                "UNSUPPORTED_MEDIA_TYPE",
                "文件内容不是可正常解码的 JPEG/JPG 或 PNG",
            ) from exc

    @staticmethod
    def _canonical_value(value: str) -> str:
        try:
            return json.dumps(
                json.loads(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (json.JSONDecodeError, TypeError):
            return value.strip()

    @staticmethod
    def _candidate_evidence(records: list[AIGCRecord]) -> list[CandidateEvidence]:
        evidence: list[CandidateEvidence] = []
        for index, record in enumerate(records, start=1):
            preview = record.raw_value
            if len(preview) > 512:
                preview = preview[:512] + "…"
            evidence.append(
                CandidateEvidence(
                    index=index,
                    property_name=record.property_name,
                    packet_location=record.packet_location,
                    raw_value_sha256=hashlib.sha256(
                        record.raw_value.encode("utf-8", "replace")
                    ).hexdigest(),
                    raw_value_preview=preview,
                    parseable=record.parse_error is None,
                    parsed_document=record.document,
                    parse_error=record.parse_error,
                )
            )
        return evidence

    def _cross_read(
        self, path: Path, records: list[AIGCRecord]
    ) -> tuple[CrossReaderResult, Optional[ComplianceIssue]]:
        if self.exiftool is None:
            return CrossReaderResult(
                status="not_run", detail="当前检测未配置 ExifTool 交叉读取"
            ), None
        try:
            external = self.exiftool.read_known_aigc_values(str(path))
        except (ExifToolExecutionError, ExifToolNotFoundError) as exc:
            return (
                CrossReaderResult(status="unavailable", detail=str(exc)),
                ComplianceIssue(
                    code="EXIFTOOL_CROSSCHECK_UNAVAILABLE",
                    category="tool",
                    level="warning",
                    detail="ExifTool 交叉读取不可用；国标结构结论仍由项目读取器给出",
                ),
            )
        project_values = [self._canonical_value(item.raw_value) for item in records]
        external_values = [self._canonical_value(item) for item in external]
        if Counter(project_values) != Counter(external_values):
            return (
                CrossReaderResult(
                    status="diverged",
                    detail="项目读取器与 ExifTool 检出的 AIGC 候选项不一致",
                ),
                ComplianceIssue(
                    code="AIGC_READERS_DIVERGED",
                    category="carrier",
                    level="error",
                    detail="两条读取路径结果不一致，无法可靠判断唯一性",
                ),
            )
        return CrossReaderResult(status="matched"), None

    def _verify_source(
        self, document: Optional[dict], pixel_sha256: str
    ) -> tuple[SourceVerificationResult, list[ComplianceIssue]]:
        if not isinstance(document, dict):
            return SourceVerificationResult(status="not_applicable"), []
        candidate = document.get("AIGC", document)
        if not isinstance(candidate, dict):
            return SourceVerificationResult(status="not_applicable"), []
        required = (
            "ContentProducer", "ProduceID", "ContentPropagator", "PropagateID"
        )
        if any(
            not isinstance(candidate.get(field), str) or not candidate[field]
            for field in required
        ):
            return SourceVerificationResult(status="not_applicable"), []
        if self.identifier_registry is None:
            return SourceVerificationResult(
                status="unverified",
                details=["当前检测未连接编号登记库"],
            ), []

        aigc = candidate
        checks = (
            ("producer", aigc["ContentProducer"], aigc["ProduceID"]),
            ("propagator", aigc["ContentPropagator"], aigc["PropagateID"]),
        )
        matched = 0
        missing = 0
        details: list[str] = []
        issues: list[ComplianceIssue] = []
        for role, provider, content_id in checks:
            registered = self.identifier_registry.lookup(role, provider, content_id)
            if registered is None:
                missing += 1
                details.append(f"{role} 编号未在本地登记库中找到")
            elif registered == pixel_sha256:
                matched += 1
                details.append(f"{role} 编号与当前图片像素指纹一致")
            else:
                details.append(f"{role} 编号已登记到另一内容指纹")
                issues.append(
                    ComplianceIssue(
                        code="AIGC_IDENTIFIER_CONFLICT",
                        category="provenance",
                        level="error",
                        detail=f"{role} 编号与登记内容指纹冲突；这不是国标结构失败，但禁止自动修复",
                    )
                )
        if issues:
            status = "conflict"
        elif matched == len(checks):
            status = "verified"
        elif matched:
            status = "partially_verified"
        else:
            status = "unverified"
        return SourceVerificationResult(status=status, details=details), issues

    def inspect(
        self,
        file_path: str,
        *,
        initial_write: Optional[bool] = None,
    ) -> MetadataComplianceResult:
        path = Path(file_path).resolve()
        format_name, mime_type, pixel_sha256 = self._inspect_image(path)
        file_sha256 = self._sha256(path)
        issues: list[ComplianceIssue] = []
        reason_codes: list[str] = []

        extended_xmp = False
        extended_xmp_check_failed = False
        if format_name == "JPEG":
            try:
                extended_xmp = has_extended_xmp(str(path))
            except OSError as exc:
                extended_xmp_check_failed = True
                issues.append(
                    ComplianceIssue(
                        code="EXTENDED_XMP_CHECK_FAILED",
                        category="carrier",
                        level="error",
                        detail=f"无法检查 Extended XMP: {exc}",
                    )
                )

        try:
            records = read_aigc_records(str(path))
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ComplianceInspectionError(
                "METADATA_READ_FAILED", f"读取 AIGC 元数据失败: {exc}"
            ) from exc

        cross_reader, cross_issue = self._cross_read(path, records)
        if cross_issue is not None:
            issues.append(cross_issue)

        c2pa = detect_c2pa_presence(str(path), format_name)
        if c2pa.status is C2PAPresence.PRESENT_UNVERIFIED:
            issues.append(
                ComplianceIssue(
                    code="C2PA_PRESENT_UNVERIFIED",
                    category="c2pa",
                    level="warning",
                    detail=c2pa.detail or "发现 C2PA 数据；当前未验证签名",
                )
            )
        elif c2pa.status is C2PAPresence.INDETERMINATE:
            issues.append(
                ComplianceIssue(
                    code="C2PA_PRESENCE_INDETERMINATE",
                    category="c2pa",
                    level="warning",
                    detail=c2pa.detail or "无法判断是否存在 C2PA 数据",
                )
            )

        document: Optional[dict] = None
        project_errors: list[str] = []
        if extended_xmp_check_failed:
            conclusion = ComplianceConclusion.INDETERMINATE
            reason_codes.append("EXTENDED_XMP_CHECK_FAILED")
        elif extended_xmp:
            conclusion = ComplianceConclusion.INDETERMINATE
            reason_codes.append("EXTENDED_XMP_UNSUPPORTED")
            issues.append(
                ComplianceIssue(
                    code="EXTENDED_XMP_UNSUPPORTED",
                    category="carrier",
                    level="error",
                    detail="发现当前版本不能完整组装的 Extended XMP，不能可靠判断唯一性",
                )
            )
        elif cross_reader.status == "diverged":
            conclusion = ComplianceConclusion.INDETERMINATE
            reason_codes.append("AIGC_READERS_DIVERGED")
        elif not records:
            conclusion = ComplianceConclusion.NOT_FOUND
            reason_codes.append("AIGC_NOT_FOUND")
            issues.append(
                ComplianceIssue(
                    code="AIGC_NOT_FOUND",
                    category="gb45438",
                    level="info",
                    detail="未发现文件元数据隐式标识；如需新增应进入普通标注流程",
                    standard_basis="GB 45438—2025 6.1",
                )
            )
        elif len(records) != 1:
            conclusion = ComplianceConclusion.NONCOMPLIANT
            reason_codes.append("AIGC_MULTIPLE_RECORDS")
            issues.append(
                ComplianceIssue(
                    code="AIGC_MULTIPLE_RECORDS",
                    category="gb45438",
                    level="error",
                    detail=f"检出 {len(records)} 份 AIGC 候选记录；同一内容文件应仅保留一份",
                    standard_basis="GB 45438—2025 6.1 c)",
                )
            )
        else:
            record = records[0]
            if record.parse_error:
                conclusion = ComplianceConclusion.NONCOMPLIANT
                reason_codes.append("AIGC_JSON_INVALID")
                issues.append(
                    ComplianceIssue(
                        code="AIGC_JSON_INVALID",
                        category="gb45438",
                        level="error",
                        detail=record.parse_error,
                        standard_basis="GB 45438—2025 附录 E b)",
                    )
                )
            else:
                formal_errors = validate_aigc_document(record.document)
                if formal_errors:
                    conclusion = ComplianceConclusion.NONCOMPLIANT
                    reason_codes.append("AIGC_SCHEMA_INVALID")
                    issues.extend(
                        ComplianceIssue(
                            code="AIGC_SCHEMA_INVALID",
                            category="gb45438",
                            level="error",
                            detail=error,
                            standard_basis="GB 45438—2025 附录 E",
                        )
                        for error in formal_errors
                    )
                else:
                    document = record.document
                    conclusion = ComplianceConclusion.COMPLIANT
                    relationship_errors = validate_aigc_business_rules(
                        document,
                        strict_characters=False,
                        require_initial_relationships=initial_write is True,
                        enforce_project_lengths=False,
                    )
                    if relationship_errors:
                        conclusion = ComplianceConclusion.NONCOMPLIANT
                        reason_codes.append("AIGC_INITIAL_RELATION_INVALID")
                        issues.extend(
                            ComplianceIssue(
                                code="AIGC_INITIAL_RELATION_INVALID",
                                category="gb45438",
                                level="error",
                                detail=error,
                                standard_basis="GB 45438—2025 附录 E 注1",
                            )
                            for error in relationship_errors
                        )
                    else:
                        reason_codes.append("AIGC_COMPLIANT")
                    project_errors = validate_project_policy(document)
                    issues.extend(
                        ComplianceIssue(
                            code="PROJECT_POLICY_REJECTED",
                            category="project_policy",
                            level="warning",
                            detail=error,
                        )
                        for error in project_errors
                    )

        source_document = document
        if source_document is None:
            parseable_documents = [
                record.document
                for record in records
                if record.parse_error is None and isinstance(record.document, dict)
            ]
            if len(parseable_documents) == 1:
                source_document = parseable_documents[0]
            elif parseable_documents:
                identities = []
                for candidate_document in parseable_documents:
                    inner = candidate_document.get("AIGC", candidate_document)
                    if isinstance(inner, dict):
                        identities.append(
                            tuple(inner.get(field) for field in (
                                "ContentProducer", "ProduceID",
                                "ContentPropagator", "PropagateID",
                            ))
                        )
                if identities and len(set(identities)) == 1:
                    source_document = parseable_documents[0]
        source_verification, source_issues = self._verify_source(
            source_document, pixel_sha256
        )
        issues.extend(source_issues)

        if conclusion in {
            ComplianceConclusion.COMPLIANT,
            ComplianceConclusion.NOT_FOUND,
        }:
            repairability = Repairability.NOT_APPLICABLE
        else:
            repairability = Repairability.MANUAL_REVIEW
        if source_verification.status == "conflict":
            repairability = Repairability.FORBIDDEN

        return MetadataComplianceResult(
            conclusion=conclusion,
            reason_codes=list(dict.fromkeys(reason_codes)),
            repairability=repairability,
            detected_format=format_name,
            mime_type=mime_type,
            file_sha256=file_sha256,
            pixel_sha256=pixel_sha256,
            record_count=len(records),
            aigc_metadata=document["AIGC"] if document else None,
            candidates=self._candidate_evidence(records),
            issues=issues,
            project_policy=ProjectPolicyResult(
                accepted=not project_errors, errors=project_errors
            ),
            cross_reader=cross_reader,
            source_verification=source_verification,
            c2pa_presence=C2PAPresencePayload.from_result(c2pa),
            extended_xmp=extended_xmp,
            raw_records=records,
        )
