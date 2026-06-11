from app.engine.base import Detector, DetectionContext
from app.metadata.xmp_reader import extract_aigc_json
from app.schemas.models import CheckItem, CheckStatus, MarkType
from app.schemas.validation import validate_aigc_json


class MetadataDetector(Detector):
    name = "metadata"
    modality = "image"
    mark_type = MarkType.IMPLICIT_METADATA

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        aigc = extract_aigc_json(ctx.image)
        ctx.cache["aigc_metadata"] = aigc
        if aigc is None:
            return [CheckItem(
                id="metadata.presence",
                title="隐式标识(元数据)",
                status=CheckStatus.FAIL,
                detail="未在文件元数据(XMP)中检出 AIGC 标识 JSON",
                suggestion="按 GB 45438 附录E 在 XMP 中写入 AIGC 标识信息(Label/ContentProducer/ProduceID 等)",
                mark_type=self.mark_type,
            )]
        errors = validate_aigc_json(aigc)
        if errors:
            joined = "; ".join(errors)
            return [CheckItem(
                id="metadata.schema",
                title="隐式标识(元数据)格式校验",
                status=CheckStatus.WARN,
                detail=f"检出 AIGC JSON 但不符合附录E结构: {joined}",
                suggestion=f"修正以下问题: {joined}",
                mark_type=self.mark_type,
            )]
        return [CheckItem(
            id="metadata.schema",
            title="隐式标识(元数据)",
            status=CheckStatus.PASS,
            detail="检出并通过附录E结构校验的 AIGC 标识",
            mark_type=self.mark_type,
        )]
