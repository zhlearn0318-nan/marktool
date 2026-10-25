"""测试用的小体积合成媒体。依赖本机 ffmpeg。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("需要 ffmpeg")
    return path


def make_silent_video(directory: Path, name: str = "silent.mp4") -> Path:
    require_ffmpeg()
    target = directory / name
    subprocess.check_call([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x224466:s=320x180:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
    ])
    return target


def make_tone(directory: Path, name: str = "tone.m4a") -> Path:
    require_ffmpeg()
    target = directory / name
    subprocess.check_call([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:a", "aac", "-b:a", "64k", str(target),
    ])
    return target


def make_av_subtitle_video(directory: Path, name: str = "av-sub.mp4") -> Path:
    """1 秒画面 + 正弦音频 + 一条中文字幕。"""
    require_ffmpeg()
    vtt = directory / "cue.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n合成字幕示例\n",
        encoding="utf-8",
    )
    target = directory / name
    subprocess.check_call([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x224466:s=320x180:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-i", str(vtt),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        "-c:s", "mov_text",
        "-shortest", str(target),
    ])
    return target
