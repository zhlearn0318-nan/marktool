from app.engine.base import Detector, DetectionContext
from app.schemas.models import CheckItem, CheckStatus, MarkType

_KW_TECH = ("ai", "人工智能")
_KW_GEN = ("生成", "合成")


def evaluate_explicit_mark(text: str, box: tuple, image_size: tuple) -> dict:
    """对一条 OCR 文字结果做真实判定（关键词 + 字高≥最短边5% + 位于边角）。
    box=(x1,y1,x2,y2)，image_size=(w,h)。供未来 OCR 插件复用。"""
    lower = text.lower()
    has_kw = any(k in lower for k in _KW_TECH) and any(k in text for k in _KW_GEN)
    w, h = image_size
    char_h = box[3] - box[1]
    height_ok = char_h >= 0.05 * min(w, h)
    in_corner = (box[0] <= 0.25 * w or box[2] >= 0.75 * w) and \
                (box[1] <= 0.25 * h or box[3] >= 0.75 * h)
    return {
        "has_keyword": has_kw,
        "height_ok": height_ok,
        "in_corner": in_corner,
        "compliant": has_kw and height_ok and in_corner,
    }


class ExplicitMarkDetector(Detector):
    name = "explicit_text"
    modality = "image"
    mark_type = MarkType.EXPLICIT_TEXT

    def applicable(self, ctx: DetectionContext) -> bool:
        return ctx.image is not None

    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        # OCR 后端为可插拔占位：判定逻辑(evaluate_explicit_mark)已就绪，
        # 接入 PaddleOCR 后将其输出逐条喂入即可。
        return [CheckItem(
            id="explicit.ocr",
            title="显式标识(边角文字)",
            status=CheckStatus.WARN,
            detail="显式文字标识检测需 OCR 插件(PaddleOCR)，当前未启用",
            suggestion="启用 OCR 插件以检测边角『AI/人工智能 + 生成/合成』文字及字高是否≥最短边5%",
            mark_type=self.mark_type,
        )]
