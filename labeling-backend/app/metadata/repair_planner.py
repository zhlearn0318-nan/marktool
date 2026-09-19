import json
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.metadata.c2pa_presence import C2PAPresence
from app.metadata.compliance import (
    ComplianceConclusion,
    MetadataComplianceResult,
    Repairability,
)
from app.schemas.validation import (
    AIGC_FIELD_ORDER,
    validate_aigc_business_rules,
    validate_aigc_document,
    validate_project_policy,
)


class RepairSourceType(str, Enum):
    DERIVED_FROM_FILE = "derived_from_file"
    IDENTIFIER_REGISTRY = "identifier_registry"
    AUDIT_HISTORY = "audit_history"
    VERIFIED_PROVIDER_RECORD = "verified_provider_record"
    AUTHORIZED_MANUAL = "authorized_manual"


class WriteContext(str, Enum):
    UNKNOWN = "unknown"
    INITIAL_GENERATION = "initial_generation"
    PROPAGATION = "propagation"


class TrustedRepairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    AIGC: dict[str, Any]
    source_type: Literal[
        "identifier_registry",
        "audit_history",
        "verified_provider_record",
        "authorized_manual",
    ]
    source_reference: str = Field(min_length=1, max_length=500)
    write_context: WriteContext = WriteContext.UNKNOWN

    def document(self) -> dict[str, Any]:
        return {"AIGC": self.AIGC}


class FieldChange(BaseModel):
    field: str
    before: Any = None
    after: Any = None
    reason: str


class RepairPlanDraft(BaseModel):
    repairability: Repairability
    executable: bool
    source_type: Optional[RepairSourceType] = None
    source_reference: Optional[str] = None
    write_context: WriteContext = WriteContext.UNKNOWN
    proposed_document: Optional[dict[str, Any]] = None
    actions: list[str] = Field(default_factory=list)
    field_changes: list[FieldChange] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepairPlanningError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


class RepairPlanner:
    """根据只读检查结果生成计划；本类绝不修改文件。"""

    def __init__(self, *, require_cross_reader: bool = True):
        self.require_cross_reader = require_cross_reader

    @staticmethod
    def _canonical(document: dict) -> str:
        return json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _normalize_safe(document: Any) -> tuple[Optional[dict], list[str]]:
        """只做无需猜测语义的结构归一化。"""
        if not isinstance(document, dict):
            return None, []
        actions: list[str] = []
        if "AIGC" in document:
            if set(document) != {"AIGC"} or not isinstance(document["AIGC"], dict):
                return None, []
            aigc = dict(document["AIGC"])
        else:
            aigc = dict(document)
            actions.append("补充外层 AIGC 对象")

        allowed = set(AIGC_FIELD_ORDER)
        if set(aigc) - allowed:
            return None, []

        for reserved in ("ReservedCode1", "ReservedCode2"):
            if reserved not in aigc:
                aigc[reserved] = ""
                actions.append(f"经确认将缺失的 {reserved} 补为空字符串")

        label = aigc.get("Label")
        if type(label) is int and label in {1, 2, 3}:
            aigc["Label"] = str(label)
            actions.append("将数值型 Label 转换为等值字符串")

        if set(aigc) != allowed:
            return None, []
        normalized = {
            "AIGC": {field: aigc[field] for field in AIGC_FIELD_ORDER}
        }
        if validate_aigc_document(normalized):
            return None, []
        if validate_project_policy(normalized):
            return None, []
        return normalized, actions

    @staticmethod
    def _field_changes(
        before_document: Optional[dict], after_document: dict, reason: str
    ) -> list[FieldChange]:
        before = {}
        if isinstance(before_document, dict):
            candidate = before_document.get("AIGC", before_document)
            if isinstance(candidate, dict):
                before = candidate
        after = after_document["AIGC"]
        changes: list[FieldChange] = []
        for field in AIGC_FIELD_ORDER:
            if before.get(field) != after.get(field):
                changes.append(
                    FieldChange(
                        field=field,
                        before=before.get(field),
                        after=after.get(field),
                        reason=reason,
                    )
                )
        return changes

    @staticmethod
    def _validate_trusted(
        trusted: TrustedRepairInput,
    ) -> dict[str, Any]:
        document = trusted.document()
        errors = validate_aigc_document(document)
        errors.extend(validate_project_policy(document))
        if trusted.write_context is WriteContext.INITIAL_GENERATION:
            errors.extend(
                validate_aigc_business_rules(
                    document,
                    strict_characters=False,
                    enforce_project_lengths=False,
                    require_initial_relationships=True,
                )
            )
        if errors:
            raise RepairPlanningError(
                "TRUSTED_METADATA_INVALID",
                "提交的可信七字段不能作为修复结果",
                errors,
            )
        if not trusted.source_reference.strip():
            raise RepairPlanningError(
                "TRUSTED_SOURCE_REFERENCE_REQUIRED",
                "人工或外部可信数据必须说明来源引用",
            )
        return document

    def plan(
        self,
        inspection: MetadataComplianceResult,
        trusted: Optional[TrustedRepairInput] = None,
    ) -> RepairPlanDraft:
        if inspection.conclusion is ComplianceConclusion.COMPLIANT:
            return RepairPlanDraft(
                repairability=Repairability.NOT_APPLICABLE,
                executable=False,
                blocking_reasons=["当前文件元数据已经符合国标，无需修复"],
            )
        if inspection.conclusion is ComplianceConclusion.NOT_FOUND:
            return RepairPlanDraft(
                repairability=Repairability.NOT_APPLICABLE,
                executable=False,
                blocking_reasons=["未发现标识；如需新增应进入普通标注流程"],
            )

        blockers: list[str] = []
        if inspection.extended_xmp:
            blockers.append("Extended XMP 当前不能安全组装")
        if inspection.cross_reader.status == "diverged":
            blockers.append("项目读取器与 ExifTool 结果不一致")
        if self.require_cross_reader and inspection.cross_reader.status in {
            "not_run",
            "unavailable",
        }:
            blockers.append("ExifTool 交叉读取未完成，不能可靠确认现有标识份数")
        if inspection.c2pa_presence.status is C2PAPresence.PRESENT_UNVERIFIED:
            blockers.append("发现尚未验证的 C2PA 来源凭证，当前禁止自动改写")
        if inspection.c2pa_presence.status is C2PAPresence.INDETERMINATE:
            blockers.append("无法判断 C2PA 是否存在")
        if inspection.source_verification.status == "conflict":
            return RepairPlanDraft(
                repairability=Repairability.FORBIDDEN,
                executable=False,
                blocking_reasons=["编号登记与当前图片内容指纹冲突"],
            )
        if blockers:
            return RepairPlanDraft(
                repairability=Repairability.MANUAL_REVIEW,
                executable=False,
                blocking_reasons=blockers,
            )

        if trusted is not None:
            proposed = self._validate_trusted(trusted)
            before = (
                inspection.raw_records[0].document
                if len(inspection.raw_records) == 1
                else None
            )
            return RepairPlanDraft(
                repairability=Repairability.CONFIRMABLE,
                executable=True,
                source_type=RepairSourceType(trusted.source_type),
                source_reference=trusted.source_reference.strip(),
                write_context=trusted.write_context,
                proposed_document=proposed,
                actions=["删除全部旧 AIGC 候选记录", "整体写入一份可信七字段记录"],
                field_changes=self._field_changes(
                    before, proposed, "来自已声明的可信修复来源"
                ),
                warnings=["操作人身份当前未接入账号系统，确认记录不等同于身份认证"],
            )

        normalized: list[tuple[dict, list[str], Any]] = []
        for record in inspection.raw_records:
            if record.parse_error:
                return RepairPlanDraft(
                    repairability=Repairability.MANUAL_REVIEW,
                    executable=False,
                    blocking_reasons=["存在损坏 JSON，无法从文件可靠恢复七字段"],
                )
            proposed, actions = self._normalize_safe(record.document)
            if proposed is None:
                return RepairPlanDraft(
                    repairability=Repairability.MANUAL_REVIEW,
                    executable=False,
                    blocking_reasons=["身份、编号、Label 或结构需要可信外部数据决定"],
                )
            normalized.append((proposed, actions, record.document))

        if not normalized:
            return RepairPlanDraft(
                repairability=Repairability.MANUAL_REVIEW,
                executable=False,
                blocking_reasons=["没有可用于生成修复结果的原始记录"],
            )
        canonical = {self._canonical(item[0]) for item in normalized}
        if len(canonical) != 1:
            return RepairPlanDraft(
                repairability=Repairability.MANUAL_REVIEW,
                executable=False,
                blocking_reasons=["多份标识内容冲突，系统不会任意选择其中一份"],
            )

        proposed = normalized[0][0]
        actions = [action for item in normalized for action in item[1]]
        if len(normalized) > 1:
            actions.append("经确认删除完全相同的重复标识，仅保留一份")
        actions.append("整体写入一份规范记录")
        actions = list(dict.fromkeys(actions))
        changes = self._field_changes(
            normalized[0][2], proposed, "由原文件中的确定性结构转换得到"
        )
        return RepairPlanDraft(
            repairability=Repairability.CONFIRMABLE,
            executable=True,
            source_type=RepairSourceType.DERIVED_FROM_FILE,
            source_reference="原文件中全部可识别 AIGC 候选记录",
            proposed_document=proposed,
            actions=actions,
            field_changes=changes,
            warnings=["执行前仍需用户明确确认；原文件不会被覆盖"],
        )
