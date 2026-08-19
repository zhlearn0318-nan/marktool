import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class IdempotencyConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreateJobResult:
    record: dict[str, Any]
    created: bool


class SQLiteMetadataJobStore:
    """异步标注任务的单机持久化存储。"""

    _JSON_COLUMNS = {
        "request_json",
        "embedded_metadata_json",
        "validation_json",
        "error_details_json",
    }

    def __init__(self, database_path: str):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata_label_jobs (
                    job_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'succeeded', 'failed')
                    ),
                    stage TEXT NOT NULL,
                    progress INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    expires_at TEXT,
                    original_file_name TEXT NOT NULL,
                    detected_mime_type TEXT NOT NULL,
                    input_size_bytes INTEGER NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    output_file_name TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    output_mime_type TEXT,
                    output_size_bytes INTEGER,
                    output_sha256 TEXT,
                    carrier TEXT,
                    adapter_version TEXT,
                    embedded_metadata_json TEXT,
                    validation_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    error_retryable INTEGER,
                    error_details_json TEXT,
                    failed_at_stage TEXT
                )
                """
            )

    @classmethod
    def _decode(cls, row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        record = dict(row)
        for column in cls._JSON_COLUMNS:
            value = record.pop(column, None)
            record[column.removesuffix("_json")] = (
                json.loads(value) if value is not None else None
            )
        if record.get("error_retryable") is not None:
            record["error_retryable"] = bool(record["error_retryable"])
        return record

    def create(self, values: dict[str, Any]) -> CreateJobResult:
        columns = (
            "job_id",
            "request_id",
            "idempotency_key",
            "request_fingerprint",
            "status",
            "stage",
            "progress",
            "created_at",
            "updated_at",
            "original_file_name",
            "detected_mime_type",
            "input_size_bytes",
            "input_sha256",
            "input_path",
            "output_path",
            "output_file_name",
            "request_json",
        )
        encoded = dict(values)
        encoded["request_json"] = json.dumps(
            values["request_json"], ensure_ascii=False, separators=(",", ":")
        )
        placeholders = ", ".join("?" for _ in columns)
        try:
            with self._connect() as connection:
                connection.execute(
                    f"INSERT INTO metadata_label_jobs ({', '.join(columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(encoded[column] for column in columns),
                )
        except sqlite3.IntegrityError as exc:
            key = values.get("idempotency_key")
            existing = self.get_by_idempotency_key(key) if key else None
            if existing and existing["request_fingerprint"] == values["request_fingerprint"]:
                return CreateJobResult(existing, created=False)
            if existing:
                raise IdempotencyConflictError(
                    "同一 Idempotency-Key 已用于另一份文件或参数"
                ) from exc
            raise
        record = self.get(values["job_id"])
        if record is None:
            raise RuntimeError("任务创建后无法读取")
        return CreateJobResult(record, created=True)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM metadata_label_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._decode(row)

    def get_by_idempotency_key(self, key: Optional[str]) -> Optional[dict[str, Any]]:
        if not key:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM metadata_label_jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return self._decode(row)

    def claim(self, job_id: str, timestamp: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE metadata_label_jobs
                SET status = 'running', stage = 'writing_metadata',
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (timestamp, timestamp, job_id),
            )
            return cursor.rowcount == 1

    def update_stage(self, job_id: str, stage: str, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_label_jobs
                SET stage = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (stage, timestamp, job_id),
            )

    def succeed(
        self,
        job_id: str,
        *,
        timestamp: str,
        expires_at: str,
        output_mime_type: str,
        output_size_bytes: int,
        output_sha256: str,
        carrier: str,
        adapter_version: str,
        embedded_metadata: dict,
        validation: dict,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_label_jobs
                SET status = 'succeeded', stage = 'completed', progress = 100,
                    updated_at = ?, completed_at = ?, expires_at = ?,
                    output_mime_type = ?, output_size_bytes = ?, output_sha256 = ?,
                    carrier = ?, adapter_version = ?,
                    embedded_metadata_json = ?, validation_json = ?,
                    error_code = NULL, error_message = NULL,
                    error_retryable = NULL, error_details_json = NULL
                WHERE job_id = ? AND status = 'running'
                """,
                (
                    timestamp,
                    timestamp,
                    expires_at,
                    output_mime_type,
                    output_size_bytes,
                    output_sha256,
                    carrier,
                    adapter_version,
                    json.dumps(embedded_metadata, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(validation, ensure_ascii=False, separators=(",", ":")),
                    job_id,
                ),
            )

    def fail(
        self,
        job_id: str,
        *,
        timestamp: str,
        expires_at: str,
        code: str,
        message: str,
        retryable: bool,
        details: Optional[list[str]] = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_label_jobs
                SET failed_at_stage = stage,
                    status = 'failed', stage = 'failed', progress = NULL,
                    updated_at = ?, completed_at = ?, expires_at = ?,
                    error_code = ?, error_message = ?, error_retryable = ?,
                    error_details_json = ?
                WHERE job_id = ? AND status IN ('queued', 'running')
                """,
                (
                    timestamp,
                    timestamp,
                    expires_at,
                    code,
                    message,
                    int(retryable),
                    json.dumps(details or [], ensure_ascii=False),
                    job_id,
                ),
            )

    def recover_incomplete(self, timestamp: str) -> list[str]:
        """进程重启后把未完成任务放回队列。"""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_label_jobs
                SET status = 'queued', stage = 'queued', progress = NULL,
                    updated_at = ?
                WHERE status = 'running'
                """,
                (timestamp,),
            )
            rows = connection.execute(
                "SELECT job_id FROM metadata_label_jobs WHERE status = 'queued'"
            ).fetchall()
        return [row[0] for row in rows]

    def expired(self, timestamp: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM metadata_label_jobs
                WHERE expires_at IS NOT NULL AND expires_at <= ?
                  AND status IN ('succeeded', 'failed')
                """,
                (timestamp,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def delete(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM metadata_label_jobs WHERE job_id = ?", (job_id,)
            )
