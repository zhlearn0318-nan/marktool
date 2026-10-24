"""ffprobe / ffmpeg 调用。参数用列表传递，不经过 shell。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..labeling_error import LabelingError


def run_tool(cmd: list[str], *, timeout: int = 300) -> str:
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LabelingError("MEDIA_TOOL_FAILED", f"{cmd[0]} 执行超时") from exc
    except FileNotFoundError as exc:
        raise LabelingError("MEDIA_TOOL_FAILED", f"未找到 {cmd[0]}，请先安装 ffmpeg / ffprobe。") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:400]
        raise LabelingError("MEDIA_TOOL_FAILED", f"{cmd[0]} 失败: {detail}")
    return completed.stdout


def probe(path: str | Path, ffprobe: str = "ffprobe") -> dict:
    raw = run_tool([
        ffprobe, "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ])
    try:
        data = json.loads(raw or "{}")
    except ValueError as exc:
        raise LabelingError("MEDIA_TOOL_FAILED", "ffprobe 输出无法解析") from exc
    return data if isinstance(data, dict) else {}


def duration_of(info: dict) -> float:
    try:
        return float((info.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def is_attached_picture(stream: dict) -> bool:
    disposition = stream.get("disposition") or {}
    return bool(disposition.get("attached_pic"))


def normalize_to_m4a(src: str | Path, dst: str | Path, *, ffmpeg: str, ffprobe: str) -> bool:
    """得到一份可写 XMP 的 M4A。已经是无视频轨的 AAC/M4A 时直接复制。

    返回值表示是否发生了转码。WAV、MP3 在当前 ExifTool 下不能写 XMP，
    因此先转为 AAC。
    """
    import shutil

    source = Path(src)
    destination = Path(dst)
    info = probe(source, ffprobe)
    streams = info.get("streams") or []
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    videos = [
        item for item in streams
        if item.get("codec_type") == "video" and not is_attached_picture(item)
    ]
    if not audios:
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "文件里没有音频轨道。")
    if videos:
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "该文件含视频轨，不能当作纯音频成分。")
    codec = audios[0].get("codec_name")
    if codec == "aac" and source.suffix.lower() in {".m4a", ".mp4", ".m4b"}:
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        return False
    run_tool([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-vn", "-c:a", "aac", "-b:a", "128k", str(destination),
    ])
    return True
