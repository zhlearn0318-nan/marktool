"""API 响应组装（开发手册 §7.2 / §7.3 / §11.1）。"""
from __future__ import annotations

import json
from typing import Any

from ..core.jobs import SUCCEEDED


def _input_block(row: dict) -> dict | None:
    if not row.get("original_file_name"):
        return None
    return {
        "original_file_name": row["original_file_name"],
        "detected_mime_type": row["detected_mime_type"],
        "size_bytes": row["input_size_bytes"],
        "sha256": row["input_sha256"],
    }


def _output_block(row: dict) -> dict | None:
    if not row.get("output_stored_name"):
        return None
    return {
        "file_name": row["output_file_name"],
        "mime_type": row["output_mime_type"],
        "size_bytes": row["output_size_bytes"],
        "sha256": row["output_sha256"],
        "carrier": row["carrier"],
        "download_url": f"/api/v1/metadata-label-jobs/{row['job_id']}/output",
        "expires_at": row["expires_at"],
    }


def _error_block(row: dict) -> dict | None:
    if not row.get("error_code"):
        return None
    return {
        "code": row["error_code"],
        "message": row["error_message"],
        "retryable": bool(row["retryable"]),
    }


def _embedded(row: dict) -> dict | None:
    raw = row.get("embedded_aigc")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _validation(row: dict) -> dict | None:
    raw = row.get("validation")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def job_to_response(row: dict, request_id: str | None = None) -> dict:
    base = {
        "request_id": request_id or row["request_id"],
        "job_id": row["job_id"],
        "status": row["status"],
        "stage": row["stage"],
        "progress": row["progress"],
        "created_at": row["created_at"],
        "links": {
            "self": f"/api/v1/metadata-label-jobs/{row['job_id']}",
            "output": (f"/api/v1/metadata-label-jobs/{row['job_id']}/output"
                       if row["status"] == SUCCEEDED and row.get("output_stored_name") else None),
        },
    }
    if row["status"] != "queued":
        base["updated_at"] = row["updated_at"]
        base["input"] = _input_block(row)
    if row["status"] == SUCCEEDED:
        base["output"] = _output_block(row)
        base["embedded_metadata"] = _embedded(row)
        base["validation"] = _validation(row)
    if row["status"] == "failed":
        base["error"] = _error_block(row)
    return base


def error_response(request_id: str, code: str, message: str,
                   field_errors: list[dict] | None = None) -> dict:
    return {
        "request_id": request_id,
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
        },
    }
