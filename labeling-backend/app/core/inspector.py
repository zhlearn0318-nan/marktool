"""MP4 已有元数据隐式标识 · 合规检测器（方案文档 §三/§四）。

只读检测：扫描 MP4 全部 AIGC 候选 → 按 GB 45438—2025 附录 E 判定 → 输出
conclusion / reason_code / issues / repairability / c2pa_presence /
media_status / confidence。

判定原则（方案文档 §二）：
- §2.1 只认国标：外层 AIGC、七字段、Label 枚举、仅一份、首次写入关系。
- §2.2 项目字符约束不是国标不合规：范围外字符记 CHARSET warn，不翻转结论，
  因此不复用写入器的 validate_aigc（其把字符约束当 error）。
- §2.4 媒体状态与元数据结论分离：media_status 独立输出；载体整体不可读时
  结论 indeterminate，不被"顺带判不合规"。

与写入器共用同一套 Schema 与读取器（开发手册 §14）：字段常量/枚举/解析复用
aigc.py，全标签扫描复用 reader.py。本模块不承担修复（RepairPlanner /
metadata-repair-jobs / 审计存储 属后续阶段），repairability 仅作报告字段。
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import aigc, bmff
from .reader import read_aigc_records
from ..adapters import AdapterError
from ..adapters.video import Mp4Adapter

# ---- 结论与状态取值（§4）----
CONCLUSION_COMPLIANT = "compliant"
CONCLUSION_NONCOMPLIANT = "noncompliant"
CONCLUSION_NOT_FOUND = "not_found"
CONCLUSION_INDETERMINATE = "indeterminate"

MEDIA_OK = "ok"
MEDIA_DEGRADED = "degraded"
MEDIA_UNREADABLE = "unreadable"

C2PA_ABSENT = "absent"
C2PA_PRESENT = "present_unverified"
C2PA_INDETERMINATE = "indeterminate"

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"

REPAIR_AUTO = "auto_fixable"
REPAIR_HUMAN = "needs_human"
REPAIR_FORBIDDEN = "forbidden"

# 四身份字段（非空约束；ReservedCode1/2 允许空字符串）
_IDENTITY_FIELDS = ("ContentProducer", "ProduceID", "ContentPropagator", "PropagateID")

DETECTOR_VERSION = "mp4-compliance-inspector/0.1.0"


@dataclass
class Issue:
    """一条问题。severity: error（判 noncompliant）/ warn / info。"""
    code: str
    severity: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.field:
            d["field"] = self.field
        return d


def _not_str(field: str) -> Issue:
    return Issue("MISSING_FIELD", "error", f"字段 {field} 必须是字符串", f"AIGC.{field}")


def structural_issues(inner: dict) -> list[Issue]:
    """国标专属结构判定（§2.1），与写入器 validate_aigc 的字符约束解耦。

    返回 issues；error 级表示国标不合规，warn 级（CHARSET / FIRST_WRITE_MISMATCH）
    不翻转结论。
    """
    issues: list[Issue] = []

    unknown = sorted(set(inner) - set(aigc.REQUIRED))
    if unknown:
        issues.append(Issue("UNKNOWN_FIELD", "error",
                            "标准对象外存在字段: " + ", ".join(unknown), "AIGC"))

    for f in aigc.FIELD_ORDER:
        if f not in inner:
            issues.append(Issue("MISSING_FIELD", "error", f"缺失必填字段 {f}", f"AIGC.{f}"))
            continue
        v = inner[f]
        if f == "Label":
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                # 数值 1/2/3 → 语义唯一，可确定性转字符串（§5.1 可确定修复）
                if str(int(v)) in aigc.LABEL_VALUES:
                    issues.append(Issue("BAD_LABEL", "error",
                                        f"Label 为数值 {v}，需转为字符串 \"{int(v)}\"（可确定性修复）",
                                        "AIGC.Label"))
                    continue
                issues.append(Issue("BAD_LABEL", "error", f"Label 必须是字符串 1、2 或 3", "AIGC.Label"))
                continue
            if not isinstance(v, str):
                issues.append(Issue("BAD_LABEL", "error", "Label 必须是字符串 1、2 或 3", "AIGC.Label"))
            elif v not in aigc.LABEL_VALUES:
                issues.append(Issue("BAD_LABEL", "error",
                                    f"Label 取值 \"{v}\" 非法，必须是字符串 1、2 或 3", "AIGC.Label"))
            continue
        if not isinstance(v, str):
            issues.append(_not_str(f))
            continue
        if f in _IDENTITY_FIELDS and not v:
            issues.append(Issue("MISSING_FIELD", "error", f"字段 {f} 不能为空", f"AIGC.{f}"))

    # 字符集（§2.2）：只给 warn，不判 noncompliant。
    charset_fields: list[str] = []
    for f in aigc.FIELD_ORDER:
        v = inner.get(f)
        if isinstance(v, str) and any(not aigc.allowed_char(c) for c in v):
            charset_fields.append(f)
    if charset_fields:
        issues.append(Issue(
            "CHARSET", "warn",
            "字段值含国标规定字符范围外的字符，需人工复核: " + ", ".join(charset_fields)))

    # 首次写入关系（§2.1 第 5 点）：仅当身份字段齐全且确实不等时提示，不武断判不合规。
    both = {f: inner.get(f) for f in ("ContentProducer", "ProduceID",
                                      "ContentPropagator", "PropagateID")}
    if all(isinstance(v, str) and v for v in both.values()):
        if not aigc.first_write_consistent(inner):
            issues.append(Issue(
                "FIRST_WRITE_MISMATCH", "warn",
                "传播方 ≠ 制作者（或传播编号 ≠ 制作编号）：可能是合法的二次传播，"
                "也可能首次写入不一致，需登记库核对后确认", None))

    # 稳定排序：error 在前，其余按追加顺序。
    return sorted(issues, key=lambda i: (i.severity != "error",))


def _candidate_entry(rec) -> dict:
    entry: dict = {"tag": rec.tag_key, "parseable": rec.aigc is not None}
    if rec.location:
        entry["location"] = rec.location
    if rec.raw:
        entry["raw_preview"] = rec.raw[:120]
    if rec.aigc is not None:
        entry["parsed_fields"] = [f for f in aigc.FIELD_ORDER if f in rec.aigc]
    return entry


def _short(v) -> str:
    """字段值短展示（诊断文案用），超长截断。"""
    s = v if isinstance(v, str) else str(v)
    return s[:20] + "…" if len(s) > 20 else s


def _same_fields(a: dict, b: dict) -> bool:
    """两份解析出的内层 AIGC 是否七个字段完全一致（用于判断是否真冲突）。"""
    return all(a.get(f) == b.get(f) for f in aigc.FIELD_ORDER)


def _json_fault_fragment(raw: str) -> str | None:
    """把 JSON 解析失败位置翻译成诊断文案片段；失败返回 None（绝不抛）。

    用 json.JSONDecodeError 报告的首个失败字符位置 pos，结合 §5.2 七个字段在
    原文里的键名区间，指出卡在哪一字段的值段 / 结构位置。
    """
    try:
        json.loads(raw)
        return None                     # 竟然能解析，无需定位
    except json.JSONDecodeError as e:
        pos = e.pos
    except Exception:
        return None
    if not raw:
        return "（值为空）"
    # 截断型错误 pos 可能落在文档末尾甚至 len 之后（JSONDecodeError 的列号），
    # clamp 到文档内最后一个字符，避免误判"未落在任何字段值段"。
    ep = min(pos, len(raw) - 1)
    # 定位七个字段键在原文中的区间
    segs: list[tuple[str, int, int]] = []        # (字段名, 键起点, 下一键起点)
    for f in aigc.FIELD_ORDER:
        k = f'"{f}"'
        i = raw.find(k)
        if i == -1:
            continue
        nxt = min((raw.find(f'"{g}"', i + len(k)) for g in aigc.FIELD_ORDER
                   if raw.find(f'"{g}"', i + len(k)) >= 0),
                  default=len(raw))
        segs.append((f, i, nxt))
    if segs:
        segs.sort(key=lambda t: t[1])
        for f, ks, ve in segs:
            if ks <= ep < ve:
                return (f"（解析失败于第 {pos} 字符、字段 {f} 的值段内，"
                        f"距该字段键约 {ep - ks} 字符）")
        # ep 超出已知段：归到键起点不大于它的最近字段（截断在收尾处）
        tail = [s for s in segs if s[1] <= ep]
        if tail:
            f = tail[-1][0]
            return (f"（解析失败于第 {pos} 字符附近，处于字段 {f} 值段或其收尾处，"
                    f"疑 JSON 在此被截断）")
    return f"（解析失败于第 {pos} 字符附近，未落在任何字段值段，疑外层结构损坏）"


def _is_canonical_tag(tag_key: str) -> bool:
    """本系统指定写入位置（XMP-aigc:AIGC）与一般 *:AIGC 新载体。"""
    return tag_key.endswith(":AIGC")


class MetadataComplianceInspector:
    """统一完成候选扫描、国标判断、问题编码、媒体/box/C2PA 检查。"""

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
        """核心结论：not_found / noncompliant(多份|单份结构错) / compliant。"""
        if len(records) == 0:
            return {"conclusion": CONCLUSION_NOT_FOUND, "reason_code": None,
                    "issues": [], "repairability": None, "confidence": CONFIDENCE_HIGH}

        if len(records) > 1:
            return self._classify_many(records)

        rec = records[0]
        legacy_tag = "QuickTime" in rec.tag_key
        issues: list[Issue] = []
        if legacy_tag:
            loc = f"，位于{rec.location}" if rec.location else ""
            issues.append(Issue("LEGACY_CARRIER", "info",
                                "旧载体 QuickTime:Comment（存在正式迁移规则，不影响国标结论）"
                                + loc, rec.tag_key))

        # 无法解析出国标结构（JSON 错误或缺少外层大写 AIGC 对象）
        if rec.aigc is None:
            frag = _json_fault_fragment(rec.raw)
            loc = f"，位于{rec.location}" if rec.location else ""
            msg = ("元数据值无法解析为国标结构（JSON 非法或缺外层 AIGC 对象）"
                   + loc + (frag or ""))
            issues.append(Issue("BAD_JSON", "error", msg, rec.tag_key))
            return {"conclusion": CONCLUSION_NONCOMPLIANT, "reason_code": "BAD_JSON",
                    "issues": issues, "repairability": REPAIR_HUMAN,
                    "confidence": CONFIDENCE_LOW}

        st = structural_issues(rec.aigc)
        issues.extend(st)
        errors = [i for i in st if i.severity == "error"]
        if errors:
            priority = ("MISSING_FIELD", "BAD_LABEL", "UNKNOWN_FIELD", "BAD_JSON")
            reason = next((c for c in priority if any(i.code == c for i in errors)),
                          errors[0].code)
            return {"conclusion": CONCLUSION_NONCOMPLIANT, "reason_code": reason,
                    "issues": issues, "repairability": self._single_repairability(rec.aigc),
                    "confidence": CONFIDENCE_HIGH}

        # 无国标结构错误：结论 compliant（LEGACY_CARRIER/CHARSET/FIRST_WRITE 均为 warn 级）
        return {"conclusion": CONCLUSION_COMPLIANT, "reason_code": None,
                "issues": issues, "repairability": None,
                "confidence": CONFIDENCE_HIGH if not issues else CONFIDENCE_LOW}

    def _classify_many(self, records: list) -> dict:
        issues: list[Issue] = [
            Issue("DUPLICATE_RECORDS", "error",
                  f"检测到 {len(records)} 处 AIGC 标识，国标要求同一文件仅保留一份",
                  None)]
        legacy_present = any("QuickTime" in r.tag_key for r in records)
        if legacy_present:
            issues.append(Issue("LEGACY_CARRIER", "warn",
                                "其中包含旧载体 QuickTime:Comment，与新载体共存形成多份",
                                None))

        # 多份记录的逐字段位置不一致详情（新增，additive）：以规范新载体为基准，
        # 对比其余可解析记录，指出"哪两个位置、哪个字段值不同"，供定位"到底哪里出问题"。
        parsed = [(r, r.aigc) for r in records if r.aigc is not None]
        if len(parsed) >= 2:
            base_r, base_a = parsed[0]
            for r, a in parsed[1:]:
                if not _same_fields(base_a, a):
                    issues.append(self._field_disagreement(base_r, r, base_a, a))

        keyed = [(r.tag_key, r.raw) for r in records]
        if len(set(keyed)) == 1:
            repairability = REPAIR_AUTO          # 完全相同的重复标识
        else:
            canonical = [r for r in records if _is_canonical_tag(r.tag_key)]
            if len(canonical) == 1 and canonical[0].aigc is not None:
                repairability = REPAIR_AUTO      # 单一规范新载体 + 旧载体残留 → 去旧保新
            else:
                repairability = REPAIR_HUMAN     # 冲突/多份规范记录 → 人工选择
        return {"conclusion": CONCLUSION_NONCOMPLIANT, "reason_code": "DUPLICATE_RECORDS",
                "issues": issues, "repairability": repairability,
                "confidence": CONFIDENCE_LOW}

    @staticmethod
    def _field_disagreement(ra, rb, a: dict, b: dict) -> Issue:
        """构造一条"两处位置某字段不一致"的详细问题（warn 级，结论仍由多份决定）。"""
        loc_a = ra.location or ra.tag_key
        loc_b = rb.location or rb.tag_key
        diff = []
        for f in aigc.FIELD_ORDER:
            if f in a and f in b and a[f] != b[f]:
                diff.append(f"{f}: {_short(a[f])} vs {_short(b[f])}")
        return Issue(
            "FIELDS_DISAGREE", "warn",
            "多份标识内容不一致：" + "；".join(diff[:5])
            + f"。位置 A = {loc_a}，位置 B = {loc_b}（详见 candidates 各自 raw_preview）",
            None)

    @staticmethod
    def _single_repairability(inner: dict) -> str:
        """单份结构错误标识的修复可行性（§5.1）。"""
        # 身份/编号缺失 → 无法自动补（来源不明不猜测，§2.3）
        if any(not isinstance(inner.get(f), str) or not inner[f]
               for f in _IDENTITY_FIELDS):
            return REPAIR_HUMAN
        label = inner.get("Label")
        if isinstance(label, (int, float)) and not isinstance(label, bool):
            if str(int(label)) in aigc.LABEL_VALUES:
                return REPAIR_AUTO              # Label 数值 → 字符串，语义唯一
            return REPAIR_HUMAN
        if not isinstance(label, str) or label not in aigc.LABEL_VALUES:
            return REPAIR_HUMAN                 # Label 含义不明
        # 身份与 Label 均可确定：余下缺空值 ReservedCode / 多余未知字段可确定性修正
        return REPAIR_AUTO

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
    def _registry_block(self, registry: Callable[[str], bool] | None,
                        produce_id: str | None) -> dict:
        if registry is None:
            return {"mode": "skipped",
                    "reason": "本服务未登记该文件的来源指纹，未核对编号真伪"}
        if not produce_id:
            return {"mode": "skipped", "reason": "无解析出的 ProduceID，未核对"}
        try:
            known = bool(registry(produce_id))
        except Exception:
            known = False
        return {"mode": "local_produceid", "produce_id": produce_id,
                "known": known,
                "reason": "仅按 ProduceID 是否在本系统出现过核对；指纹级核对待登记库完善"}

    def _build(self, probe: bmff.BmffProbe, media: dict, *, candidates: list,
               issues: list[Issue], conclusion: str, reason_code: str | None,
               repairability: str | None, c2pa: str, confidence: str,
               registry: dict, file_name: str | None, size_bytes: int | None,
               sha256: str | None, request_id: str | None, elapsed_ms: int) -> dict:
        report: dict = {
            "request_id": request_id,
            "file_name": file_name,
            "detected_mime_type": "video/mp4",
            "size_bytes": size_bytes,
            "sha256": sha256,
            "record_count": len(candidates),
            "candidates": candidates,
            "issues": [i.to_dict() for i in issues],
            "conclusion": conclusion,
            "reason_code": reason_code,
            "repairability": repairability,
            "c2pa_presence": c2pa,
            "media_status": media["media_status"],
            "confidence": confidence,
            "media": media,
            "bmff": probe.to_dict(),
            "registry": registry,
            "detector_version": DETECTOR_VERSION,
            "exiftool_version": self._exiftool_version(),
            "elapsed_ms": elapsed_ms,
        }
        return report

    def _exiftool_version(self) -> str | None:
        try:
            out = subprocess.run([self._exiftool, "-ver"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace",
                                 timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        return (out.stdout or "").strip() or None
