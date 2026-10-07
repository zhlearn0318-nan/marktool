"""MP4 已有元数据隐式标识 · 合规检测器（方案文档 §三/§四）。

只读检测：扫描 MP4 全部 AIGC 候选 → 按 GB 45438—2025 附录 E 判定 → 输出
conclusion / reason_code / issues / repairability / c2pa_presence /
media_status / confidence。

格式无关的公共件（结论词表、国标结构判定、报告组装）已在
``app/core/inspector_common.py``，本模块**重导出**同名符号，供既有调用方与测试
继续 ``from app.core.inspector import ...``——抽出只是让文档/图片检测器复用同一份
判定，不改任何一条既有结论文案或 reason_code。

与写入器共用同一套 Schema 与读取器（开发手册 §14）：字段常量/枚举/解析复用
aigc.py，全标签扫描复用 reader.py。本模块不承担修复（RepairPlanner /
metadata-repair-jobs / 审计存储 属后续阶段），repairability 仅作报告字段。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import bmff
from .inspector_common import (
    C2PA_ABSENT,
    C2PA_INDETERMINATE,
    C2PA_PRESENT,
    CONCLUSION_COMPLIANT,
    CONCLUSION_INDETERMINATE,
    CONCLUSION_NONCOMPLIANT,
    CONCLUSION_NOT_FOUND,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    IDENTITY_FIELDS as _IDENTITY_FIELDS,
    MEDIA_DEGRADED,
    MEDIA_OK,
    MEDIA_UNREADABLE,
    REPAIR_AUTO,
    REPAIR_FORBIDDEN,
    REPAIR_HUMAN,
    Issue,
    LegacyRule,
    bmff_not_applicable,
    build_report,
    candidate_entry as _candidate_entry,
    classify_records,
    exiftool_version as _exiftool_version_of,
    field_disagreement as _field_disagreement,
    is_canonical_tag as _is_canonical_tag,
    json_fault_fragment as _json_fault_fragment,
    registry_block as _registry_block,
    same_fields as _same_fields,
    single_repairability as _single_repairability_impl,
    structural_issues,
)
from .reader import read_aigc_records
from ..adapters import AdapterError
from ..adapters.video import Mp4Adapter

DETECTOR_VERSION = "mp4-compliance-inspector/0.1.0"

# MP4 的旧载体：QuickTime:Comment（©cmt 数据原子），存在正式迁移规则
_MP4_LEGACY = LegacyRule(detects=lambda tag: "QuickTime" in tag,
                         name="QuickTime:Comment",
                         single_note="存在正式迁移规则，不影响国标结论")

__all__ = [
    "C2PA_ABSENT", "C2PA_INDETERMINATE", "C2PA_PRESENT",
    "CONCLUSION_COMPLIANT", "CONCLUSION_INDETERMINATE", "CONCLUSION_NONCOMPLIANT",
    "CONCLUSION_NOT_FOUND", "CONFIDENCE_HIGH", "CONFIDENCE_LOW",
    "DETECTOR_VERSION", "Issue", "MEDIA_DEGRADED", "MEDIA_OK", "MEDIA_UNREADABLE",
    "MetadataComplianceInspector", "REPAIR_AUTO", "REPAIR_FORBIDDEN", "REPAIR_HUMAN",
    "structural_issues",
]


class MetadataComplianceInspector:
    """统一完成候选扫描、国标判断、问题编码、媒体/box/C2PA 检查。"""

    # 子类（图片检测器）覆盖此值，报告据此区分两种模态的检测器版本
    detector_version = DETECTOR_VERSION

    def __init__(self, exiftool: str = "exiftool", ffprobe: str = "ffprobe",
                 ffmpeg: str = "ffmpeg", exiftool_config: str | None = None):
        self._exiftool = exiftool
        self._exiftool_config = exiftool_config
        self._adapter = Mp4Adapter(exiftool=exiftool, ffprobe=ffprobe, ffmpeg=ffmpeg,
                                   exiftool_config=exiftool_config)

    # ---- 对外入口 ----
    def inspect(self, path: str | Path, *, file_name: str | None = None,
                size_bytes: int | None = None, sha256: str | None = None,
                request_id: str | None = None,
                registry: Callable[[str], bool] | None = None) -> dict:
        """对单个 MP4 做只读合规检测，返回报告 dict。文件内容问题一律落成结论。"""
        path = Path(path)
        started = time.monotonic()

        probe = bmff.scan_top_level(path)
        c2pa = self._c2pa_presence(probe)
        media = self._probe_media(path, probe)

        # moov 缺失/不可读 → 元数据载体整体不可靠，无法确认是否存有标识（§2.4/§3）。
        if not probe.has_moov:
            return self._build(
                probe, media, candidates=[], issues=[
                    Issue("UNREADABLE_CARRIER", "error",
                          "MP4 缺少可读取的 moov 元数据载体（结构损坏或文件截断）")],
                conclusion=CONCLUSION_INDETERMINATE, reason_code="UNREADABLE_CARRIER",
                repairability=REPAIR_HUMAN, c2pa=c2pa, confidence=CONFIDENCE_LOW,
                registry=self._registry_block(registry, None),
                file_name=file_name, size_bytes=size_bytes, sha256=sha256,
                request_id=request_id, elapsed_ms=int((time.monotonic() - started) * 1000))

        # ExifTool 全标签扫描（复用 reader，§14）。工具进程失败向上抛（API 转 500）。
        records = read_aigc_records(str(path), exiftool=self._exiftool,
                                    config=self._exiftool_config)

        candidates = [_candidate_entry(r) for r in records]
        classified = self._classify(records)

        # 载体可靠性升级（§2.4 / 方案 md §三-6、md §2.4）：moov 在但文件整体
        # 不可靠时，"未检出标识"并非确定结论——损坏载体可能本应藏有标识，却因
        # ExifTool 宽容地读成空而漏报。→ 升级 indeterminate，交人工复核，禁止
        # 当作干净的 not_found（md：不得误判为合规/干净）。
        if classified["conclusion"] == CONCLUSION_NOT_FOUND:
            blocked = self._carrier_unreadable_issue(probe, media)
            if blocked is not None:
                return self._build(
                    probe, media, candidates=[], issues=[blocked],
                    conclusion=CONCLUSION_INDETERMINATE,
                    reason_code=blocked.code, repairability=REPAIR_HUMAN,
                    c2pa=c2pa, confidence=CONFIDENCE_LOW,
                    registry=self._registry_block(registry, None),
                    file_name=file_name, size_bytes=size_bytes, sha256=sha256,
                    request_id=request_id,
                    elapsed_ms=int((time.monotonic() - started) * 1000))

            # 元数据承载位被清零：ExifTool 读不到任何记录，但 ©cmt 数据原子仍在
            # 且内容全 0 —— 若其曾写入标识，损坏载体不得当"无标识"确定结论（§2.4）。
            wiped = bmff.scan_wiped_comment_atoms(path)
            if wiped:
                detail = "；".join(
                    f"{w['path']} @ 偏移 {w['offset']}（{w['payload_size']}B 全 0）"
                    for w in wiped)
                return self._build(
                    probe, media, candidates=[], issues=[
                        Issue("METADATA_REGION_WIPED", "error",
                              "检测到 QuickTime 注释数据原子被清零不可读：" + detail
                              + "。该元数据承载位已损坏，若曾写入 AIGC 标识则无法"
                                "确认，需人工复核（不得按\"无标识\"定论）", None)],
                    conclusion=CONCLUSION_INDETERMINATE,
                    reason_code="METADATA_REGION_WIPED", repairability=REPAIR_HUMAN,
                    c2pa=c2pa, confidence=CONFIDENCE_LOW,
                    registry=self._registry_block(registry, None),
                    file_name=file_name, size_bytes=size_bytes, sha256=sha256,
                    request_id=request_id,
                    elapsed_ms=int((time.monotonic() - started) * 1000))

        parsed_single = next((r.aigc for r in records
                              if len(records) == 1 and r.aigc is not None), None)
        produce_id = parsed_single.get("ProduceID") if parsed_single else None
        registry_block = self._registry_block(registry, produce_id)

        report = self._build(
            probe, media, candidates=candidates, issues=classified["issues"],
            conclusion=classified["conclusion"], reason_code=classified["reason_code"],
            repairability=classified["repairability"], c2pa=c2pa,
            confidence=classified["confidence"], registry=registry_block,
            file_name=file_name, size_bytes=size_bytes, sha256=sha256,
            request_id=request_id, elapsed_ms=int((time.monotonic() - started) * 1000))
        return report

    # ---- 分类 ----
    def _classify(self, records: list) -> dict:
        """核心结论：not_found / noncompliant(多份|单份结构错) / compliant。

        实现在 ``inspector_common.classify_records``（各模态共用一份），这里只
        绑定 MP4 的旧载体规则。
        """
        return classify_records(records, _MP4_LEGACY)

    def _classify_many(self, records: list) -> dict:
        return classify_records(records, _MP4_LEGACY)

    @staticmethod
    def _field_disagreement(ra, rb, a: dict, b: dict) -> Issue:
        return _field_disagreement(ra, rb, a, b)

    @staticmethod
    def _single_repairability(inner: dict) -> str:
        return _single_repairability_impl(inner)

    # ---- 媒体 / C2PA ----
    @staticmethod
    def _carrier_unreadable_issue(probe: bmff.BmffProbe, media: dict) -> Issue | None:
        """moov 存在但载体整体不可靠时，返回一条 UNREADABLE_CARRIER 问题。

        仅在"未检出任何记录"（would-be not_found）时调用。任一信号说明元数据
        读取结果不足以支撑"本文件没有标识"这一确定结论，就交人工复核：
        1. 顶层 box 越界/非法尺寸（parse_ok=False，moov 声明尺寸可信度坍塌）；
        2. ffprobe 无可枚举媒体流（moov 内部 trak 损坏）；
        3. 解码冒烟失败（视频数据损坏，文件整体不可用）；
        4. 有 moov 却缺 ftyp/mdat（结构异常，头部或主体被截）。
        返回 None 表示载体可靠，可以信任 not_found。
        """
        if not probe.parse_ok:
            return Issue(
                "UNREADABLE_CARRIER", "error",
                "MP4 顶层 box 结构越界/非法（moov 存在但损坏或截断），元数据读取"
                "结果不可作为\"无标识\"依据")
        if media.get("media_status") == MEDIA_UNREADABLE:
            return Issue(
                "UNREADABLE_CARRIER", "error",
                "moov 元数据载体存在但媒体/流不可解析（内部结构损坏），无法确认是否存有标识")
        if media.get("decode_smoke") == "failed":
            return Issue(
                "UNREADABLE_CARRIER", "error",
                "视频流无法解码（媒体数据损坏），无法确认是否存有标识")
        if probe.has_moov and (not probe.has_mdat or not probe.has_ftyp):
            return Issue(
                "UNREADABLE_CARRIER", "error",
                "关键 box（ftyp/mdat）缺失，MP4 结构异常，无法确认是否存有标识")
        return None

    def _probe_media(self, path: Path, probe: bmff.BmffProbe) -> dict:
        """媒体状态探测（§2.4/§3.2）：ffprobe 签名 + 解码冒烟 + box 结构。"""
        box_bad = (probe.error is not None or not probe.parse_ok
                   or not (probe.has_ftyp and probe.has_moov and probe.has_mdat))
        try:
            p = self._adapter._ffprobe_json(path)
        except AdapterError as e:
            return {"media_status": MEDIA_UNREADABLE, "format_name": None,
                    "duration": None, "streams": [], "has_video": False,
                    "probe_error": str(e)[:200], "decode_smoke": "skipped"}

        fmt = p.get("format", {})
        streams = p.get("streams", []) or []
        has_video = any(s.get("codec_type") == "video" for s in streams)
        stream_summary = [{"codec_type": s.get("codec_type"),
                           "codec_name": s.get("codec_name"),
                           "width": s.get("width"), "height": s.get("height")}
                          for s in streams]
        media: dict = {
            "media_status": None, "format_name": fmt.get("format_name"),
            "duration": fmt.get("duration"), "streams": stream_summary,
            "has_video": has_video, "probe_error": None, "decode_smoke": "ok",
        }
        if not streams:
            media.update({"media_status": MEDIA_UNREADABLE, "decode_smoke": "skipped"})
            return media
        if not has_video:
            media.update({"media_status": MEDIA_DEGRADED, "decode_smoke": "skipped",
                          "note": "无视频轨，跳过解码冒烟"})
            return media
        try:
            self._adapter._decode_smoke(path)
        except AdapterError as e:
            media.update({"media_status": MEDIA_DEGRADED, "decode_smoke": "failed",
                          "decode_error": str(e)[:200]})
            return media
        media["media_status"] = MEDIA_DEGRADED if box_bad else MEDIA_OK
        return media

    @staticmethod
    def _c2pa_presence(probe: bmff.BmffProbe) -> str:
        """§7 C2PA 存在性：只判存在，不验签。"""
        if not probe.parse_ok:
            return C2PA_INDETERMINATE          # box 树不可信 → 无法判断
        return C2PA_PRESENT if probe.c2pa_uuid else C2PA_ABSENT

    # ---- 报告 ----
    @staticmethod
    def _registry_block(registry: Callable[[str], bool] | None,
                        produce_id: str | None) -> dict:
        return _registry_block(registry, produce_id)

    def _build(self, probe: bmff.BmffProbe, media: dict, *, candidates: list,
               issues: list[Issue], conclusion: str, reason_code: str | None,
               repairability: str | None, c2pa: str, confidence: str,
               registry: dict, file_name: str | None, size_bytes: int | None,
               sha256: str | None, request_id: str | None, elapsed_ms: int,
               detected_mime: str = "video/mp4",
               bmff_block: dict | None = None) -> dict:
        """组装报告。图片/文档检测器复用公共 ``build_report``，用 detected_mime /
        bmff_block 覆盖 MP4 专有字段，保证各模态产出**同构**报告。"""
        return build_report(
            bmff=bmff_block if bmff_block is not None else probe.to_dict(),
            media=media, candidates=candidates, issues=issues, conclusion=conclusion,
            reason_code=reason_code, repairability=repairability, c2pa=c2pa,
            confidence=confidence, registry=registry, file_name=file_name,
            size_bytes=size_bytes, sha256=sha256, request_id=request_id,
            elapsed_ms=elapsed_ms, detected_mime=detected_mime,
            detector_version=self.detector_version,
            exiftool_version_value=self._exiftool_version())

    def _exiftool_version(self) -> str | None:
        return _exiftool_version_of(self._exiftool)
