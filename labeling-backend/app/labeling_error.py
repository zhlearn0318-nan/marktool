"""多模态与场景标识流程的业务错误。

与 :class:`app.core.errors.ApiError` 分开：这里不携带 HTTP 状态码，
由 API 层映射为手册 §11 的错误信封。流水线、CLI 与测试共用同一套异常。
"""
from __future__ import annotations


class LabelingError(Exception):
    def __init__(self, code: str, message: str, details: list | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
