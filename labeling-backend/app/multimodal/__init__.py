"""多模态内容的拆分、隐式标识、解析、重组与校验。"""
from .service import label_multimodal, parse_multimodal_path, verify_multimodal_path

__all__ = [
    "label_multimodal",
    "parse_multimodal_path",
    "verify_multimodal_path",
]
