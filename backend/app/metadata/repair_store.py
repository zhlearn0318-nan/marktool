import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


_PLAN_JSON_COLUMNS = {"inspection_json", "draft_json", "trusted_input_json"}
_JOB_JSON_COLUMNS = {"validation_json", "error_details_json"}


class RepairPlanConflictError(RuntimeError):
    pass


class SQLiteMetadataRepairStore:
    """修复计划、异步任务和追加式审计事件的单机存储。"""

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata_repair_plans (
                    plan_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'consumed', 'expired')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    input_expires_at TEXT,
                    input_purged_at TEXT,
                    original_file_name TEXT NOT NULL,
                    detected_mime_type TEXT NOT NULL,
                    input_size_bytes INTEGER NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    pixel_sha256 TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    inspection_json TEXT NOT NULL,
                    draft_json TEXT NOT NULL,
                    trusted_input_json TEXT,
                    plan_hash TEXT NOT NULL,
                    job_id TEXT,
                    operator_label TEXT,
                    identity_verified INTEGER,
                    confirmation_method TEXT,
                    confirmed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS metadata_repair_jobs (
                    job_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'succeeded', 'failed')
                    ),
                    stage TEXT NOT NULL,
                    progress INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    files_expires_at TEXT,
                    output_path TEXT NOT NULL,
                    output_file_name TEXT NOT NULL,
                    output_mime_type TEXT,
                    output_size_bytes INTEGER,
                    output_sha256 TEXT,
                    validation_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    error_retryable INTEGER,
                    error_details_json TEXT,
                    FOREIGN KEY (plan_id) REFERENCES metadata_repair_plans(plan_id)
                );

                CREATE TABLE IF NOT EXISTS metadata_repair_audit_events (
                    event_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    job_id TEXT,
                    sequence_number INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    UNIQUE(plan_id, sequence_number)
                );

                CREATE TABLE IF NOT EXISTS metadata_repair_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    job_id TEXT,
                    artifact_type TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    removed_at TEXT,
                    UNIQUE(plan_id, artifact_type, content_sha256)
                );
                """
            )
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(metadata_repair_plans)"
                ).fetchall()
            }
            if "input_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE metadata_repair_plans ADD COLUMN input_expires_at TEXT"
                )
            if "input_purged_at" not in columns:
                connection.execute(
                    "ALTER TABLE metadata_repair_plans ADD COLUMN input_purged_at TEXT"
                )

    @staticmethod
    def _encode_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _decode_plan(cls, row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        for column in _PLAN_JSON_COLUMNS:
            value = result.pop(column, None)
            result[column.removesuffix("_json")] = json.loads(value) if value else None
        if result.get("identity_verified") is not None:
            result["identity_verified"] = bool(result["identity_verified"])
        return result

    @classmethod
    def _decode_job(cls, row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        result = dict(row)
        for column in _JOB_JSON_COLUMNS:
            value = result.pop(column, None)
            result[column.removesuffix("_json")] = json.loads(value) if value else None
        if result.get("error_retryable") is not None:
            result["error_retryable"] = bool(result["error_retryable"])
        return result

    def create_plan(self, values: dict[str, Any]) -> dict[str, Any]:
        columns = (
            "plan_id", "request_id", "status", "created_at", "updated_at",
            "expires_at", "input_expires_at", "original_file_name", "detected_mime_type",
            "input_size_bytes", "input_sha256", "pixel_sha256", "input_path",
            "inspection_json", "draft_json", "trusted_input_json", "plan_hash",
        )
        encoded = dict(values)
        for name in ("inspection_json", "draft_json", "trusted_input_json"):
            value = values.get(name)
            encoded[name] = self._encode_json(value) if value is not None else None
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO metadata_repair_plans ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(encoded[column] for column in columns),
            )
        record = self.get_plan(values["plan_id"])
        if record is None:
            raise RuntimeError("修复计划创建后无法读取")
        return record

    def get_plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM metadata_repair_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return self._decode_plan(row)

    def confirm_and_create_job(
        self,
        *,
        plan_id: str,
        expected_plan_hash: str,
        job_id: str,
        request_id: str,
        timestamp: str,
        operator_label: str,
        output_path: str,
        output_file_name: str,
        confirmation_event_id: str,
        confirmation_payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = connection.execute(
                "SELECT * FROM metadata_repair_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if plan is None:
                raise RepairPlanConflictError("修复计划不存在")
            if plan["status"] != "pending":
                raise RepairPlanConflictError("修复计划已使用或已过期")
            if plan["plan_hash"] != expected_plan_hash:
                raise RepairPlanConflictError("修复计划哈希不一致")
            if plan["expires_at"] <= timestamp:
                connection.execute(
                    "UPDATE metadata_repair_plans SET status='expired', updated_at=? "
                    "WHERE plan_id=?",
                    (timestamp, plan_id),
                )
                # 先持久化过期状态，再向调用方报告冲突；否则异常会让 with
                # 上下文回滚 UPDATE，计划会一直停留在 pending。
                connection.commit()
                raise RepairPlanConflictError("修复计划已过期")
            draft = json.loads(plan["draft_json"])
            if not draft.get("executable"):
                raise RepairPlanConflictError("该计划需要人工复核，不能创建自动修复任务")

            connection.execute(
                """
                INSERT INTO metadata_repair_jobs (
                    job_id, plan_id, request_id, status, stage, progress,
                    created_at, updated_at, output_path, output_file_name
                ) VALUES (?, ?, ?, 'queued', 'queued', NULL, ?, ?, ?, ?)
                """,
                (
                    job_id, plan_id, request_id, timestamp, timestamp,
                    output_path, output_file_name,
                ),
            )
            connection.execute(
                """
                UPDATE metadata_repair_plans
                SET status='consumed', updated_at=?, job_id=?, operator_label=?,
                    identity_verified=0, confirmation_method='manual_api',
                    confirmed_at=?
                WHERE plan_id=?
                """,
                (timestamp, job_id, operator_label, timestamp, plan_id),
            )
            previous = connection.execute(
                """
                SELECT sequence_number, event_hash
                FROM metadata_repair_audit_events
                WHERE plan_id=? ORDER BY sequence_number DESC LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            sequence = (previous["sequence_number"] + 1) if previous else 1
            previous_hash = previous["event_hash"] if previous else "0" * 64
            canonical = self._encode_json(
                {
                    "plan_id": plan_id,
                    "job_id": job_id,
                    "sequence_number": sequence,
                    "event_type": "repair_confirmed",
                    "created_at": timestamp,
                    "payload": confirmation_payload,
                    "previous_event_hash": previous_hash,
                }
            )
            event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO metadata_repair_audit_events (
                    event_id, plan_id, job_id, sequence_number, event_type,
                    created_at, payload_json, previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, 'repair_confirmed', ?, ?, ?, ?)
                """,
                (
                    confirmation_event_id, plan_id, job_id, sequence, timestamp,
                    self._encode_json(confirmation_payload), previous_hash, event_hash,
                ),
            )
        record = self.get_job(job_id)
        if record is None:
            raise RuntimeError("修复任务创建后无法读取")
        return record

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM metadata_repair_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._decode_job(row)

    def claim_job(self, job_id: str, timestamp: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE metadata_repair_jobs
                SET status='running', stage='archiving_original', updated_at=?
                WHERE job_id=? AND status='queued'
                """,
                (timestamp, job_id),
            )
            return cursor.rowcount == 1

    def update_stage(self, job_id: str, stage: str, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE metadata_repair_jobs SET stage=?, updated_at=? "
                "WHERE job_id=? AND status='running'",
                (stage, timestamp, job_id),
            )

    def succeed_job(
        self,
        job_id: str,
        *,
        timestamp: str,
        files_expires_at: str,
        output_mime_type: str,
        output_size_bytes: int,
        output_sha256: str,
        validation: dict,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_repair_jobs
                SET status='succeeded', stage='completed', progress=100,
                    updated_at=?, completed_at=?, files_expires_at=?,
                    output_mime_type=?, output_size_bytes=?, output_sha256=?,
                    validation_json=?, error_code=NULL, error_message=NULL,
                    error_retryable=NULL, error_details_json=NULL
                WHERE job_id=? AND status='running'
                """,
                (
                    timestamp, timestamp, files_expires_at, output_mime_type,
                    output_size_bytes, output_sha256, self._encode_json(validation), job_id,
                ),
            )

    def succeed_job_with_event(
        self,
        job_id: str,
        *,
        plan_id: str,
        event_id: str,
        timestamp: str,
        files_expires_at: str,
        output_mime_type: str,
        output_size_bytes: int,
        output_sha256: str,
        validation: dict,
        event_payload: dict[str, Any],
    ) -> None:
        """在同一事务中完成任务和成功审计，避免二者状态分叉。"""
        payload_json = self._encode_json(event_payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """
                SELECT sequence_number, event_hash
                FROM metadata_repair_audit_events
                WHERE plan_id=? ORDER BY sequence_number DESC LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            sequence = (previous["sequence_number"] + 1) if previous else 1
            previous_hash = previous["event_hash"] if previous else "0" * 64
            canonical = self._encode_json(
                {
                    "plan_id": plan_id,
                    "job_id": job_id,
                    "sequence_number": sequence,
                    "event_type": "repair_succeeded",
                    "created_at": timestamp,
                    "payload": event_payload,
                    "previous_event_hash": previous_hash,
                }
            )
            event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            cursor = connection.execute(
                """
                UPDATE metadata_repair_jobs
                SET status='succeeded', stage='completed', progress=100,
                    updated_at=?, completed_at=?, files_expires_at=?,
                    output_mime_type=?, output_size_bytes=?, output_sha256=?,
                    validation_json=?, error_code=NULL, error_message=NULL,
                    error_retryable=NULL, error_details_json=NULL
                WHERE job_id=? AND status='running'
                """,
                (
                    timestamp, timestamp, files_expires_at, output_mime_type,
                    output_size_bytes, output_sha256,
                    self._encode_json(validation), job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("修复任务不在可完成状态")
            connection.execute(
                """
                INSERT INTO metadata_repair_audit_events (
                    event_id, plan_id, job_id, sequence_number, event_type,
                    created_at, payload_json, previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, 'repair_succeeded', ?, ?, ?, ?)
                """,
                (
                    event_id, plan_id, job_id, sequence, timestamp,
                    payload_json, previous_hash, event_hash,
                ),
            )

    def fail_job(
        self,
        job_id: str,
        *,
        timestamp: str,
        files_expires_at: str,
        code: str,
        message: str,
        retryable: bool,
        details: Optional[list[str]] = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_repair_jobs
                SET status='failed', stage='failed', progress=NULL,
                    updated_at=?, completed_at=?, files_expires_at=?,
                    error_code=?, error_message=?, error_retryable=?,
                    error_details_json=?
                WHERE job_id=? AND status IN ('queued', 'running')
                """,
                (
                    timestamp, timestamp, files_expires_at, code, message,
                    int(retryable), self._encode_json(details or []), job_id,
                ),
            )

    def recover_incomplete(self, timestamp: str) -> list[str]:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE metadata_repair_jobs
                SET status='queued', stage='queued', progress=NULL, updated_at=?
                WHERE status='running'
                """,
                (timestamp,),
            )
            rows = connection.execute(
                "SELECT job_id FROM metadata_repair_jobs WHERE status='queued'"
            ).fetchall()
        return [row[0] for row in rows]

    def append_event(
        self,
        *,
        event_id: str,
        plan_id: str,
        job_id: Optional[str],
        event_type: str,
        timestamp: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload_json = self._encode_json(payload)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                """
                SELECT sequence_number, event_hash
                FROM metadata_repair_audit_events
                WHERE plan_id=? ORDER BY sequence_number DESC LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            sequence = (previous["sequence_number"] + 1) if previous else 1
            previous_hash = previous["event_hash"] if previous else "0" * 64
            canonical = self._encode_json(
                {
                    "plan_id": plan_id,
                    "job_id": job_id,
                    "sequence_number": sequence,
                    "event_type": event_type,
                    "created_at": timestamp,
                    "payload": payload,
                    "previous_event_hash": previous_hash,
                }
            )
            event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT INTO metadata_repair_audit_events (
                    event_id, plan_id, job_id, sequence_number, event_type,
                    created_at, payload_json, previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, plan_id, job_id, sequence, event_type, timestamp,
                    payload_json, previous_hash, event_hash,
                ),
            )
        return {
            "event_id": event_id,
            "sequence_number": sequence,
            "previous_event_hash": previous_hash,
            "event_hash": event_hash,
        }

    def add_artifact(self, values: dict[str, Any]) -> None:
        columns = (
            "artifact_id", "plan_id", "job_id", "artifact_type",
            "content_sha256", "storage_path", "size_bytes", "created_at",
        )
        with self._connect() as connection:
            connection.execute(
                f"INSERT OR IGNORE INTO metadata_repair_artifacts ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                tuple(values[column] for column in columns),
            )

    def expired_pending_plans(self, timestamp: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM metadata_repair_plans "
                "WHERE status='pending' AND expires_at<=?",
                (timestamp,),
            ).fetchall()
        return [self._decode_plan(row) for row in rows]

    def mark_plan_expired(self, plan_id: str, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE metadata_repair_plans SET status='expired', updated_at=? "
                "WHERE plan_id=? AND status='pending'",
                (timestamp, plan_id),
            )

    def plans_with_expired_inputs(self, timestamp: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM metadata_repair_plans
                WHERE input_expires_at IS NOT NULL AND input_expires_at<=?
                  AND input_purged_at IS NULL AND job_id IS NULL
                """,
                (timestamp,),
            ).fetchall()
        return [self._decode_plan(row) for row in rows]

    def mark_plan_input_purged(self, plan_id: str, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE metadata_repair_plans SET input_purged_at=?, updated_at=? "
                "WHERE plan_id=? AND input_purged_at IS NULL",
                (timestamp, timestamp, plan_id),
            )

    def jobs_with_expired_files(self, timestamp: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM metadata_repair_jobs
                WHERE files_expires_at IS NOT NULL AND files_expires_at<=?
                  AND stage!='files_purged'
                """,
                (timestamp,),
            ).fetchall()
        return [self._decode_job(row) for row in rows]

    def mark_files_purged(self, job_id: str, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE metadata_repair_jobs SET stage='files_purged', updated_at=? "
                "WHERE job_id=?",
                (timestamp, job_id),
            )
