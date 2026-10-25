"""命令行入口：``python -m app.cli``。

与 HTTP 接口调用同一套多模态 / 场景函数，不另写标识逻辑。
在 ``labeling-backend`` 目录下执行。
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .config import load_settings
from .labeling_error import LabelingError
from .multimodal.service import label_multimodal, parse_multimodal_path, verify_multimodal_path
from .scenes.interactive_scene import (
    label_interactive_scene, parse_interactive_scene, verify_interactive_scene,
)
from .scenes.virtual_scene import label_virtual_scene, parse_virtual_scene, verify_virtual_scene
from .tools import tools_from_settings


def _aigc_from_args(args: argparse.Namespace) -> dict:
    produce_id = args.produce_id or str(uuid.uuid4()).upper()
    producer = args.producer
    return {
        "Label": args.label,
        "ContentProducer": producer,
        "ProduceID": produce_id,
        "ReservedCode1": "",
        "ContentPropagator": args.propagator or producer,
        "PropagateID": args.propagate_id or produce_id,
        "ReservedCode2": "",
    }


def _add_aigc_args(parser: argparse.ArgumentParser, required_producer: bool = True) -> None:
    parser.add_argument("--producer", required=required_producer, help="ContentProducer")
    parser.add_argument("--produce-id", dest="produce_id", default=None)
    parser.add_argument("--label", default="1", choices=["1", "2", "3"])
    parser.add_argument("--propagator", default=None)
    parser.add_argument("--propagate-id", dest="propagate_id", default=None)
    parser.add_argument("--policy", default="reject", choices=["reject", "replace"])
    parser.add_argument("--output", required=True, help="输出目录")


def _print(report: dict) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed", True) else 2


def _run(action) -> int:
    try:
        return _print(action())
    except LabelingError as exc:
        print(json.dumps({"passed": False, "code": exc.code, "message": exc.message},
                         ensure_ascii=False), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marktool", description="GB 45438—2025 多模态与场景标识")
    sub = parser.add_subparsers(dest="command", required=True)

    multimodal = sub.add_parser("multimodal", help="多模态拆分、标识、解析、校验")
    mm = multimodal.add_subparsers(dest="action", required=True)
    label = mm.add_parser("label")
    _add_aigc_args(label)
    label.add_argument("--video", help="待拆分的视频文件")
    label.add_argument("--text")
    label.add_argument("--image")
    label.add_argument("--audio")
    label.add_argument("--part-video", dest="part_video", help="单独的视频成分，不与 --video 同时使用")
    parse = mm.add_parser("parse")
    parse.add_argument("--input", required=True)
    verify = mm.add_parser("verify")
    verify.add_argument("--input", required=True)

    scene = sub.add_parser("scene", help="虚拟场景与交互场景")
    scene_sub = scene.add_subparsers(dest="scene_type", required=True)
    virtual = scene_sub.add_parser("virtual")
    virtual_sub = virtual.add_subparsers(dest="action", required=True)
    virtual_label = virtual_sub.add_parser("label")
    _add_aigc_args(virtual_label)
    virtual_label.add_argument("--text", default="人工智能生成合成")
    virtual_label.add_argument("--position", default="bottom_right")
    virtual_label.add_argument("--color", default="#FFFFFF")
    virtual_label.add_argument("--background", default="#102033")
    virtual_label.add_argument("--during-service", action="store_true")
    virtual_label.add_argument("--source", help="起始画面图片，或用于截取第 1 帧的视频")
    virtual_parse = virtual_sub.add_parser("parse")
    virtual_parse.add_argument("--input", required=True)
    virtual_verify = virtual_sub.add_parser("verify")
    virtual_verify.add_argument("--input", required=True)

    interactive = scene_sub.add_parser("interactive")
    interactive_sub = interactive.add_subparsers(dest="action", required=True)
    interactive_label = interactive_sub.add_parser("label")
    _add_aigc_args(interactive_label, required_producer=False)
    interactive_label.add_argument("--text", default="AI生成")
    interactive_label.add_argument("--placement", action="append", dest="placements")
    interactive_label.add_argument("--color", default="#102033")
    interactive_label.add_argument("--background", default="#F4EBD4")
    interactive_label.add_argument("--content")
    interactive_label.add_argument("--content-role", dest="content_role",
                                   choices=["text", "image", "audio", "video"])
    interactive_parse = interactive_sub.add_parser("parse")
    interactive_parse.add_argument("--input", required=True)
    interactive_verify = interactive_sub.add_parser("verify")
    interactive_verify.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    tools = tools_from_settings(load_settings())
    if args.command == "multimodal" and args.action == "label":
        parts = {}
        if args.text:
            parts["text"] = args.text
        if args.image:
            parts["image"] = args.image
        if args.audio:
            parts["audio"] = args.audio
        if args.part_video:
            parts["video"] = args.part_video
        return _run(lambda: label_multimodal(
            args.output, _aigc_from_args(args), policy=args.policy, tools=tools,
            composite_video=args.video, parts=parts or None,
        ))
    if args.command == "multimodal" and args.action == "parse":
        return _run(lambda: parse_multimodal_path(args.input, tools))
    if args.command == "multimodal" and args.action == "verify":
        return _run(lambda: verify_multimodal_path(args.input, tools))
    if args.command == "scene" and args.scene_type == "virtual" and args.action == "label":
        return _run(lambda: label_virtual_scene(
            args.output, _aigc_from_args(args), text=args.text, position=args.position,
            color=args.color, background=args.background, during_service=args.during_service,
            source=args.source, policy=args.policy, tools=tools,
        ))
    if args.command == "scene" and args.scene_type == "virtual" and args.action == "parse":
        return _run(lambda: parse_virtual_scene(args.input, tools))
    if args.command == "scene" and args.scene_type == "virtual" and args.action == "verify":
        return _run(lambda: verify_virtual_scene(args.input, tools))
    if args.command == "scene" and args.scene_type == "interactive" and args.action == "label":
        aigc_obj = _aigc_from_args(args) if args.producer else None
        return _run(lambda: label_interactive_scene(
            args.output, text=args.text, placements=args.placements or ["near_content"],
            color=args.color, background=args.background, content=args.content,
            content_role=args.content_role, aigc_obj=aigc_obj, policy=args.policy, tools=tools,
        ))
    if args.command == "scene" and args.scene_type == "interactive" and args.action == "parse":
        return _run(lambda: parse_interactive_scene(args.input, tools))
    if args.command == "scene" and args.scene_type == "interactive" and args.action == "verify":
        return _run(lambda: verify_interactive_scene(args.input, tools))
    parser.error("无法识别的命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
