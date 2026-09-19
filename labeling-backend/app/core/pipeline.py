"""标注任务流水线（开发手册 §8.2 / §9）：统一引擎 → 载体适配器。

对应 UniMark 架构中的 unified_engine：按真实文件类型把任务分发给对应适配器。
完整流程：检查已有标识 → 写入（或先整体替换再写入）→ 回读校验 → 媒体完整性
校验 → 原子发布。任一强制步骤失败即保留审计信息并标记 failed。
"""
from __future__ import annotations

import json
from typing import Any

from . import jobs, util
from .errors import INTERNAL_ERROR
from ..adapters import AdapterError, get_adapter
from .mimetype import suffix_for_mime
from .storage import FileStorage, safe_display_name
from .store import JobStore


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
        raise PipelineError(jobs.AIGC_METADATA_EXISTS, "文件已存在 AIGC 标识且策略为拒绝",
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
        raise PipelineError(jobs.METADATA_WRITE_FAILED, str(e)) from None
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
        raise PipelineError(jobs.MEDIA_INTEGRITY_FAILED, str(e)) from None
    validation["media_integrity_valid"] = media.passed
    audit["media_before"] = media.before
    audit["media_after"] = media.after
    if not media.passed:
        raise PipelineError(jobs.MEDIA_INTEGRITY_FAILED,
                            f"媒体完整性校验失败: {media.reason}")

    # ---- 原子发布（§9.2）----
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


def _verify_readback(result_path, submitted: dict, adapter) -> dict:
    """§9.4 回读校验：恰好一份、可解析、过 Schema、与提交对象逐字段一致。"""
    records = adapter.detect_existing(result_path)
    if len(records) == 0:
        raise PipelineError(jobs.METADATA_READBACK_FAILED, "写入后未读到 AIGC 标识")
    if len(records) > 1:
        raise PipelineError(jobs.AIGC_DUPLICATE_RECORDS,
                            f"检测到 {len(records)} 处 AIGC 标识（应仅一份）")
    rec = records[0]
    if rec.aigc is None:
        raise PipelineError(jobs.METADATA_READBACK_FAILED, "回读的 AIGC 字符串无法解析为 JSON")

    errors = validate_readback(rec.aigc)
    if errors:
        raise PipelineError(jobs.METADATA_READBACK_FAILED,
                            "回读对象未通过 Schema 校验: " + "; ".join(e["reason"] for e in errors))

    fields_match = rec.aigc == submitted
    if not fields_match:
        raise PipelineError(jobs.METADATA_READBACK_FAILED, "回读对象与提交对象逐字段不一致")

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
