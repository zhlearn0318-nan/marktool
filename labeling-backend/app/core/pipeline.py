"""标注任务流水线（开发手册 §8.2 / §9）：统一引擎 → 载体适配器。

对应 UniMark 架构中的 unified_engine：按真实文件类型把任务分发给对应适配器。
完整流程：检查已有标识 → 写入（或先整体替换再写入）→ 回读校验 → 媒体完整性
校验 → 原子发布。任一强制步骤失败即保留审计信息并标记 failed。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import jobs, util
from .errors import (AIGC_DUPLICATE_RECORDS, AIGC_IDENTIFIER_DUPLICATE,
                     AIGC_METADATA_EXISTS, INTERNAL_ERROR,
                     MEDIA_INTEGRITY_FAILED, METADATA_READBACK_FAILED,
                     METADATA_WRITE_FAILED)
from ..adapters import AdapterError, get_adapter
from .image_bridge import build_client, resolve_registry_path
from .mimetype import suffix_for_mime
from .storage import FileStorage, safe_display_name
from .store import JobStore
# 只引异常类型：登记库模块仅依赖标准库，不会与 metadata 包形成循环导入
from ..metadata.identifier_registry import DuplicateIdentifierError

# 图片侧写入由 app.metadata.image_adapter 承担（与合规检测同源，§14）
_IMAGE_MIMES = frozenset({"image/jpeg", "image/png"})

# 图片写入服务上报的阶段 → 本项目任务阶段（§8.1）
_IMAGE_STAGE = {
    "writing_metadata": jobs.STAGE_WRITING,
    "verifying_metadata": jobs.STAGE_VERIFYING_METADATA,
    "publishing_output": jobs.STAGE_PUBLISHING,
}


class PipelineError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def run_job(job_id: str, store: JobStore, storage: FileStorage,
            settings: Any) -> None:
    job = store.get(job_id)
    if job is None:
        return
    try:
        _execute(job, store, storage, settings)
    except PipelineError as e:
        _fail(job, store, e.code, e.message, e.retryable, stage=jobs.STAGE_FAILED,
              job_retention_hours=settings.storage.job_retention_hours)
    except Exception as e:  # 未分类内部错误（§11.2 500）
        _fail(job, store, INTERNAL_ERROR, f"内部错误: {e}", retryable=True,
              stage=jobs.STAGE_FAILED,
              job_retention_hours=settings.storage.job_retention_hours)


def _execute(job: dict, store: JobStore, storage: FileStorage, settings: Any) -> None:
    # §4.5：各模态共用同一套任务接口，但载体实现分属两条交付线。
    # 图片走自成一体的写入服务；其余（视频/文档）走注册表适配器 + 本流水线的
    # 通用编排（已有标识 → 策略 → 写入 → 回读 → 媒体完整性 → 原子发布）。
    if job["detected_mime_type"] in _IMAGE_MIMES:
        return _execute_image(job, store, storage, settings)
    return _execute_adapter(job, store, storage, settings)


def _execute_adapter(job: dict, store: JobStore, storage: FileStorage, settings: Any) -> None:
    job_id = job["job_id"]
    mime = job["detected_mime_type"]
    # 图片与视频共用本流水线，暂存/输出命名一律按真实 MIME 取后缀（§12.2）
    suffix = suffix_for_mime(mime)
    original_path = storage.original_path(job["original_stored_name"])
    submitted = json.loads(job["submitted_aigc"])
    adapter = get_adapter(mime, exiftool=settings.paths.exiftool,
                          ffprobe=settings.paths.ffprobe,
                          ffmpeg=settings.paths.ffmpeg,
                          exiftool_config=settings.paths.exiftool_config)
    audit = json.loads(job.get("audit") or "{}")

    store.update(job_id, status=jobs.RUNNING, stage=jobs.STAGE_INSPECTING_FILE,
                 updated_at=util.now_iso())

    # ---- 已有标识检测（§9.3）----
    store.update(job_id, stage=jobs.STAGE_CHECKING_EXISTING)
    records = adapter.detect_existing(original_path)
    policy = job["existing_metadata_policy"]
    audit["existing_records"] = [{"tag": r.tag_key, "raw": r.raw[:80]} for r in records]
    audit["policy"] = policy
    if records and policy == jobs.POLICY_REJECT:
        raise PipelineError(AIGC_METADATA_EXISTS, "文件已存在 AIGC 标识且策略为拒绝",
                            retryable=False)

    # ---- 写入（§9.3：replace 先整体移除再写入一份）----
    store.update(job_id, stage=jobs.STAGE_WRITING)
    staging_result = storage.copy_to_staging(original_path, suffix=suffix)
    staging_noaigc = None
    try:
        if records and policy == jobs.POLICY_REPLACE:
            staging_noaigc = storage.copy_to_staging(original_path,
                                                     suffix=f"_noaigc{suffix}")
            adapter.remove_aigc(original_path, staging_noaigc)
            adapter.write_metadata(staging_noaigc, staging_result, submitted)
        else:
            adapter.write_metadata(original_path, staging_result, submitted)
    except AdapterError as e:
        raise PipelineError(METADATA_WRITE_FAILED, str(e)) from None
    finally:
        if staging_noaigc:
            storage.discard(staging_noaigc)

    # ---- 回读校验（§9.4）----
    store.update(job_id, stage=jobs.STAGE_VERIFYING_METADATA)
    validation = _verify_readback(staging_result, submitted, adapter)
    embedded = json.loads(validation.pop("_embedded_json"))
    audit["readback_checks"] = validation

    # ---- 媒体完整性（§9.4）----
    store.update(job_id, stage=jobs.STAGE_VERIFYING_MEDIA)
    try:
        media = adapter.media_integrity_check(
            original_path, staging_result,
            duration_tolerance=settings.limits.duration_tolerance_seconds)
    except AdapterError as e:
        raise PipelineError(MEDIA_INTEGRITY_FAILED, str(e)) from None
    validation["media_integrity_valid"] = media.passed
    audit["media_before"] = media.before
    audit["media_after"] = media.after
    if not media.passed:
        raise PipelineError(MEDIA_INTEGRITY_FAILED,
                            f"媒体完整性校验失败: {media.reason}")

    # ---- 编号登记（§6.1 b：与图片打标、修复工作台共用同一份登记库）----
    # 放在发布之前：同号异内容要在成品落地前就拦下，否则会留下一个"有产出、
    # 无登记"的文件——修复工作台的来源核对将永远查不到它。
    # 指纹取自**待发布的成品**而不是原文件：登记库要与"以后能被别人读到的那份
    # 内容"绑定。图片/视频的指纹本来就与写入无关，两者等价；文本则不然——
    # replace 会删掉旧载体（HTML 注释），若按原文件登记，日后拿成品去核对就会
    # 撞成"同号异内容"，把正常文件拦在修复之外。
    try:
        fingerprint, fingerprint_kind = adapter.content_fingerprint(staging_result)
    except AdapterError as e:
        raise PipelineError(MEDIA_INTEGRITY_FAILED,
                            f"无法计算内容指纹: {e}") from None
    try:
        reservation = _identifier_registry(settings).reserve({"AIGC": submitted},
                                                             fingerprint)
    except DuplicateIdentifierError as e:
        raise PipelineError(
            AIGC_IDENTIFIER_DUPLICATE,
            f"{'ProduceID' if e.role == 'producer' else 'PropagateID'}"
            " 已被同一提供者用于其他内容") from None
    audit["content_fingerprint"] = {"kind": fingerprint_kind, "value": fingerprint}

    # ---- 原子发布（§9.2）----
    try:
        store.update(job_id, stage=jobs.STAGE_PUBLISHING)
        output_stored = storage.new_stored_name(suffix)
        final_path = storage.publish(staging_result, output_stored)
        output_size = final_path.stat().st_size
        output_sha256 = storage.sha256_of(final_path)
        original_display = safe_display_name(job["original_file_name"])
        stem = original_display.rsplit(".", 1)[0] if "." in original_display else original_display
        output_display = f"{stem}_labeled{suffix}"
        created = job["created_at"]
        expires = util.add_hours_iso(created, settings.storage.output_retention_hours)

        store.update(
            job_id, status=jobs.SUCCEEDED, stage=jobs.STAGE_COMPLETED, progress=100,
            updated_at=util.now_iso(),
            output_file_name=output_display,
            output_mime_type=mime,
            output_size_bytes=output_size,
            output_sha256=output_sha256,
            output_stored_name=output_stored,
            carrier=adapter.carrier_id,
            expires_at=expires,
            embedded_aigc={"AIGC": embedded},
            validation=validation,
            audit=audit,
        )
    except BaseException:
        # 发布或落库失败即撤回登记：宁可没有记录，也不要一条指向不存在产出的记录
        reservation.rollback()
        raise
    reservation.commit()


def _identifier_registry(settings: Any):
    """编号登记库：三种模态的打标与修复工作台共用同一个库（§6.1 b）。

    图片侧走 image_adapter 时，登记在写入服务内部完成（同一张表）；本函数供
    视频/文档的适配器路径使用。刻意**不再新建第二张表或第二个文件**——
    "编号登记库必须只有一个"是硬约束，两张表互不可见会让同号异内容互相漏过。
    """
    from ..metadata.identifier_registry import SQLiteIdentifierRegistry
    return SQLiteIdentifierRegistry(resolve_registry_path(settings))


def _image_service(settings: Any):
    """构造图片写入服务（ExifTool 客户端 + 编号登记库 + 写入适配器）。"""
    from ..metadata.identifier_registry import SQLiteIdentifierRegistry
    from ..metadata.image_adapter import ImageMetadataService

    client = build_client(settings.paths.exiftool, settings.paths.exiftool_config)
    registry = SQLiteIdentifierRegistry(resolve_registry_path(settings))
    return ImageMetadataService(client, registry)


def _execute_image(job: dict, store: JobStore, storage: FileStorage,
                   settings: Any) -> None:
    """图片打标：整条写入链路由 app.metadata.image_adapter 完成。

    与视频分支的差别在于职责边界：图片写入服务自己就把
    「写入前交叉读取 → 策略 → 写入 → 回读校验 → 媒体完整性 → 原子发布 →
    编号登记（失败则撤回结果）」做完了，所以这里**不再**调用
    ``_verify_readback`` 与 ``adapter.media_integrity_check``——那会变成
    同一件事的第二套实现，正是合并时要消除的东西。
    """
    from ..metadata.image_adapter import ExistingMetadataPolicy, ImageMetadataError

    job_id = job["job_id"]
    suffix = suffix_for_mime(job["detected_mime_type"])
    original_path = storage.original_path(job["original_stored_name"])
    submitted = json.loads(job["submitted_aigc"])
    audit = json.loads(job.get("audit") or "{}")
    audit["policy"] = job["existing_metadata_policy"]

    store.update(job_id, status=jobs.RUNNING, stage=jobs.STAGE_INSPECTING_FILE,
                 updated_at=util.now_iso())

    # 写入服务要求目标文件尚不存在，并自行原子发布到该路径
    output_stored = storage.new_stored_name(suffix)
    output_path = storage.output_path(output_stored)

    def on_stage(stage: str) -> None:
        store.update(job_id, stage=_IMAGE_STAGE.get(stage, stage),
                     updated_at=util.now_iso())

    try:
        result = _image_service(settings).write(
            str(original_path), str(output_path), {"AIGC": submitted},
            policy=ExistingMetadataPolicy(job["existing_metadata_policy"]),
            initial_write=True,
            stage_callback=on_stage,
        )
    except ImageMetadataError as e:
        # 错误码词表与视频侧一致，直接透传（前端按 code 展示）
        raise PipelineError(e.code, str(e), retryable=False) from None
    except OSError as e:
        raise PipelineError(METADATA_WRITE_FAILED, f"图片写入失败: {e}") from None

    final_path = Path(result.output_path)
    validation = {
        "read_back_succeeded": result.validation.read_back_succeeded,
        "schema_valid": result.validation.schema_valid,
        "single_aigc_record": result.validation.single_aigc_record,
        "media_integrity_valid": result.validation.media_integrity_valid,
    }
    audit["adapter_version"] = result.adapter_version
    audit["input_sha256"] = result.input_sha256

    original_display = safe_display_name(job["original_file_name"])
    stem = original_display.rsplit(".", 1)[0] if "." in original_display else original_display
    store.update(
        job_id, status=jobs.SUCCEEDED, stage=jobs.STAGE_COMPLETED, progress=100,
        updated_at=util.now_iso(),
        output_file_name=f"{stem}_labeled{suffix}",
        output_mime_type=result.mime_type,
        output_size_bytes=final_path.stat().st_size,
        output_sha256=result.output_sha256,
        output_stored_name=output_stored,
        carrier=result.carrier,
        expires_at=util.add_hours_iso(job["created_at"],
                                      settings.storage.output_retention_hours),
        # 图片服务的 embedded_metadata 已是外层 {"AIGC": {...}}，与视频侧同形
        embedded_aigc=result.embedded_metadata,
        validation=validation,
        audit=audit,
    )


def _verify_readback(result_path, submitted: dict, adapter) -> dict:
    """§9.4 回读校验：恰好一份、可解析、过 Schema、与提交对象逐字段一致。"""
    records = adapter.detect_existing(result_path)
    if len(records) == 0:
        raise PipelineError(METADATA_READBACK_FAILED, "写入后未读到 AIGC 标识")
    if len(records) > 1:
        raise PipelineError(AIGC_DUPLICATE_RECORDS,
                            f"检测到 {len(records)} 处 AIGC 标识（应仅一份）")
    rec = records[0]
    if rec.aigc is None:
        raise PipelineError(METADATA_READBACK_FAILED, "回读的 AIGC 字符串无法解析为 JSON")

    errors = validate_readback(rec.aigc)
    if errors:
        raise PipelineError(METADATA_READBACK_FAILED,
                            "回读对象未通过 Schema 校验: " + "; ".join(e["reason"] for e in errors))

    fields_match = rec.aigc == submitted
    if not fields_match:
        raise PipelineError(METADATA_READBACK_FAILED, "回读对象与提交对象逐字段不一致")

    validation = {
        "read_back_succeeded": True,
        "schema_valid": True,
        "single_aigc_record": True,
        "fields_match": True,
        "carrier_tag": rec.tag_key,
        "_embedded_json": json.dumps(rec.aigc, ensure_ascii=False),
    }
    return validation


def validate_readback(aigc_obj: dict) -> list[dict]:
    from .aigc import validate_aigc
    return validate_aigc(aigc_obj)


def _fail(job: dict, store: JobStore, code: str, message: str,
          retryable: bool, stage: str, job_retention_hours: int = 168) -> None:
    # 失败任务也设过期时间（创建 + 任务保留期），以便 §12.3 清理原文件与任务记录。
    expires = util.add_hours_iso(job.get("created_at"), job_retention_hours)
    store.mark_terminal(job["job_id"], status=jobs.FAILED, stage=stage,
                        updated_at=util.now_iso(), error_code=code,
                        error_message=message, retryable=retryable,
                        expires_at=expires)
