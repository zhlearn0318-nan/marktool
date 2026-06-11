from app.engine.base import Detector, DetectionContext
from app.schemas.models import CheckItem, CheckStatus, MarkType


class WatermarkDetector(Detector):
    name = "watermark"
    modality = "image"
    mark_type = MarkType.IMPLICIT_WATERMARK

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        return [CheckItem(
            id="watermark.trustmark",
            title="隐式标识(水印)",
            status=CheckStatus.WARN,
            detail="像素水印检测需 TrustMark 插件，当前未启用",
            suggestion="启用 TrustMark 插件以提取并校验抗压缩像素水印",
            mark_type=self.mark_type,
        )]
