from app.engine.base import Detector, DetectionContext
from app.metadata.compliance import (
    ComplianceConclusion,
    ComplianceInspectionError,
    MetadataComplianceInspector,
)
from app.metadata.c2pa_presence import C2PAPresence
from app.metadata.exiftool_client import ExifToolClient, ExifToolNotFoundError
from app.schemas.models import CheckItem, CheckStatus, MarkType


class MetadataDetector(Detector):
    name = "metadata"
    modality = "image"
    mark_type = MarkType.IMPLICIT_METADATA

    def __init__(self, inspector: MetadataComplianceInspector | None = None):
        if inspector is None:
            try:
                exiftool = ExifToolClient()
            except ExifToolNotFoundError:
                exiftool = None
            inspector = MetadataComplianceInspector(exiftool=exiftool)
        self.inspector = inspector

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        try:
            result = self.inspector.inspect(ctx.file_path)
        except ComplianceInspectionError as exc:
            return [
                CheckItem(
                    id="metadata.indeterminate",
                    title="文件元数据隐式标识",
                    status=CheckStatus.WARN,
                    detail=f"{exc.code}: {exc}",
                    suggestion="停止自动处理并人工复核文件",
                    mark_type=self.mark_type,
                )
            ]

        ctx.cache["aigc_record_count"] = result.record_count
        ctx.cache["aigc_metadata"] = result.aigc_metadata
        ctx.cache["metadata_compliance"] = result.model_dump(mode="json")

        if result.conclusion is ComplianceConclusion.COMPLIANT:
            primary = CheckItem(
                id="metadata.schema",
                title="文件元数据隐式标识",
                status=CheckStatus.PASS,
                detail="检出唯一一份 AIGC 标识，且国标结构与七字段校验通过",
                mark_type=self.mark_type,
            )
        elif result.conclusion is ComplianceConclusion.NOT_FOUND:
            primary = CheckItem(
                id="metadata.presence",
                title="文件元数据隐式标识",
                status=CheckStatus.FAIL,
                detail="未发现 AIGC 文件元数据隐式标识（not_found）",
                suggestion="如业务确认需要新增标识，请进入普通标注流程；不要调用修复接口",
                mark_type=self.mark_type,
            )
        elif result.conclusion is ComplianceConclusion.NONCOMPLIANT:
            details = "; ".join(
                issue.detail
                for issue in result.issues
                if issue.category == "gb45438" and issue.level == "error"
            )
            primary = CheckItem(
                id=(
                    "metadata.uniqueness"
                    if "AIGC_MULTIPLE_RECORDS" in result.reason_codes
                    else "metadata.compliance"
                ),
                title="文件元数据隐式标识",
                status=CheckStatus.FAIL,
                detail=details or "AIGC 标识未通过国标检查",
                suggestion="先生成修复计划；只有来源明确且用户确认后才能修改文件",
                mark_type=self.mark_type,
            )
        else:
            details = "; ".join(
                issue.detail for issue in result.issues if issue.level == "error"
            )
            primary = CheckItem(
                id="metadata.indeterminate",
                title="文件元数据隐式标识",
                status=CheckStatus.WARN,
                detail=details or "当前无法可靠判断 AIGC 标识是否合规",
                suggestion="停止自动处理并人工复核",
                mark_type=self.mark_type,
            )

        items = [primary]
        if not result.project_policy.accepted:
            items.append(
                CheckItem(
                    id="metadata.project_policy",
                    title="项目安全与兼容性限制",
                    status=CheckStatus.WARN,
                    detail="; ".join(result.project_policy.errors),
                    suggestion="这属于项目处理限制，不等同于国标不合规",
                    mark_type=self.mark_type,
                )
            )
        if result.source_verification.status == "conflict":
            items.append(
                CheckItem(
                    id="metadata.provenance_conflict",
                    title="编号与内容来源核验",
                    status=CheckStatus.WARN,
                    detail="; ".join(result.source_verification.details),
                    suggestion="这不是国标结构失败，但必须停止自动修复并核对编号来源",
                    mark_type=self.mark_type,
                )
            )
        if result.c2pa_presence.status is C2PAPresence.PRESENT_UNVERIFIED:
            items.append(
                CheckItem(
                    id="metadata.c2pa_presence",
                    title="C2PA 来源凭证保护检查",
                    status=CheckStatus.WARN,
                    detail=result.c2pa_presence.detail or "发现尚未验证的 C2PA 数据",
                    suggestion="当前只确认存在性；完成签名验证和重签方案前不要自动改写",
                    mark_type=self.mark_type,
                )
            )
        elif result.c2pa_presence.status is C2PAPresence.INDETERMINATE:
            items.append(
                CheckItem(
                    id="metadata.c2pa_indeterminate",
                    title="C2PA 来源凭证保护检查",
                    status=CheckStatus.WARN,
                    detail=result.c2pa_presence.detail or "无法判断 C2PA 是否存在",
                    suggestion="停止自动修复并人工复核",
                    mark_type=self.mark_type,
                )
            )
        return items
