"""SQLite 任务与审计存储（开发手册 §12.1）。

数据库只保存任务状态、文件引用与审计字段；大体积视频二进制放文件系统。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class JobStore:
    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 查询 ----
    def get(self, job_id: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def find_by_idempotency_key(self, key: str) -> dict | None:
        if not key:
            return None
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1",
                    (key,)).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def find_produce_id_used(self, produce_id: str) -> bool:
        """§5.6 ProduceID 唯一性：是否已有任务使用过该编号（SQLite JSON1 提取）。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM jobs "
                    "WHERE json_extract(submitted_aigc, '$.AIGC.ProduceID') = ? LIMIT 1",
                    (produce_id,)).fetchone()
            finally:
                conn.close()
        return row is not None

    # ---- 写入 ----
    def create(self, job: dict) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO jobs (
                        job_id, request_id, idempotency_key, status, stage, progress,
                        created_at, updated_at, original_file_name, detected_mime_type,
                        input_size_bytes, input_sha256, original_stored_name,
                        existing_metadata_policy, submitted_aigc, audit
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job["job_id"], job["request_id"], job.get("idempotency_key"),
                     job["status"], job["stage"], job.get("progress"),
                     job["created_at"], job["updated_at"],
                     job["input"]["original_file_name"], job["input"]["detected_mime_type"],
                     job["input"]["size_bytes"], job["input"]["sha256"],
                     job["original_stored_name"], job["existing_metadata_policy"],
                     json.dumps(job["submitted_aigc"], ensure_ascii=False),
                     json.dumps(job.get("audit", {}), ensure_ascii=False)))
                conn.commit()
            finally:
                conn.close()

    def update(self, job_id: str, **fields: Any) -> None:
        """按字段更新。支持 status/stage/progress/updated_at 与 output/validation/
        embedded_aigc/error_code/error_message/retryable/audit（后几者需先 JSON 化）。"""
        setters = []
        params = []
        for key, val in fields.items():
            if val is None:
                continue
            if key in ("validation", "audit", "embedded_aigc"):
                val = json.dumps(val, ensure_ascii=False)
            setters.append(f"{key} = ?")
            params.append(val)
        if not setters:
            return
        params.append(job_id)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"UPDATE jobs SET {', '.join(setters)} WHERE job_id = ?", params)
                conn.commit()
            finally:
                conn.close()

    def mark_terminal(self, job_id: str, status: str, stage: str, updated_at: str,
                      error_code: str | None = None, error_message: str | None = None,
                      retryable: bool | None = None, expires_at: str | None = None) -> None:
        self.update(job_id, status=status, stage=stage, updated_at=updated_at,
                    error_code=error_code, error_message=error_message,
                    retryable=retryable, expires_at=expires_at)

    def list_expired(self, cutoff_iso: str, statuses: tuple[str, ...]) -> list[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT job_id, original_stored_name, output_stored_name FROM jobs "
                    "WHERE status IN (%s) AND expires_at IS NOT NULL AND expires_at < ?"
                    % ",".join("?" * len(statuses)),
                    (*statuses, cutoff_iso)).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]

    def delete_jobs(self, job_ids: list[str]) -> None:
        """过期清理后删除任务记录（§12.3：任务记录保留时间可配置）。"""
        if not job_ids:
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany("DELETE FROM jobs WHERE job_id = ?",
                                 [(jid,) for jid in job_ids])
                conn.commit()
            finally:
                conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id                TEXT PRIMARY KEY,
    request_id            TEXT,
    idempotency_key       TEXT,
    status                TEXT NOT NULL,
    stage                 TEXT NOT NULL,
    progress              INTEGER,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    original_file_name    TEXT,
    detected_mime_type    TEXT,
    input_size_bytes      INTEGER,
    input_sha256          TEXT,
    original_stored_name  TEXT,
    existing_metadata_policy TEXT,
    submitted_aigc        TEXT,
    embedded_aigc         TEXT,
    output_file_name      TEXT,
    output_mime_type      TEXT,
    output_size_bytes     INTEGER,
    output_sha256         TEXT,
    output_stored_name    TEXT,
    carrier               TEXT,
    expires_at            TEXT,
    validation            TEXT,
    error_code            TEXT,
    error_message         TEXT,
    retryable             INTEGER,
    audit                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_idem ON jobs(idempotency_key);
"""
