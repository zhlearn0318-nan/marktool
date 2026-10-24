"""音频（M4A）文件元数据隐式标识。

ExifTool 12.x 不能写 WAV / MP3。AAC in M4A 与 MP4 一样落在 XMP-aigc:AIGC，
因此直接复用 :class:`app.adapters.base.BaseAdapter` 的写入、删除和全标签读取。
对应附录 F.2 的音频文件元数据示例，以及第 6.1 条“每个内容文件一份标识”。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..adapters.base import AdapterError, BaseAdapter, MediaReport


class AudioAdapter(BaseAdapter):
    carrier_id = "m4a-aigc-v1"
    modality = "audio"
    supported_mimes = ("audio/mp4",)

    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        before = self._signature(self._ffprobe_json(src))
        after = self._signature(self._ffprobe_json(dst))
        problems: list[str] = []
        if not before["streams"]:
            problems.append("未探测到音频轨道")
        if before["streams"] != after["streams"]:
            problems.append("音频轨道、采样率或编解码器发生变化")
        if abs(before["duration"] - after["duration"]) > duration_tolerance:
            problems.append(
                f"时长变化: {before['duration']:.3f}s -> {after['duration']:.3f}s"
            )
        if not problems:
            try:
                self._decode_smoke(dst)
            except AdapterError as exc:
                problems.append(str(exc))
        return MediaReport(
            passed=not problems,
            reason="; ".join(problems) if problems else None,
            before=before,
            after=after,
        )

    def _decode_smoke(self, path: str | Path) -> None:
        cmd = [self.ffmpeg, "-v", "error", "-hide_banner", "-t", "2",
               "-i", str(path), "-map", "0:a:0", "-f", "null", "-"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        if out.returncode != 0:
            raise AdapterError(f"音频无法解码: {out.stderr.strip()[:200]}")

    @staticmethod
    def _signature(probe: dict) -> dict:
        fmt = probe.get("format", {})
        streams = [{
            "codec_type": stream.get("codec_type"),
            "codec_name": stream.get("codec_name"),
            "sample_rate": stream.get("sample_rate"),
            "channels": stream.get("channels"),
        } for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
        try:
            duration = float(fmt.get("duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return {"format_name": fmt.get("format_name"), "duration": duration, "streams": streams}
