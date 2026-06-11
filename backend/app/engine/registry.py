from app.engine.base import Detector, DetectionContext
from app.schemas.models import CheckItem, CheckStatus


class DetectorRegistry:
    def __init__(self):
        self._detectors: list[Detector] = []

    def register(self, detector: Detector) -> Detector:
        self._detectors.append(detector)
        return detector

    def run_all(self, ctx: DetectionContext, modality: str) -> list[CheckItem]:
        items: list[CheckItem] = []
        for d in self._detectors:
            if d.modality != modality or not d.applicable(ctx):
                continue
            try:
                items.extend(d.detect(ctx))
            except Exception as exc:  # 单个检测器失败不应中断整体
                items.append(CheckItem(
                    id=f"{d.name}.error",
                    title=f"{d.name} 检测异常",
                    status=CheckStatus.WARN,
                    detail=str(exc),
                    suggestion="检查该检测器输入或实现",
                    mark_type=d.mark_type,
                ))
        return items


def build_default_registry() -> DetectorRegistry:
    """延迟导入避免循环依赖；注册图片模态的三个检测器。"""
    from app.engine.detectors.metadata_detector import MetadataDetector
    from app.engine.detectors.explicit_mark_detector import ExplicitMarkDetector
    from app.engine.detectors.watermark_detector import WatermarkDetector

    reg = DetectorRegistry()
    reg.register(MetadataDetector())
    reg.register(ExplicitMarkDetector())
    reg.register(WatermarkDetector())
    return reg
