"""视频（MP4）元数据适配器 —— 本模块由视频负责人实现（开发手册 §6.4 / §9.4）。

- 载体：ExifTool 写 XMP-aigc:AIGC 进 MP4 的 moov/meta 区域，不转码（已实测通过）。
- 媒体完整性（§9.4）：ffprobe 对比写入前后的时长/分辨率/轨道/编解码器，
  编解码器信息逐项一致即判定未发生非预期变化；再以 ffmpeg 解码前 2 秒做
  "可播放"冒烟（§15.5.8），解码失败即判失败。
- 输出任务必须携带 carrier 标识（§6.4）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..core import bmff
from ..core.jobs import MODALITY_VIDEO
from .base import AdapterError, BaseAdapter, MediaReport, fingerprint_of


class Mp4Adapter(BaseAdapter):
    carrier_id = "mp4-aigc-v1"
    modality = MODALITY_VIDEO
    supported_mimes = ("video/mp4",)

    def media_integrity_check(self, src: str | Path, dst: str | Path,
                              duration_tolerance: float = 0.1) -> MediaReport:
        """写入前后媒体签名对比。任一探测失败或差异超容忍即判失败。"""
        before = self._signature(self._ffprobe_json(src))
        after = self._signature(self._ffprobe_json(dst))
        problems: list[str] = []

        if before["format_name"] != after["format_name"]:
            problems.append(f"容器格式变化: {before['format_name']} -> {after['format_name']}")
        if abs(before["duration"] - after["duration"]) > duration_tolerance:
            problems.append(f"时长变化: {before['duration']:.3f}s -> {after['duration']:.3f}s")
        if before["streams"] != after["streams"]:
            problems.append("轨道/编解码器/分辨率信息发生变化")
        if len(before["streams"]) == 0:
            problems.append("未探测到任何媒体轨道")
        if not problems:
            # 仅当结构与写入前一致时，才做解码冒烟（可播放性，§15.5.8）。
            try:
                self._decode_smoke(dst)
            except AdapterError as e:
                problems.append(str(e))

        return MediaReport(passed=not problems,
                           reason="; ".join(problems) if problems else None,
                           before=before, after=after)

    def _decode_smoke(self, path: str | Path) -> None:
        """解码第一个视频流的前 2 秒（或整段，若更短），确认结果文件可播放。
        限定时间以控制大文件成本；参数数组调用，禁止拼接 shell（§13）。
        """
        cmd = [self.ffmpeg, "-v", "error", "-hide_banner", "-t", "2",
               "-i", str(path), "-map", "0:v:0", "-f", "null", "-"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300)
        if out.returncode != 0:
            raise AdapterError(f"视频无法解码（不可播放）: {out.stderr.strip()[:200]}")

    def content_fingerprint(self, path: str | Path) -> tuple[str, str]:
        """媒体字节指纹：顶层 ``mdat`` 载荷的 sha256（编号登记库用，§6.1 b）。

        早先这里用的是 ffprobe 摘要（容器格式 + 时长 + 轨道/编解码/分辨率），
        它只到"还是同一段媒体"这一粒度：**两份时长与轨道相同的不同片子算出来
        一模一样**，登记库核对"是不是同一份内容"时形同虚设。

        改用 mdat 载荷后能真正区分内容，理由见 ``bmff.media_payload_digest``：
        写标识只动 moov/udta 与新增 uuid box，mdat 载荷原样复制，所以它跨写入
        稳定；而 moov 里装着标识本身，不能进指纹。

        **仍然比图片的像素哈希弱一档**：它证明"媒体字节没变"，但不做解码，
        因此证明不了"每一帧都能正常解出来"。后者由 ``media_integrity_check``
        的前后对比 + 解码冒烟承担，两者分工不同，缺一不可。

        没有 mdat 可读时（结构损坏）退回 ffprobe 摘要：宁可给一个弱指纹，
        也不要让打标流程因为算不出指纹而整个失败。
        """
        digest = bmff.media_payload_digest(path)
        if digest is not None:
            return digest, "stream"
        return fingerprint_of(self._signature(self._ffprobe_json(path))), "stream"

    @staticmethod
    def _signature(probe: dict) -> dict:
        fmt = probe.get("format", {})
        streams = [{
            "codec_type": s.get("codec_type"),
            "codec_name": s.get("codec_name"),
            "width": s.get("width"),
            "height": s.get("height"),
            "sample_rate": s.get("sample_rate"),
            "channels": s.get("channels"),
        } for s in probe.get("streams", [])]
        try:
            duration = float(fmt.get("duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return {"format_name": fmt.get("format_name"), "duration": duration, "streams": streams}
