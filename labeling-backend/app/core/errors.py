"""错误码与异常（开发手册 §11）。

同步错误（HTTP 阶段返回）与异步失败（任务 status=failed 时返回）共用一套错误码，
便于前端统一映射为明确提示。
"""
from __future__ import annotations


class ApiError(Exception):
    """携带 HTTP 状态码与机器错误码的接口异常。"""

    def __init__(self, status: int, code: str, message: str,
                 field_errors: list[dict] | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field_errors = field_errors or []
        self.retryable = retryable


# ---- 同步错误（§11.2 上表）----
INVALID_MULTIPART = "INVALID_MULTIPART"            # 400
FILE_TOO_LARGE = "FILE_TOO_LARGE"                  # 413
UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"  # 415
AIGC_SCHEMA_INVALID = "AIGC_SCHEMA_INVALID"        # 422
AIGC_CHARACTER_INVALID = "AIGC_CHARACTER_INVALID"  # 422
MODALITY_MISMATCH = "MODALITY_MISMATCH"            # 422
AIGC_METADATA_EXISTS = "AIGC_METADATA_EXISTS"      # 409
JOB_NOT_FOUND = "JOB_NOT_FOUND"                    # 404
INTERNAL_ERROR = "INTERNAL_ERROR"                  # 500

# ---- 异步失败（§11.2 下表，status=failed）----
METADATA_WRITE_FAILED = "METADATA_WRITE_FAILED"
METADATA_READBACK_FAILED = "METADATA_READBACK_FAILED"
AIGC_DUPLICATE_RECORDS = "AIGC_DUPLICATE_RECORDS"
MEDIA_INTEGRITY_FAILED = "MEDIA_INTEGRITY_FAILED"
JOB_TIMEOUT = "JOB_TIMEOUT"
