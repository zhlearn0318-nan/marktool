"""虚拟场景与交互场景。"""
from .interactive_scene import (
    label_interactive_scene, parse_interactive_scene, verify_interactive_scene,
)
from .virtual_scene import label_virtual_scene, parse_virtual_scene, verify_virtual_scene

__all__ = [
    "label_interactive_scene",
    "label_virtual_scene",
    "parse_interactive_scene",
    "parse_virtual_scene",
    "verify_interactive_scene",
    "verify_virtual_scene",
]
