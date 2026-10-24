"""单模态隐式标识的写入、解析与校验入口。

图片与视频走既有适配器；音频（M4A / XMP-aigc）与文本（文件尾元数据段）
在此补齐，供多模态拆分后的成分文件复用。
"""
from .audio_label import AudioAdapter
from .dispatch import label_file, parse_file, verify_implicit
from .text_label import parse_text_bytes, write_text_label

__all__ = [
    "AudioAdapter",
    "label_file",
    "parse_file",
    "parse_text_bytes",
    "verify_implicit",
    "write_text_label",
]
