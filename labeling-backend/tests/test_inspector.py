"""MP4 已有元数据隐式标识 · 合规检测器测试（合规方案 §9.2 验收）。

分三层：
1. 国标结构判定（structural_issues）—— 纯函数：缺字段/Label 枚举/未知字段/
   空身份 → error；字符集/首次写入关系 → 仅 warn 不翻转结论（§2.2 / §2.1）。
2. 结论分类（_classify / _classify_many）—— 伪造 AIGCRecord 直接测
   not_found / compliant / noncompliant(BAD_JSON·MISSING_FIELD·DUPLICATE) 与
   repairability（相同重复→auto、规范+旧载体→auto、冲突→human）。
3. 端到端文件矩阵 —— 复制 sample_clean.mp4，用 exiftool 写不同载体/内容，
   跑完整 inspect()，验证 §9.2 各结论 + 媒体状态与元数据结论分离（§2.4）。
"""
from __future__ import annotations

import shutil
import struct

import pytest

from app.adapters import Mp4Adapter
from app.core import aigc
from app.core.inspector import (
    CONCLUSION_COMPLIANT,
    CONCLUSION_INDETERMINATE,
    CONCLUSION_NONCOMPLIANT,
    CONCLUSION_NOT_FOUND,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    DETECTOR_VERSION,
    Issue,
    MetadataComplianceInspector,
    REPAIR_AUTO,
    REPAIR_HUMAN,
    structural_issues,
)
from app.core.reader import AIGCRecord
from tests.common import CLEAN_MP4, EXIFTOOL_CONFIG, VALID_AIGC


# ---- 构造辅助 -------------------------------------------------------------

def _adapter() -> Mp4Adapter:
    return Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))


def _inspector() -> MetadataComplianceInspector:
    return MetadataComplianceInspector(exiftool="exiftool",
                                       exiftool_config=str(EXIFTOOL_CONFIG))


def _clean_copy(tmp_path, name: str = "in.mp4"):
    p = tmp_path / name
    shutil.copyfile(CLEAN_MP4, p)
    return p


def _raw(inner: dict) -> str:
    return aigc.serialize_aigc(inner)


def _write_xmp(path, raw: str) -> None:
    """把任意 AIGC 值写入规范新载体 XMP-aigc:AIGC（不校验内容）。"""
    _adapter()._exiftool_cmd(["-overwrite_original", "-XMP-aigc:AIGC=" + raw], str(path))


def _write_quicktime(path, raw: str) -> None:
    """把任意 AIGC 值写入旧载体 QuickTime:Comment。"""
    _adapter()._exiftool_cmd(["-overwrite_original", "-QuickTime:Comment=" + raw], str(path))


def _top_boxes(d: bytes) -> list[tuple[int, str, int]]:
    """顶层 box 列表 (offset, type, size)。只在损坏测试里定位 moov/mdat。"""
    out: list[tuple[int, str, int]] = []
    i, n = 0, len(d)
    while i + 8 <= n:
        size, typ = struct.unpack(">I4s", d[i:i + 8])
        name = typ.decode("latin1")
        hdr = 8
        if size == 1:
            if i + 16 > n:
                break
            size = struct.unpack(">Q", d[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = n - i
        if size < hdr or i + size > n:
            break
        out.append((i, name, size))
        i += size
    return out


def _box_region(d: bytes, btype: str) -> tuple[int, int]:
    """返回某顶层 box 的 (offset, size)，假定恰好存在一份。"""
    hit = [t for t in _top_boxes(d) if t[1] == btype]
    assert len(hit) == 1, f"fixture 应恰好含一个 {btype} box"
    return hit[0][0], hit[0][2]


def _damaged_copy(tmp_path, mode: str, name: str = "damaged.mp4"):
    """从干净样本做字节级损坏，构造『moov 在但内部损坏』的三种真实形态。

    mode:
      zero_moov_payload  —— moov 盒子头保留、内部元数据载荷清零（exiftool 会
                            宽容地读成空，ffprobe 无可枚举流 → unreadable）。
      moov_size_overrun  —— moov 完好，文件尾追一段声明越界的 box 头（拼接/残留
                            数据），使 has_moov=True 但顶层树越界截断。
      zero_mdat_payload  —— 保留 moov/ftyp，清空 mdat 视频数据（解码冒烟失败）。
    """
    p = _clean_copy(tmp_path, name)
    d = bytearray(p.read_bytes())
    if mode == "zero_moov_payload":
        off, size = _box_region(bytes(d), "moov")
        end = off + size
        d[off + 100:end - 32] = b"\x00" * ((end - 32) - (off + 100))   # 保留盒子头与尾部
    elif mode == "moov_size_overrun":
        d += struct.pack(">I4s", 0xFFFFFFF0, b"JUNK")                    # 越界残留
    elif mode == "zero_mdat_payload":
        off, size = _box_region(bytes(d), "mdat")
        d[off + 8:off + size] = b"\x00" * (size - 8)
    else:
        raise AssertionError(f"未知损坏模式: {mode}")
    p.write_bytes(d)
    return p


def _rec(tag_key: str, inner: dict | None) -> AIGCRecord:
    """伪造一条读取记录：inner=None 表示无法解析出国标结构。"""
    raw = _raw(inner) if inner is not None else "{\"AIGC\": "
    return AIGCRecord(tag_key=tag_key, raw=raw, aigc=inner)


def _error_codes(report: dict) -> set[str]:
    return {i["code"] for i in report["issues"] if i["severity"] == "error"}


# ============================================================================
# 一、国标结构判定纯函数（§2.1 / §2.2）
# ============================================================================

def test_structural_valid_aigc_has_no_errors():
    issues = structural_issues(dict(VALID_AIGC))
    assert [i for i in issues if i.severity == "error"] == []


def test_structural_missing_field_is_error():
    inner = dict(VALID_AIGC)
    del inner["ReservedCode2"]
    issues = structural_issues(inner)
    codes = {i.code for i in issues if i.severity == "error"}
    assert codes == {"MISSING_FIELD"}


def test_structural_unknown_field_is_error():
    issues = structural_issues({**VALID_AIGC, "Extra": "x"})
    assert {i.code for i in issues if i.severity == "error"} == {"UNKNOWN_FIELD"}


def test_structural_label_numeric_is_error_with_deterministic_hint():
    inner = {**VALID_AIGC, "Label": 1}               # 数值 1 → 语义唯一
    issues = structural_issues(inner)
    assert any(i.code == "BAD_LABEL" and i.severity == "error"
               and "需转为字符串" in i.message for i in issues)


def test_structural_label_illegal_value_is_error():
    for bad in (9, "x", True):
        issues = structural_issues({**VALID_AIGC, "Label": bad})
        assert any(i.code == "BAD_LABEL" and i.severity == "error" for i in issues), bad


def test_structural_empty_identity_is_error():
    inner = dict(VALID_AIGC)
    inner["ContentPropagator"] = ""
    issues = structural_issues(inner)
    assert any(i.code == "MISSING_FIELD" and i.severity == "error"
               and "ContentPropagator" in i.message for i in issues)


def test_structural_charset_warn_only_not_error():
    """§2.2：范围外字符只 warn，不得把项目限制冒充国标要求判不合规。"""
    inner = {**VALID_AIGC, "ContentProducer": "ORG 1565"}   # 含空格
    issues = structural_issues(inner)
    assert any(i.code == "CHARSET" and i.severity == "warn" for i in issues)
    assert all(i.severity != "error" for i in issues)


def test_structural_first_write_mismatch_warn_only():
    """§2.1 第 5 点：传播方≠制作者可能是合法二次传播，仅 warn。"""
    inner = dict(VALID_AIGC)
    inner["ContentPropagator"] = "ORG_ANOTHER"
    inner["PropagateID"] = "ANOTHER-ID"
    issues = structural_issues(inner)
    assert any(i.code == "FIRST_WRITE_MISMATCH" and i.severity == "warn" for i in issues)
    assert all(i.severity != "error" for i in issues)


def test_structural_consistent_first_write_no_warn():
    issues = structural_issues(dict(VALID_AIGC))
    assert not any(i.code == "FIRST_WRITE_MISMATCH" for i in issues)


# ============================================================================
# 二、结论分类（纯逻辑）
# ============================================================================

@pytest.fixture
def inspector():
    return _inspector()


def test_classify_no_record_not_found(inspector):
    r = inspector._classify([])
    assert r["conclusion"] == CONCLUSION_NOT_FOUND
    assert r["reason_code"] is None
    assert r["confidence"] == CONFIDENCE_HIGH
    assert r["repairability"] is None


def test_classify_single_valid_compliant(inspector):
    r = inspector._classify([_rec("XMP-aigc:AIGC", dict(VALID_AIGC))])
    assert r["conclusion"] == CONCLUSION_COMPLIANT
    assert r["reason_code"] is None
    assert r["repairability"] is None
    assert r["confidence"] == CONFIDENCE_HIGH


def test_classify_single_unparseable_bad_json(inspector):
    r = inspector._classify([_rec("XMP-aigc:AIGC", None)])
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert r["reason_code"] == "BAD_JSON"
    assert r["repairability"] == REPAIR_HUMAN
    assert r["confidence"] == CONFIDENCE_LOW


def test_classify_single_missing_field_noncompliant(inspector):
    inner = dict(VALID_AIGC)
    del inner["ContentProducer"]                     # 身份缺失 → 人工
    r = inspector._classify([_rec("XMP-aigc:AIGC", inner)])
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert r["reason_code"] == "MISSING_FIELD"
    assert r["repairability"] == REPAIR_HUMAN


def test_classify_single_missing_reserved_auto_fixable(inspector):
    """§5.1：缺空值保留字段 + 身份与 Label 均可确定 → auto_fixable。"""
    inner = dict(VALID_AIGC)
    del inner["ReservedCode2"]
    r = inspector._classify([_rec("XMP-aigc:AIGC", inner)])
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert r["reason_code"] == "MISSING_FIELD"
    assert r["repairability"] == REPAIR_AUTO


def test_classify_legacy_single_compliant_with_info(inspector):
    """旧载体 QuickTime:Comment 单一记录：LEGACY_CARRIER 只是 info，不翻转结论。"""
    r = inspector._classify([_rec("QuickTime:Comment", dict(VALID_AIGC))])
    assert r["conclusion"] == CONCLUSION_COMPLIANT
    assert any(i.code == "LEGACY_CARRIER" and i.severity == "info"
               for i in r["issues"])
    assert r["confidence"] == CONFIDENCE_LOW         # 有 warn/info → 保守低可信


def test_classify_many_identical_duplicates_auto(inspector):
    """完全相同的重复标识 → auto_fixable（去重即合规）。"""
    recs = [_rec("XMP-aigc:AIGC", dict(VALID_AIGC)),
            _rec("XMP-aigc:AIGC", dict(VALID_AIGC))]
    r = inspector._classify(recs)
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert r["reason_code"] == "DUPLICATE_RECORDS"
    assert r["repairability"] == REPAIR_AUTO
    assert r["confidence"] == CONFIDENCE_LOW


def test_classify_many_canonical_plus_legacy_auto(inspector):
    """规范新载体 + 旧载体残留 → 去旧保新可自动。"""
    recs = [_rec("QuickTime:Comment", dict(VALID_AIGC)),
            _rec("XMP-aigc:AIGC", dict(VALID_AIGC))]
    r = inspector._classify(recs)
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert r["reason_code"] == "DUPLICATE_RECORDS"
    assert r["repairability"] == REPAIR_AUTO
    assert any(i.code == "LEGACY_CARRIER" for i in r["issues"])   # 共存告警


def test_classify_many_conflicting_canonical_human(inspector):
    """两份冲突的规范记录 → 人工选择，禁止自动修。"""
    other = dict(VALID_AIGC)
    other["ProduceID"] = "OTHER-ID"
    recs = [_rec("XMP-aigc:AIGC", dict(VALID_AIGC)),
            _rec("XMP-aigc:AIGC", other)]
    r = inspector._classify(recs)
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert r["reason_code"] == "DUPLICATE_RECORDS"
    assert r["repairability"] == REPAIR_HUMAN


# ============================================================================
# 三、端到端文件矩阵（§9.2 验收）
# ============================================================================

def test_inspect_not_found_clean_file(inspector, tmp_path):
    """无标识文件 → not_found；媒体正常单独输出 ok（§2.4）。

    复制到 tmp_path 再检，避免受仓库路径（含 "AIGC" 字样）的读取误报干扰——
    reader 会把 exiftool 回显的 SourceFile 路径值当候选，详见 feedback 记录。
    """
    report = inspector.inspect(_clean_copy(tmp_path))
    assert report["conclusion"] == CONCLUSION_NOT_FOUND
    assert report["record_count"] == 0
    assert report["reason_code"] is None
    assert report["media_status"] == "ok"
    assert report["c2pa_presence"] == "absent"
    assert report["confidence"] == CONFIDENCE_HIGH
    assert report["bmff"]["has_moov"] is True


def test_inspect_compliant_unique_file(inspector, tmp_path):
    """合规唯一一份 → compliant，报告结构与登记核对字段齐全。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, _raw(VALID_AIGC))
    report = inspector.inspect(
        p, file_name="demo.mp4", size_bytes=1234, sha256="ab" * 32,
        request_id="req-1",
        registry=lambda pid: pid == VALID_AIGC["ProduceID"])

    assert report["conclusion"] == CONCLUSION_COMPLIANT
    assert report["reason_code"] is None
    assert report["record_count"] == 1
    assert report["issues"] == []
    assert report["repairability"] is None
    assert report["confidence"] == CONFIDENCE_HIGH
    assert report["media_status"] == "ok"
    assert report["c2pa_presence"] == "absent"

    # 报告字段回显与形状
    assert report["file_name"] == "demo.mp4"
    assert report["size_bytes"] == 1234
    assert report["sha256"] == "ab" * 32
    assert report["request_id"] == "req-1"
    assert report["detected_mime_type"] == "video/mp4"
    assert report["detector_version"] == DETECTOR_VERSION
    assert isinstance(report["exiftool_version"], str) and report["exiftool_version"]
    assert isinstance(report["elapsed_ms"], int) and report["elapsed_ms"] >= 0

    cand = report["candidates"][0]
    assert cand["parseable"] is True and cand["tag"].endswith(":AIGC")
    assert set(cand["parsed_fields"]) == set(aigc.FIELD_ORDER)

    # 登记核对：本地出现过该 ProduceID → known True
    reg = report["registry"]
    assert reg["mode"] == "local_produceid"
    assert reg["produce_id"] == VALID_AIGC["ProduceID"]
    assert reg["known"] is True


def test_inspect_legacy_carrier_single_compliant(inspector, tmp_path):
    """只有旧载体且内容合规 → compliant（旧载体不判不合规，§3.1）。"""
    p = _clean_copy(tmp_path)
    _write_quicktime(p, _raw(VALID_AIGC))
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_COMPLIANT
    assert report["reason_code"] is None
    assert report["record_count"] == 1
    assert report["candidates"][0]["tag"].startswith("QuickTime")
    assert any(i["code"] == "LEGACY_CARRIER" for i in report["issues"])


def test_inspect_duplicate_new_plus_legacy(inspector, tmp_path):
    """新载体 + 旧载体共存 → 多份不合规，去旧保新 auto，且带共存告警。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, _raw(VALID_AIGC))
    _write_quicktime(p, _raw(VALID_AIGC))
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "DUPLICATE_RECORDS"
    assert report["record_count"] == 2
    assert report["repairability"] == REPAIR_AUTO
    assert report["confidence"] == CONFIDENCE_LOW
    assert "DUPLICATE_RECORDS" in _error_codes(report)
    assert any(i["code"] == "LEGACY_CARRIER" and i["severity"] == "warn"
               for i in report["issues"])


def test_inspect_bad_json_single(inspector, tmp_path):
    """值损坏（JSON 截断）→ BAD_JSON，需人工（§4）。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, '{"AIGC": ')                        # 残缺 JSON，仍含 AIGC 字样
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "BAD_JSON"
    assert report["repairability"] == REPAIR_HUMAN
    assert report["confidence"] == CONFIDENCE_LOW


def test_inspect_missing_field_auto_fixable(inspector, tmp_path):
    """缺空值保留字段 → 非合规但可确定性修复。"""
    p = _clean_copy(tmp_path)
    inner = dict(VALID_AIGC)
    del inner["ReservedCode2"]
    _write_xmp(p, _raw(inner))
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "MISSING_FIELD"
    assert report["repairability"] == REPAIR_AUTO


def test_inspect_label_numeric(inspector, tmp_path):
    """Label 数值（语义唯一）→ BAD_LABEL，可确定性转字符串。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, _raw({**VALID_AIGC, "Label": 3}))
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "BAD_LABEL"
    assert report["repairability"] == REPAIR_AUTO


def test_inspect_charset_warn_keeps_compliant(inspector, tmp_path):
    """§2.2 端到端：范围外字符 → CHARSET warn，结论仍 compliant。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, _raw({**VALID_AIGC, "ContentProducer": "ORG 1565"}))
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_COMPLIANT
    assert report["reason_code"] is None
    assert any(i["code"] == "CHARSET" and i["severity"] == "warn"
               for i in report["issues"])
    assert report["confidence"] == CONFIDENCE_LOW


def test_inspect_missing_moov_indeterminate(inspector, tmp_path):
    """moov 缺失/结构损坏 → indeterminate，不把不可读误判为不合规（§2.4/§3）。"""
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(CLEAN_MP4.read_bytes()[:200])     # 只留 ftyp 附近头部
    report = inspector.inspect(bad)
    assert report["conclusion"] == CONCLUSION_INDETERMINATE
    assert report["reason_code"] == "UNREADABLE_CARRIER"
    assert report["repairability"] == REPAIR_HUMAN
    assert report["confidence"] == CONFIDENCE_LOW
    assert report["bmff"]["has_moov"] is False
    assert report["c2pa_presence"] != "absent"         # 结构不可信 → 非 absent
    assert any(i["code"] == "UNREADABLE_CARRIER" for i in report["issues"])


# ---- 载体可靠性升级（§2.4/md§56：moov 在但整体不可读时禁止误报 not_found）----

def _assert_indeterminate_carrier(report: dict, *, has_moov: bool = True):
    assert report["conclusion"] == CONCLUSION_INDETERMINATE
    assert report["reason_code"] == "UNREADABLE_CARRIER"
    assert report["repairability"] == REPAIR_HUMAN
    assert report["confidence"] == CONFIDENCE_LOW
    assert report["record_count"] == 0
    assert report["candidates"] == []
    assert report["bmff"]["has_moov"] is has_moov
    assert any(i["code"] == "UNREADABLE_CARRIER" for i in report["issues"])


def test_inspect_moov_payload_damaged_indeterminate(inspector, tmp_path):
    """moov 存在但内部元数据载荷清零 → 不得按"未检出干净文件"处理。

    exiftool 对这种损坏宽容地读出空元数据，若只信 exiftool 就会误报
    not_found；须以媒体不可解析为信号升级为 indeterminate（需人工复核）。
    """
    report = inspector.inspect(_damaged_copy(tmp_path, "zero_moov_payload"))
    _assert_indeterminate_carrier(report)
    assert report["media_status"] == "unreadable"


def test_inspect_moov_size_overrun_indeterminate(inspector, tmp_path):
    """moov 声明的 box 尺寸越界截断 → 顶层结构不可信 → indeterminate。

    moov 盒子头仍在（has_moov True）但无法安全组装，属于 md 所谓
    『不能安全组装的元数据位置，不得误判为合规』。
    """
    report = inspector.inspect(_damaged_copy(tmp_path, "moov_size_overrun"))
    _assert_indeterminate_carrier(report)
    assert report["bmff"]["truncated"] is True
    assert report["c2pa_presence"] != "absent"


def test_inspect_mdat_damaged_undecodable_indeterminate(inspector, tmp_path):
    """moov/ftyp 完好但 mdat 视频数据清零、解码冒烟失败 → indeterminate。

    元数据结论只回答"标识是否合规"（§2.4），但文件整体不可解码时不能声称
    "没有标识"是确定的，需人工复核，而不是 not_found。
    """
    report = inspector.inspect(_damaged_copy(tmp_path, "zero_mdat_payload"))
    _assert_indeterminate_carrier(report)
    assert report["media_status"] == "degraded"
    assert report["media"]["decode_smoke"] == "failed"
    assert report["bmff"]["has_mdat"] is True          # mdat 盒子在，只是数据坏


def test_inspect_registry_skipped_when_none(inspector, tmp_path):
    """未接登记库时 registry 报告 mode=skipped，不报错。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, _raw(VALID_AIGC))
    report = inspector.inspect(p)
    assert report["registry"]["mode"] == "skipped"


# ============================================================================
# 四、详细诊断（additive：候选带位置 / 多份逐字段冲突 / BAD_JSON 定位 / 载体清零）
# ============================================================================

def test_json_fault_fragment_locates_truncated_field():
    """BAD_JSON 定位：值在字段中段被截 → 文案指出字段名。"""
    from app.core.inspector import _json_fault_fragment
    raw = _raw(VALID_AIGC)                       # 完整可解析
    assert _json_fault_fragment(raw) is None
    cut = raw.find('"ProduceID"') + 12           # 截断在 ProduceID 值中段
    frag = _json_fault_fragment(raw[:cut])
    assert frag is not None and "ProduceID" in frag
    # 垃圾/空白不崩溃，且返回非空定位片段
    assert _json_fault_fragment("not json at all") is not None
    assert _json_fault_fragment("   ") is not None


def test_classify_many_conflict_emits_field_level_detail(inspector):
    """多份且内容不一致 → 除 DUPLICATE 摘要外，还有逐字段不一致详情。"""
    other = dict(VALID_AIGC)
    other["ProduceID"] = "DIFFERENT-PRODUCE-ID"
    r = inspector._classify([
        _rec("XMP-aigc:AIGC", dict(VALID_AIGC)),
        _rec("XMP-aigc:AIGC", other)])
    assert r["conclusion"] == CONCLUSION_NONCOMPLIANT
    codes = [i.code for i in r["issues"]]
    assert "FIELDS_DISAGREE" in codes
    msg = next(i.message for i in r["issues"] if i.code == "FIELDS_DISAGREE")
    assert "ProduceID" in msg and "DIFFERENT-PRODUCE-ID" in msg


def test_inspect_candidate_carries_location(inspector, tmp_path):
    """候选记录带人类可读载体位置（新增字段，不影响判定）。"""
    p = _clean_copy(tmp_path)
    _write_xmp(p, _raw(VALID_AIGC))
    report = inspector.inspect(p)
    cand = report["candidates"][0]
    assert cand["location"].startswith("XMP")


def test_inspect_new_plus_legacy_conflict_details(inspector, tmp_path):
    """新载体规范 + 旧载体内容冲突 → FIELDS_DISAGREE 点名字段与两处位置。"""
    p = _clean_copy(tmp_path)
    other = dict(VALID_AIGC)
    other["ContentProducer"] = "ORG_CONFLICT"
    other["ContentPropagator"] = "ORG_CONFLICT"
    other["ProduceID"] = "CONFLICT-ID"
    other["PropagateID"] = "CONFLICT-ID"
    _write_xmp(p, _raw(VALID_AIGC))
    _write_quicktime(p, _raw(other))
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_NONCOMPLIANT
    assert report["reason_code"] == "DUPLICATE_RECORDS"
    hit = [i for i in report["issues"] if i["code"] == "FIELDS_DISAGREE"]
    assert hit and "ContentProducer" in hit[0]["message"]
    assert "XMP" in hit[0]["message"] and "QuickTime" in hit[0]["message"]


def test_inspect_wiped_comment_atom_indeterminate(inspector, tmp_path):
    """旧载体数据原子被清零 → 不按无标识，升 indeterminate 并点名位置（§2.4）。"""
    p = _clean_copy(tmp_path)
    raw = _raw(VALID_AIGC)
    _write_quicktime(p, raw)
    d = bytearray(p.read_bytes())
    idx = d.find(raw.encode())
    assert idx != -1
    d[idx:idx + len(raw)] = b"\x00" * len(raw)          # 内容清零，原子保留
    p.write_bytes(d)
    report = inspector.inspect(p)
    assert report["conclusion"] == CONCLUSION_INDETERMINATE
    assert report["reason_code"] == "METADATA_REGION_WIPED"
    assert report["record_count"] == 0
    assert any(i["code"] == "METADATA_REGION_WIPED" and "©cmt" in i["message"]
               for i in report["issues"])
