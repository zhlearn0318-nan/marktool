import struct
import zlib

from app.metadata.c2pa_presence import C2PAPresence
from app.metadata.compliance import (
    ComplianceConclusion,
    CrossReaderResult,
    MetadataComplianceInspector,
    Repairability,
)
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from app.metadata.exiftool_client import ExifToolExecutionError
from app.metadata.repair_planner import RepairPlanner, TrustedRepairInput
from tests.fixtures import (
    UPDATED_AIGC,
    VALID_AIGC,
    broken_json_xmp,
    duplicate_xmp,
    legacy_xmp,
    make_jpeg,
    make_png,
)


def _add_png_chunk_before_iend(path, chunk_type: bytes, payload: bytes) -> None:
    data = path.read_bytes()
    offset = data.rfind(b"IEND") - 4
    assert offset >= 8
    body = chunk_type + payload
    chunk = struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
    path.write_bytes(data[:offset] + chunk + data[offset:])


def _add_jpeg_app11(path, payload: bytes) -> None:
    data = path.read_bytes()
    assert data.startswith(b"\xff\xd8")
    segment = b"\xff\xeb" + struct.pack(">H", len(payload) + 2) + payload
    path.write_bytes(data[:2] + segment + data[2:])


def _offline_planner() -> RepairPlanner:
    """结构归一化单元测试不依赖外部进程。

    这里**不再**用 ``require_cross_reader=False`` 关掉闸门：那个开关生产代码里
    一次都不传（``repair_service`` 用的是 ``RepairPlanner()``），关着测等于在验证
    一份跑不到的配置——`test_repair_service` 之外的这四条曾经就是这样过来的。
    改成直接把"交叉读取已匹配"注入检测结果，既不用起外部进程，又走生产默认的
    同一个判定路径。
    """
    return RepairPlanner()


def _plan(inspection, trusted=None):
    """注入"交叉读取已匹配"后交给生产默认配置的规划器。"""
    return _offline_planner().plan(_cross_read_matched(inspection), trusted)


def _cross_read_matched(result):
    """把"该做没做"的交叉读取补成"已匹配"，模拟真实图片检测器的产物。

    只补 ``not_run``：那是"检测器没跑这一步"，单测里跑 ``inspect()`` 本来就拿不到。
    真实结论（diverged / unavailable / not_applicable）一律原样保留——有测试专门
    靠它们立命（``test_reader_divergence_...``），覆盖掉就等于把被测对象改没了。
    """
    if result.cross_reader.status == "not_run":
        result.cross_reader = CrossReaderResult(
            status="matched", detail="测试注入：两条读取路径一致")
    return result


def test_not_found_is_not_repairable(tmp_path):
    path = make_png(tmp_path / "plain.png")
    result = MetadataComplianceInspector().inspect(path)
    plan = _plan(result)

    assert result.conclusion is ComplianceConclusion.NOT_FOUND
    assert plan.repairability is Repairability.NOT_APPLICABLE
    assert plan.executable is False
    assert "普通标注流程" in plan.blocking_reasons[0]


def test_project_character_rule_is_separate_from_gb_conclusion(tmp_path):
    chinese = {**VALID_AIGC, "ContentProducer": "示例机构"}
    path = make_png(tmp_path / "chinese.png", aigc_dict=chinese)
    result = MetadataComplianceInspector().inspect(path)

    assert result.conclusion is ComplianceConclusion.COMPLIANT
    assert result.project_policy.accepted is False
    assert any(item.category == "project_policy" for item in result.issues)


def test_missing_reserved_fields_can_be_proposed_without_guessing_identity(tmp_path):
    without_reserved = {
        key: value
        for key, value in VALID_AIGC.items()
        if key not in {"ReservedCode1", "ReservedCode2"}
    }
    path = make_png(
        tmp_path / "missing-reserved.png",
        raw_xmp=legacy_xmp(without_reserved),
    )
    inspection = MetadataComplianceInspector().inspect(path)
    plan = _plan(inspection)

    assert inspection.conclusion is ComplianceConclusion.NONCOMPLIANT
    assert plan.repairability is Repairability.CONFIRMABLE
    assert plan.executable is True
    assert plan.proposed_document["AIGC"]["ReservedCode1"] == ""
    assert plan.proposed_document["AIGC"]["ReservedCode2"] == ""
    assert any("补为空字符串" in action for action in plan.actions)


def test_identifier_conflict_blocks_partial_record_repair(tmp_path):
    registry = SQLiteIdentifierRegistry(str(tmp_path / "identifiers.sqlite3"))
    registry.reserve({"AIGC": VALID_AIGC}, "0" * 64).commit()
    without_reserved = {
        key: value
        for key, value in VALID_AIGC.items()
        if key not in {"ReservedCode1", "ReservedCode2"}
    }
    path = make_png(
        tmp_path / "conflicting-source.png",
        raw_xmp=legacy_xmp(without_reserved),
    )

    inspection = MetadataComplianceInspector(
        identifier_registry=registry
    ).inspect(path)
    plan = _plan(inspection)

    assert inspection.source_verification.status == "conflict"
    assert plan.repairability is Repairability.FORBIDDEN
    assert plan.executable is False


def test_identical_duplicates_can_be_deduplicated_after_confirmation(tmp_path):
    path = make_png(
        tmp_path / "duplicate.png",
        raw_xmp=duplicate_xmp(VALID_AIGC, VALID_AIGC),
    )
    inspection = MetadataComplianceInspector().inspect(path)
    plan = _plan(inspection)

    assert inspection.reason_codes == ["AIGC_MULTIPLE_RECORDS"]
    assert plan.repairability is Repairability.CONFIRMABLE
    assert plan.executable is True
    assert any("完全相同" in action for action in plan.actions)


def test_conflicting_duplicates_require_manual_review(tmp_path):
    path = make_png(
        tmp_path / "conflict.png",
        raw_xmp=duplicate_xmp(VALID_AIGC, UPDATED_AIGC),
    )
    inspection = MetadataComplianceInspector().inspect(path)
    plan = _plan(inspection)

    assert plan.repairability is Repairability.MANUAL_REVIEW
    assert plan.executable is False
    assert any("冲突" in reason for reason in plan.blocking_reasons)


def test_broken_json_needs_trusted_source(tmp_path):
    path = make_png(tmp_path / "broken.png", raw_xmp=broken_json_xmp())
    inspection = MetadataComplianceInspector().inspect(path)

    automatic = _plan(inspection)
    assert automatic.executable is False

    trusted = TrustedRepairInput(
        AIGC=VALID_AIGC,
        source_type="authorized_manual",
        source_reference="组长审批单 TEST-001",
    )
    confirmed_source_plan = _plan(inspection, trusted)
    assert confirmed_source_plan.executable is True
    assert confirmed_source_plan.proposed_document == {"AIGC": VALID_AIGC}


def test_c2pa_presence_blocks_automatic_repair(tmp_path):
    path = tmp_path / "c2pa.png"
    make_png(path, raw_xmp=broken_json_xmp())
    _add_png_chunk_before_iend(path, b"caBX", b"test-manifest-placeholder")

    inspection = MetadataComplianceInspector().inspect(str(path))
    plan = _plan(inspection)

    assert inspection.c2pa_presence.status is C2PAPresence.PRESENT_UNVERIFIED
    assert plan.repairability is Repairability.MANUAL_REVIEW
    assert plan.executable is False
    assert any("C2PA" in reason for reason in plan.blocking_reasons)


def test_jpeg_c2pa_app11_presence_blocks_automatic_repair(tmp_path):
    path = tmp_path / "c2pa.jpg"
    make_jpeg(path, raw_xmp=broken_json_xmp())
    manifest_uuid = bytes.fromhex("6332706100110010800000aa00389b71")
    _add_jpeg_app11(path, b"JP" + manifest_uuid + b"c2pa\x00placeholder")

    inspection = MetadataComplianceInspector().inspect(str(path))
    plan = _plan(inspection)

    assert inspection.c2pa_presence.status is C2PAPresence.PRESENT_UNVERIFIED
    assert inspection.c2pa_presence.carrier == "jpeg:APP11/JUMBF"
    assert plan.executable is False


def test_non_c2pa_jpeg_app11_is_not_misclassified(tmp_path):
    path = tmp_path / "other-app11.jpg"
    make_jpeg(path, aigc_dict=VALID_AIGC)
    _add_jpeg_app11(path, b"unrelated APP11 application data")

    inspection = MetadataComplianceInspector().inspect(str(path))

    assert inspection.conclusion is ComplianceConclusion.COMPLIANT
    assert inspection.c2pa_presence.status is C2PAPresence.NOT_FOUND


def test_reader_divergence_makes_result_indeterminate(tmp_path):
    class DivergedReader:
        def read_known_aigc_values(self, _file_path):
            return ['{"AIGC":{"Label":"different"}}']

    path = make_png(tmp_path / "diverged.png", aigc_dict=VALID_AIGC)
    inspection = MetadataComplianceInspector(exiftool=DivergedReader()).inspect(path)
    plan = _plan(inspection)

    assert inspection.conclusion is ComplianceConclusion.INDETERMINATE
    assert inspection.cross_reader.status == "diverged"
    assert plan.executable is False


def test_unavailable_cross_reader_is_warning_not_false_noncompliance(tmp_path):
    class UnavailableReader:
        def read_known_aigc_values(self, _file_path):
            raise ExifToolExecutionError("test unavailable")

    path = make_png(tmp_path / "unavailable.png", aigc_dict=VALID_AIGC)
    inspection = MetadataComplianceInspector(exiftool=UnavailableReader()).inspect(path)

    assert inspection.conclusion is ComplianceConclusion.COMPLIANT
    assert inspection.cross_reader.status == "unavailable"
    assert any(item.code == "EXIFTOOL_CROSSCHECK_UNAVAILABLE" for item in inspection.issues)


def test_unavailable_cross_reader_blocks_automatic_repair(tmp_path):
    class UnavailableReader:
        def read_known_aigc_values(self, _file_path):
            raise ExifToolExecutionError("test unavailable")

    without_reserved = {
        key: value
        for key, value in VALID_AIGC.items()
        if key not in {"ReservedCode1", "ReservedCode2"}
    }
    path = make_png(
        tmp_path / "unavailable-repair.png",
        raw_xmp=legacy_xmp(without_reserved),
    )
    inspection = MetadataComplianceInspector(exiftool=UnavailableReader()).inspect(path)
    plan = RepairPlanner().plan(inspection)

    assert inspection.conclusion is ComplianceConclusion.NONCOMPLIANT
    assert plan.repairability is Repairability.MANUAL_REVIEW
    assert plan.executable is False
    assert any("交叉读取" in reason for reason in plan.blocking_reasons)
