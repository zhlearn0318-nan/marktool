"""M4A 音频元数据适配器。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.jobs import MODALITY_AUDIO
from .base import AdapterError, BaseAdapter, MediaReport


class M4aAdapter(BaseAdapter):
    """通过嵌入式 XMP-aigc:AIGC 为 M4A 写入标识。"""

    carrier_id = "m4a-aigc-v1"
    modality = MODALITY_AUDIO
    supported_mimes = ("audio/mp4",)

    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        before = self._signature(self._ffprobe_json(src))
        after = self._signature(self._ffprobe_json(dst))
        problems: list[str] = []
        if before["format_name"] != after["format_name"]:
            problems.append(f"容器格式变化: {before['format_name']} -> {after['format_name']}")
        if abs(before["duration"] - after["duration"]) > duration_tolerance:
            problems.append(f"时长变化: {before['duration']:.3f}s -> {after['duration']:.3f}s")
        if before["audio_stream_count"] == 0:
            problems.append("未探测到音频轨")
        if after["audio_stream_count"] == 0:
            problems.append("写入后未探测到音频轨")
        if before["audio_streams"] != after["audio_streams"]:
            problems.append("音频轨数量、编解码器、采样率或声道数发生变化")
        if not problems:
            try:
                self._decode_smoke(dst)
            except AdapterError as exc:
                problems.append(str(exc))
        return MediaReport(not problems, "; ".join(problems) if problems else None,
                           before, after)

    def _decode_smoke(self, path: str | Path) -> None:
        """仅解码第一个音频流，确保纯音频容器不会误走视频流。"""
        cmd = [self.ffmpeg, "-v", "error", "-hide_banner", "-t", "2",
               "-i", str(path), "-map", "0:a:0", "-f", "null", "-"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        if out.returncode != 0:
            raise AdapterError(f"音频无法解码（不可播放）: {out.stderr.strip()[:200]}")

    @staticmethod
    def _signature(probe: dict) -> dict:
        fmt = probe.get("format", {})
        streams = [
            {"codec_name": stream.get("codec_name"),
             "sample_rate": stream.get("sample_rate"),
             "channels": stream.get("channels")}
            for stream in probe.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]
        try:
            duration = float(fmt.get("duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return {"format_name": fmt.get("format_name"), "duration": duration,
                "audio_stream_count": len(streams), "audio_streams": streams}
