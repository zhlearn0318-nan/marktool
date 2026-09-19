"""图片合规检测桥接：把图片模块的判定结果映射为本项目统一的报告结构。

开发手册 §4.5 要求图片与视频产出**同构**报告，前端不为模态分叉。图片侧的判定
逻辑由 ``app.metadata.compliance`` 承担（与写入侧同源，§14），本模块只做两件事：

1. 把它的 ``MetadataComplianceResult`` 逐字段翻译成本项目 ``/api/v1/compliance-inspect``
   已有的报告字段；
2. 把**原始结果完整**放进 ``metadata_compliance`` 键，它的富信息
   （``category`` / ``standard_basis`` / ``project_policy`` / ``cross_reader`` /
   ``source_verification`` / ``extended_xmp`` / ``raw_value_sha256``）一个不丢。

两边的 ``conclusion`` 词表本就相同，无需换算。
"""
from __future__ import annotations

from pathlib import Path

from ..metadata.compliance import MetadataComplianceResult
from ..metadata.exiftool_client import ExifToolClient
from ..metadata.identifier_registry import SQLiteIdentifierRegistry

# 图片没有 BMFF box 结构；前端据此整行隐藏容器结构（保留键名以维持形状一致）
_BMFF_NOT_APPLICABLE = {
    "applicable": False,
    "note": "图片无 BMFF box 结构（BMFF 为 MP4 专有）",
    "has_ftyp": None,
    "has_moov": None,
    "has_mdat": None,
    "c2pa_uuid": [],
}

# 严重度词表：他们的 level → 本项目既有的 severity
_SEVERITY = {"error": "error", "warning": "warn", "info": "info"}

# 可修复性词表：他们的 Repairability → 本项目既有的 repairability
_REPAIRABILITY = {
    "not_applicable": None,        # 合规/未检出，无需修复
    "confirmable": "auto_fixable",  # 由 repair_planner 给出，检测阶段不会出现
    "manual_review": "needs_human",
    "forbidden": "forbidden",
}

# C2PA 存在性：他们 not_found → 本项目 absent，其余同名
_C2PA = {"not_found": "absent", "present_unverified": "present_unverified",
         "indeterminate": "indeterminate"}

DETECTOR_VERSION = "image-metadata-compliance/1.0 (app.metadata.compliance)"


def resolve_exiftool(candidate: str | None) -> str | None:
    """本项目配置里 exiftool 是**命令名**，图片模块要的是**可执行文件路径**。

    直接把 ``"exiftool"`` 传过去会被判成"EXIFTOOL_PATH 指向的文件不存在"。
    只有确实是个文件时才传路径，否则交回 None，让它按 PATH 自行发现
    （或遵循其 EXIFTOOL_PATH 环境变量）。
    """
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    return str(path) if path.is_file() else None


def build_client(exiftool: str | None, exiftool_config: str | None,
                 *, timeout_seconds: int = 30) -> ExifToolClient:
    """图片模块的 ExifTool 客户端。写入侧与检测侧共用同一配置（§14）。"""
    return ExifToolClient(
        executable=resolve_exiftool(exiftool),
        config_path=exiftool_config,
        timeout_seconds=timeout_seconds,
    )


def build_inspector(*, exiftool: str | None = "exiftool",
                    exiftool_config: str | None = None,
                    registry_path: str | None = None,
                    timeout_seconds: int = 30):
    """构造图片检测器。"""
    from ..metadata.compliance import MetadataComplianceInspector

    registry = SQLiteIdentifierRegistry(registry_path) if registry_path else None
    return MetadataComplianceInspector(
        exiftool=build_client(exiftool, exiftool_config,
                              timeout_seconds=timeout_seconds),
        identifier_registry=registry,
    )


def _candidate(cand) -> dict:
    """他们的 CandidateEvidence → 本项目的候选条目。

    前端已渲染 ``location``（物理位置）与 ``parsed_fields``，两者都从
    ``packet_location`` / ``parsed_document`` 推出——多份记录时靠位置区分是哪一份。
    """
    document = cand.parsed_document
    fields: list[str] = []
    if isinstance(document, dict):
        inner = document.get("AIGC", document)
        if isinstance(inner, dict):
            fields = [k for k, v in inner.items() if v not in (None, "")]
    return {
        "tag": cand.property_name,
        "parseable": cand.parseable,
        "parsed_fields": fields,
        "raw_preview": cand.raw_value_preview,
        "location": cand.packet_location,
        "raw_value_sha256": cand.raw_value_sha256,
        "parse_error": cand.parse_error,
    }


def _issue(issue) -> dict:
    """他们的 ComplianceIssue → 本项目的问题条目。

    ``category`` 与 ``standard_basis`` 是本项目原先没有的：前者区分
    「国标结论」与「项目策略/载体/来源」，后者给出国标条款出处。前端可选用。
    """
    return {
        "code": issue.code,
        "severity": _SEVERITY.get(issue.level, "info"),
        "message": issue.detail,
        "field": None,
        "category": issue.category,
        "standard_basis": issue.standard_basis,
    }


def _media_block(path: str, result: MetadataComplianceResult) -> dict:
    """本项目的 ``media{}`` 来自 Pillow（视频侧来自 ffprobe）。"""
    block: dict = {
        "media_status": "ok",
        "format": result.detected_format,
        "pixels_sha256": result.pixel_sha256,
        "file_sha256": result.file_sha256,
    }
    try:
        from PIL import Image
        with Image.open(path) as image:
            block["width"], block["height"] = image.size
            block["mode"] = image.mode
    except Exception:  # 尺寸仅用于展示；探测失败不影响结论
        pass
    return block


def to_report(path: str, result: MetadataComplianceResult, *,
              file_name: str | None = None, size_bytes: int | None = None,
              sha256: str | None = None, request_id: str | None = None,
              elapsed_ms: int = 0, exiftool_version: str | None = None) -> dict:
    """把图片模块的判定结果组装成本项目统一的检测报告。"""
    media = _media_block(path, result)
    registry = {
        "status": result.source_verification.status,
        "detail": "；".join(result.source_verification.details) or None,
    }
    return {
        "request_id": request_id,
        "file_name": file_name,
        "detected_mime_type": result.mime_type,
        "size_bytes": size_bytes,
        "sha256": sha256 or result.file_sha256,
        "record_count": result.record_count,
        "candidates": [_candidate(c) for c in result.candidates],
        "issues": [_issue(i) for i in result.issues],
        "conclusion": result.conclusion.value,
        # 他们的 reason_codes 是列表（可能同时命中多条）；本项目原有字段是单值，
        # 保留它以免前端改动，同时把完整列表一并给出。
        "reason_code": result.reason_codes[0] if result.reason_codes else None,
        "reason_codes": list(result.reason_codes),
        "repairability": _REPAIRABILITY.get(result.repairability.value),
        "c2pa_presence": _C2PA.get(result.c2pa_presence.status.value,
                                  result.c2pa_presence.status.value),
        "media_status": media["media_status"],
        # 图片判定是确定性的（结构 + 交叉读取 + 登记库），不存在置信度分级
        "confidence": "high",
        "media": media,
        "bmff": dict(_BMFF_NOT_APPLICABLE),
        "registry": registry,
        "detector_version": DETECTOR_VERSION,
        "exiftool_version": exiftool_version,
        "elapsed_ms": elapsed_ms,
        # 原始结果完整保留：category / standard_basis / project_policy /
        # cross_reader / source_verification / extended_xmp 都在这里
        "metadata_compliance": result.model_dump(mode="json", exclude={"raw_records"}),
    }


def read_existing_records(path) -> list:
    """图片侧「已有标识」预检：与图片写入/检测共用同一个读取器（§14）。

    预检若另用一套读取器，就会出现"预检说没有、写入服务说有"的分歧——
    对同一份文件给出两个答案。
    """
    from ..metadata.xmp_reader import read_aigc_records
    return read_aigc_records(str(path))


def resolve_registry_path(settings) -> str | None:
    """编号登记库落在项目存储根下，与任务存储同处一地。"""
    root = Path(settings.storage.root)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return str(root / "aigc_identifiers.sqlite3")


def build_repair_service(settings):
    """按本项目配置构造修复工作台服务。

    **登记库必须是上面那一个**：两张表合并前，修复工作台用自己的默认配置
    （``<repo>/data/aigc_identifiers.sqlite3``），而打标流水线与
    ``/compliance-inspect`` 用 ``<storage.root>/aigc_identifiers.sqlite3``。
    两个库互不可见会造成两处失真：
      1. 修复写出的编号在后续合规检测里查不到，被报成 unverified；
      2. 打标写入的编号修复台看不见，跨流程的编号复用（DuplicatedIdentifier）
         检查整个失效——同一个 ProduceID 可以被两条流程各自用在不同的图上。
    所以这里把存储、审计、登记库统一锚到 settings.storage.root。
    """
    from ..metadata.repair_service import MetadataRepairConfig, MetadataRepairService

    root = Path(settings.storage.root)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    config = MetadataRepairConfig(
        storage_root=root / "metadata_repair_files",
        database_path=root / "metadata_repairs.sqlite3",
        audit_root=root / "metadata_repair_audit",
        identifier_database_path=Path(resolve_registry_path(settings)),
        max_upload_bytes=int(getattr(settings.storage, "max_file_bytes",
                                     25 * 1024 * 1024)),
    )
    return MetadataRepairService(config)
