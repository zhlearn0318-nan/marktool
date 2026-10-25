"""单模态隐式标识的写入、解析与校验入口。

图片、视频、M4A、MP3 和文档走 ``app.adapters`` 与图片服务；
字幕、纯文本的尾段只用于还没有容器适配器的成分。
"""
from .dispatch import label_file, parse_file, verify_implicit
from .text_label import parse_text_bytes, write_text_label

__all__ = [
    "label_file",
    "parse_file",
    "parse_text_bytes",
    "verify_implicit",
    "write_text_label",
]
