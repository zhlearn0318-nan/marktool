"""载体适配器基类：公共的 XMP-aigc 读写与旧标识移除。

设计（开发手册 §4.5 / §6.3 / §6.4）：
- 各格式统一写 XMP-aigc:AIGC（团队验证的载体），读回复用 reader.py。
- 操作一律在"副本"上进行（src 拷贝到 dst），绝不修改原文件（§4.4）。
- ExifTool 用参数数组调用，禁止拼接 shell（§13）。
- 删除旧标识后必须回读确认数量为零（§9.3）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..core import aigc
from ..core.reader import AIGCRecord, read_aigc_records


class AdapterError(Exception):
    pass


@dataclass
class MediaReport:
    """媒体完整性校验结果（§9.4）。before/after 为媒体签名，供审计记录。"""
    passed: bool
    reason: str | None = None
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)


class BaseAdapter:
    carrier_id = ""
    modality = ""
    supported_mimes: tuple[str, ...] = ()

    def __init__(self, exiftool: str = "exiftool", ffprobe: str = "ffprobe",
                 ffmpeg: str = "ffmpeg", exiftool_config: str | None = None):
        self.exiftool = exiftool
        self.ffprobe = ffprobe
        self.ffmpeg = ffmpeg
        self.exiftool_config = exiftool_config

    # ---- ExifTool 封装 ----
    def _exiftool_cmd(self, args: list[str], file_path: str) -> str:
        cmd = [self.exiftool]
        if self.exiftool_config:
            cmd += ["-config", self.exiftool_config]
        cmd += args + [file_path]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        if out.returncode != 0:
            raise AdapterError(f"exiftool 失败: {out.stderr.strip()[:200]}")
        return out.stdout

    # ---- 已有标识检测（与检测器共用 reader）----
    def detect_existing(self, path: str | Path) -> list[AIGCRecord]:
        return read_aigc_records(str(path), exiftool=self.exiftool, config=self.exiftool_config)

    # ---- 写入（各格式统一 XMP-aigc:AIGC）----
    def write_metadata(self, src: str | Path, dst: str | Path, aigc_obj: dict) -> None:
        shutil.copyfile(src, dst)
        raw = aigc.serialize_aigc(aigc_obj)
        self._exiftool_cmd(["-overwrite_original", "-XMP-aigc:AIGC=" + raw], str(dst))
        records = self.detect_existing(dst)
        if not records:
            raise AdapterError("写入后未能在结果文件中读到 AIGC 标识")

    # ---- 整体替换（§9.3）：移除所有可识别的旧 AIGC 标识 ----
    def remove_aigc(self, src: str | Path, dst: str | Path) -> None:
        shutil.copyfile(src, dst)
        records = self.detect_existing(dst)
        if not records:
            return
        for rec in records:
            self._exiftool_cmd(["-overwrite_original", self._delete_arg_for(rec.tag_key)], str(dst))
        remaining = self.detect_existing(dst)
        if remaining:
            raise AdapterError(f"移除旧标识后仍存在 {len(remaining)} 处 AIGC 记录")

    @staticmethod
    def _delete_arg_for(tag_key: str) -> str:
        if tag_key == "XMP:AIGC":
            return "-XMP-aigc:AIGC="
        if ":" in tag_key:
            group, tag = tag_key.split(":", 1)
            return f"-{group}:{tag}="
        return f"-{tag_key}="

    # ---- 媒体完整性（子类实现）----
    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        raise NotImplementedError

    # ---- ffprobe 工具 ----
    def _ffprobe_json(self, path: str | Path) -> dict:
        cmd = [self.ffprobe, "-v", "error",
               "-show_entries", "format=format_name,duration"
               ":stream=codec_type,codec_name,width,height,sample_rate,channels",
               "-of", "json", str(path)]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        if out.returncode != 0:
            raise AdapterError(f"ffprobe 无法探测文件: {out.stderr.strip()[:200]}")
        return json.loads(out.stdout or "{}")
