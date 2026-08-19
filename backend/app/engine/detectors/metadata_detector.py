from app.engine.base import Detector, DetectionContext
from app.metadata.xmp_reader import read_aigc_records
from app.schemas.models import CheckItem, CheckStatus, MarkType
from app.schemas.validation import (
    validate_aigc_business_rules,
    validate_aigc_document,
)


class MetadataDetector(Detector):
    name = "metadata"
    modality = "image"
    mark_type = MarkType.IMPLICIT_METADATA

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        records = read_aigc_records(ctx.file_path)
        ctx.cache["aigc_record_count"] = len(records)
        ctx.cache["aigc_metadata"] = None

        if not records:
            return [CheckItem(
                id="metadata.presence",
                title="文件元数据隐式标识",
                status=CheckStatus.FAIL,
                detail="未在图片 XMP 中检出 AIGC 标识 JSON",
                suggestion="按 GB 45438—2025 附录 E 写入一份完整的 AIGC 七字段标识",
                mark_type=self.mark_type,
            )]

        if len(records) != 1:
            return [CheckItem(
                id="metadata.uniqueness",
                title="文件元数据隐式标识唯一性",
                status=CheckStatus.FAIL,
                detail=f"检出 {len(records)} 份 AIGC 标识；国标要求仅保留一份",
                suggestion="删除重复记录并整体写入一份完整 AIGC 标识",
                mark_type=self.mark_type,
            )]

        record = records[0]
        if record.parse_error:
            return [CheckItem(
                id="metadata.json",
                title="文件元数据隐式标识 JSON 校验",
                status=CheckStatus.FAIL,
                detail=record.parse_error,
                suggestion="将字段值修正为合法的 UTF-8 JSON",
                mark_type=self.mark_type,
            )]

        errors = validate_aigc_document(record.document)
        if errors:
            joined = "; ".join(errors)
            return [CheckItem(
                id="metadata.schema",
                title="文件元数据隐式标识七字段校验",
                status=CheckStatus.FAIL,
                detail=f"AIGC JSON 不符合附录 E 结构: {joined}",
                suggestion=f"修正以下问题: {joined}",
                mark_type=self.mark_type,
            )]

        character_errors = validate_aigc_business_rules(
            record.document,
            strict_characters=True,
            require_initial_relationships=False,
        )
        if character_errors:
            joined = "; ".join(character_errors)
            return [CheckItem(
                id="metadata.characters",
                title="文件元数据隐式标识字符校验",
                status=CheckStatus.FAIL,
                detail=f"AIGC 字段不符合首期严格字符范围: {joined}",
                suggestion="改用附录 E 规定的主要字符范围，并去除空格、双引号、反斜杠和换行",
                mark_type=self.mark_type,
            )]

        ctx.cache["aigc_metadata"] = record.aigc
        return [CheckItem(
            id="metadata.schema",
            title="文件元数据隐式标识",
            status=CheckStatus.PASS,
            detail="检出唯一一份 AIGC 标识，且 JSON 与七字段结构校验通过",
            mark_type=self.mark_type,
        )]
