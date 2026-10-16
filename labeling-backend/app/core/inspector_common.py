"""合规检测的**格式无关**公共件（结论词表 · 国标结构判定 · 报告组装）。

本模块从 ``app/core/inspector.py`` 机械抽出，供三种模态的检测器共用：
MP4（``inspector.py``）、图片（``image_bridge.py``）、文档（``document_inspector.py``）。
抽出目的是让「一份国标判定的写法」只有一处——文档检测器不得复制粘贴一套
``structural_issues``，否则 MP4 改了口径、文档没改，同一份 AIGC JSON 会得到
两个答案。

判定原则（合规方案 §二）：
- §2.1 只认国标：外层 AIGC、七字段、Label 枚举、仅一份、首次写入关系。
- §2.2 项目字符约束不是国标不合规：范围外字符记 CHARSET warn，不翻转结论，
  因此不复用写入器的 validate_aigc（其把字符约束当 error）。
- §2.4 媒体状态与元数据结论分离：media_status 独立输出；载体整体不可读时
  结论 indeterminate，不被"顺带判不合规"。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from . import aigc

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
IDENTITY_FIELDS = ("ContentProducer", "ProduceID", "ContentPropagator", "PropagateID")

# 非 BMFF 格式（图片/文档）的 bmff 块占位：前端据此整行隐藏容器结构，
# 保留键名以维持报告形状一致（§4.5 同构报告）。
BMFF_NOT_APPLICABLE = {
    "applicable": False,
    "note": "该格式无 BMFF box 结构（BMFF 为 MP4 专有）",
    "has_ftyp": None,
    "has_moov": None,
    "has_mdat": None,
    "c2pa_uuid": [],
}

# 若某格式的报告需要自述"无 BMFF"，用本函数取一份副本，避免调用方改到共享字典。
def bmff_not_applicable(note: str | None = None) -> dict:
    block = dict(BMFF_NOT_APPLICABLE)
    if note:
        block["note"] = note
    return block


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


@dataclass(frozen=True)
class LegacyRule:
    """某个模态的"旧载体"识别规则与其文案。

    判定逻辑（LEGACY_CARRIER 只给 info/warn、不翻转结论）各模态一致，只有
    "哪种标签算旧载体、它叫什么"因格式而异。
    """
    detects: object          # Callable[[str], bool]
    name: str                # 人类可读的旧载体名，进提示文案
    single_note: str         # 单份命中时括号内的说明


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
        if f in IDENTITY_FIELDS and not v:
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


def candidate_entry(rec) -> dict:
    entry: dict = {"tag": rec.tag_key, "parseable": rec.aigc is not None}
    if rec.location:
        entry["location"] = rec.location
    if rec.raw:
        entry["raw_preview"] = rec.raw[:120]
    if rec.aigc is not None:
        entry["parsed_fields"] = [f for f in aigc.FIELD_ORDER if f in rec.aigc]
    return entry


def short(v) -> str:
    """字段值短展示（诊断文案用），超长截断。"""
    s = v if isinstance(v, str) else str(v)
    return s[:20] + "…" if len(s) > 20 else s


def same_fields(a: dict, b: dict) -> bool:
    """两份解析出的内层 AIGC 是否七个字段完全一致（用于判断是否真冲突）。"""
    return all(a.get(f) == b.get(f) for f in aigc.FIELD_ORDER)


def json_fault_fragment(raw: str) -> str | None:
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


def is_canonical_tag(tag_key: str) -> bool:
    """本系统指定写入位置（XMP-aigc:AIGC，读作 XMP:AIGC）与一般 *:AIGC 新载体。"""
    return tag_key.endswith(":AIGC")


def single_repairability(inner: dict) -> str:
    """单份结构错误标识的修复可行性（§5.1）。"""
    # 身份/编号缺失 → 无法自动补（来源不明不猜测，§2.3）
    if any(not isinstance(inner.get(f), str) or not inner[f]
           for f in IDENTITY_FIELDS):
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


def field_disagreement(ra, rb, a: dict, b: dict) -> Issue:
    """构造一条"两处位置某字段不一致"的详细问题（warn 级，结论仍由多份决定）。"""
    loc_a = ra.location or ra.tag_key
    loc_b = rb.location or rb.tag_key
    diff = []
    for f in aigc.FIELD_ORDER:
        if f in a and f in b and a[f] != b[f]:
            diff.append(f"{f}: {short(a[f])} vs {short(b[f])}")
    return Issue(
        "FIELDS_DISAGREE", "warn",
        "多份标识内容不一致：" + "；".join(diff[:5])
        + f"。位置 A = {loc_a}，位置 B = {loc_b}（详见 candidates 各自 raw_preview）",
        None)


def classify_records(records: list, legacy: "LegacyRule") -> dict:
    """核心结论：not_found / noncompliant(多份|单份结构错) / compliant。

    各模态共用这一份实现，只有"哪种标签算旧载体"因格式而异（``LegacyRule``）。
    分成两份实现的话，MP4 改了优先级、文档没改，同一份 AIGC JSON 会得到两个答案。
    """
    if len(records) == 0:
        return {"conclusion": CONCLUSION_NOT_FOUND, "reason_code": None,
                "issues": [], "repairability": None, "confidence": CONFIDENCE_HIGH}

    if len(records) > 1:
        return _classify_many(records, legacy)

    rec = records[0]
    issues: list[Issue] = []
    if legacy.detects(rec.tag_key):
        loc = f"，位于{rec.location}" if rec.location else ""
        issues.append(Issue("LEGACY_CARRIER", "info",
                            f"旧载体 {legacy.name}（{legacy.single_note}）" + loc,
                            rec.tag_key))

    # 无法解析出国标结构（JSON 错误或缺少外层大写 AIGC 对象）
    if rec.aigc is None:
        frag = json_fault_fragment(rec.raw)
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
                "issues": issues, "repairability": single_repairability(rec.aigc),
                "confidence": CONFIDENCE_HIGH}

    # 无国标结构错误：结论 compliant（LEGACY_CARRIER/CHARSET/FIRST_WRITE 均为 warn 级）
    return {"conclusion": CONCLUSION_COMPLIANT, "reason_code": None,
            "issues": issues, "repairability": None,
            "confidence": CONFIDENCE_HIGH if not issues else CONFIDENCE_LOW}


def _classify_many(records: list, legacy: "LegacyRule") -> dict:
    issues: list[Issue] = [
        Issue("DUPLICATE_RECORDS", "error",
              f"检测到 {len(records)} 处 AIGC 标识，国标要求同一文件仅保留一份",
              None)]
    if any(legacy.detects(r.tag_key) for r in records):
        issues.append(Issue("LEGACY_CARRIER", "warn",
                            f"其中包含旧载体 {legacy.name}，与新载体共存形成多份",
                            None))

    # 多份记录的逐字段位置不一致详情（新增，additive）：以规范新载体为基准，
    # 对比其余可解析记录，指出"哪两个位置、哪个字段值不同"，供定位"到底哪里出问题"。
    parsed = [(r, r.aigc) for r in records if r.aigc is not None]
    if len(parsed) >= 2:
        base_r, base_a = parsed[0]
        for r, a in parsed[1:]:
            if not same_fields(base_a, a):
                issues.append(field_disagreement(base_r, r, base_a, a))

    keyed = [(r.tag_key, r.raw) for r in records]
    if len(set(keyed)) == 1:
        repairability = REPAIR_AUTO          # 完全相同的重复标识
    else:
        # "规范"必须排除本格式自己认定的旧载体：Markdown 的旧载体 tag_key 恰好是
        # ``html-comment:AIGC``，同样满足 ``endswith(":AIGC")``，若一并计入，
        # "一份规范 + 一份遗留 → 去旧保新"这条最该自动修复的情形反而会掉进人工。
        canonical = [r for r in records
                     if is_canonical_tag(r.tag_key) and not legacy.detects(r.tag_key)]
        if len(canonical) == 1 and canonical[0].aigc is not None:
            repairability = REPAIR_AUTO      # 单一规范新载体 + 旧载体残留 → 去旧保新
        else:
            repairability = REPAIR_HUMAN     # 冲突/多份规范记录 → 人工选择
    return {"conclusion": CONCLUSION_NONCOMPLIANT, "reason_code": "DUPLICATE_RECORDS",
            "issues": issues, "repairability": repairability,
            "confidence": CONFIDENCE_LOW}


def registry_block(registry, produce_id: str | None) -> dict:
    """登记库核对块。registry 为 None 表示本服务未接登记库，如实报 skipped。"""
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


def exiftool_version(exiftool: str) -> str | None:
    try:
        out = subprocess.run([exiftool, "-ver"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() or None


def build_report(*, bmff: dict, media: dict, candidates: list, issues: list[Issue],
                 conclusion: str, reason_code: str | None, repairability: str | None,
                 c2pa: str, confidence: str, registry: dict,
                 file_name: str | None, size_bytes: int | None, sha256: str | None,
                 request_id: str | None, elapsed_ms: int, detected_mime: str,
                 detector_version: str, exiftool_version_value: str | None,
                 extra: dict | None = None) -> dict:
    """组装统一报告。三种模态产出的字段集合一致，前端无需分叉渲染（§4.5）。

    ``extra`` 用于附加模态专有块（如文档的 ``document``），键名不得覆盖既有字段。
    """
    report: dict = {
        "request_id": request_id,
        "file_name": file_name,
        "detected_mime_type": detected_mime,
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
        "bmff": bmff,
        "registry": registry,
        "detector_version": detector_version,
        "exiftool_version": exiftool_version_value,
        "elapsed_ms": elapsed_ms,
    }
    if extra:
        report.update(extra)
    return report
