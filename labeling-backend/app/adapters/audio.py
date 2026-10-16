"""M4A 与 MP3 音频元数据适配器。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TXXX

from ..core import aigc
from ..core.jobs import MODALITY_AUDIO
from ..core.reader import AIGCRecord
from .base import AdapterError, BaseAdapter, MediaReport


class _AudioAdapter(BaseAdapter):
    def content_fingerprint(self, path: str | Path) -> tuple[str, str]:
        """Hash compressed audio packets without ID3/XMP or container metadata."""
        input_options = ["-f", self.input_format] if self.input_format else []
        cmd = [self.ffmpeg, "-v", "error", *input_options, "-i", str(path), "-map", "0:a",
               "-c", "copy", "-f", "hash", "-hash", "sha256", "-"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterError(f"无法计算音频内容指纹: {exc}") from None
        digest = result.stdout.strip().removeprefix("SHA256=")
        if result.returncode or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AdapterError(f"无法计算音频内容指纹: {result.stderr.strip()[:200]}")
        return digest, "stream"


class M4aAdapter(_AudioAdapter):
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


class Mp3Adapter(_AudioAdapter):
    """通过 ID3v2 ``TXXX:AIGC`` 为 MP3 写入标识。

    ExifTool 目前不能可靠写 ID3v2 用户自定义文本帧，因此本适配器以
    Mutagen 精确处理描述名严格为 ``AIGC`` 的 TXXX；其他 ID3 帧不参与删除。
    """

    carrier_id = "mp3-id3v2-aigc-v1"
    modality = MODALITY_AUDIO
    supported_mimes = ("audio/mpeg",)
    # Large cover art can confuse automatic probing of otherwise valid short MP3s.
    # This adapter is selected only after verifying MPEG audio after the ID3 tag.
    input_format = "mp3"
    _AIGC_DESCRIPTION = "AIGC"

    def detect_existing(self, path: str | Path) -> list[AIGCRecord]:
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            return []
        except Exception as exc:
            raise AdapterError(f"无法读取 MP3 ID3v2 标签: {exc}") from None

        records: list[AIGCRecord] = []
        for frame in tags.getall("TXXX"):
            if frame.desc != self._AIGC_DESCRIPTION:
                continue
            # 多值 TXXX 不是本载体的合法写入结果；逐值返回，使上层唯一性保护拒绝它。
            for value in frame.text:
                raw = str(value)
                records.append(AIGCRecord(tag_key="ID3:TXXX:AIGC", raw=raw,
                                          aigc=aigc.parse_aigc(raw),
                                          location="MP3 文件开头的 ID3v2 TXXX 帧"))
        return records

    def write_metadata(self, src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
        shutil.copyfile(src, dst)
        raw = aigc.serialize_aigc(aigc_obj)
        tags = self._load_or_create_tags(dst)
        self._remove_aigc_frames(tags)
        tags.add(TXXX(encoding=3, desc=self._AIGC_DESCRIPTION, text=[raw]))
        self._save_tags(tags, dst)

        records = self.detect_existing(dst)
        if len(records) != 1 or records[0].raw != raw or records[0].aigc != aigc_obj:
            raise AdapterError("写入后 MP3 TXXX:AIGC 回读失败或不是唯一记录")

    def remove_aigc(self, src: str | Path, dst: str | Path) -> None:
        shutil.copyfile(src, dst)
        tags = self._load_or_create_tags(dst)
        self._remove_aigc_frames(tags)
        self._save_tags(tags, dst)
        if self.detect_existing(dst):
            raise AdapterError("移除旧 MP3 TXXX:AIGC 后仍存在 AIGC 记录")

    @staticmethod
    def _save_tags(tags: ID3, path: str | Path) -> None:
        try:
            # 固定 v2.4/UTF-8；Mutagen 仅重写 ID3 标签区，不重编码 MPEG 音频。
            tags.save(str(path), v2_version=4)
        except Exception as exc:
            raise AdapterError(f"写入 MP3 ID3v2 标签失败: {exc}") from None

    @staticmethod
    def _remove_aigc_frames(tags: ID3) -> None:
        for frame in list(tags.getall("TXXX")):
            if frame.desc == Mp3Adapter._AIGC_DESCRIPTION:
                del tags[frame.HashKey]

    @staticmethod
    def _load_or_create_tags(path: str | Path) -> ID3:
        try:
            return ID3(str(path))
        except ID3NoHeaderError:
            return ID3()
        except Exception as exc:
            raise AdapterError(f"无法读取 MP3 ID3v2 标签: {exc}") from None

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
        cmd = [self.ffmpeg, "-v", "error", "-hide_banner", "-t", "2",
               "-f", self.input_format, "-i", str(path), "-map", "0:a:0", "-f", "null", "-"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        if out.returncode != 0:
            raise AdapterError(f"音频无法解码（不可播放）: {out.stderr.strip()[:200]}")

    @staticmethod
    def _signature(probe: dict) -> dict:
        return M4aAdapter._signature(probe)
