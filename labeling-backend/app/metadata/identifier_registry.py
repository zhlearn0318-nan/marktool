import os
import sqlite3
from pathlib import Path
from typing import Protocol


class IdentifierRegistryConfigurationError(RuntimeError):
    pass


class DuplicateIdentifierError(RuntimeError):
    def __init__(self, role: str, provider: str, content_id: str):
        super().__init__(f"{role} 编号已被其他内容使用")
        self.role = role
        self.provider = provider
        self.content_id = content_id


class IdentifierReservation(Protocol):
    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...


class IdentifierRegistry(Protocol):
    def reserve(self, document: dict, content_fingerprint: str) -> IdentifierReservation:
        ...

    def lookup(self, role: str, provider: str, content_id: str) -> str | None:
        ...


class _SQLiteReservation:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection
        self._closed = False

    def commit(self) -> None:
        if self._closed:
            return
        try:
            self._connection.commit()
        finally:
            self._connection.close()
            self._closed = True

    def rollback(self) -> None:
        if self._closed:
            return
        try:
            self._connection.rollback()
        finally:
            self._connection.close()
            self._closed = True


class SQLiteIdentifierRegistry:
    """持久化登记“提供者 + 编号”与内容指纹的对应关系。"""

    def __init__(self, database_path: str):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_environment(cls) -> "SQLiteIdentifierRegistry":
        database_path = os.getenv("AIGC_ID_REGISTRY_PATH")
        if not database_path:
            raise IdentifierRegistryConfigurationError(
                "未配置 AIGC_ID_REGISTRY_PATH，无法检查 ProduceID/PropagateID 唯一性"
            )
        return cls(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS aigc_identifiers (
                    role TEXT NOT NULL CHECK (role IN ('producer', 'propagator')),
                    provider TEXT NOT NULL,
                    content_id TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (role, provider, content_id)
                )
                """
            )

    @staticmethod
    def _entries(document: dict) -> tuple[tuple[str, str, str], ...]:
        aigc = document["AIGC"]
        return (
            ("producer", aigc["ContentProducer"], aigc["ProduceID"]),
            ("propagator", aigc["ContentPropagator"], aigc["PropagateID"]),
        )

    def reserve(self, document: dict, content_fingerprint: str) -> _SQLiteReservation:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for role, provider, content_id in self._entries(document):
                row = connection.execute(
                    """
                    SELECT content_fingerprint
                    FROM aigc_identifiers
                    WHERE role = ? AND provider = ? AND content_id = ?
                    """,
                    (role, provider, content_id),
                ).fetchone()
                if row is not None:
                    if row[0] != content_fingerprint:
                        raise DuplicateIdentifierError(role, provider, content_id)
                    continue
                connection.execute(
                    """
                    INSERT INTO aigc_identifiers
                        (role, provider, content_id, content_fingerprint)
                    VALUES (?, ?, ?, ?)
                    """,
                    (role, provider, content_id, content_fingerprint),
                )
            return _SQLiteReservation(connection)
        except Exception:
            connection.rollback()
            connection.close()
            raise

    def lookup(self, role: str, provider: str, content_id: str) -> str | None:
        """只读查询编号登记；未登记不等同于国标不合规。"""
        if role not in {"producer", "propagator"}:
            raise ValueError("role 只能是 producer 或 propagator")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT content_fingerprint
                FROM aigc_identifiers
                WHERE role = ? AND provider = ? AND content_id = ?
                """,
                (role, provider, content_id),
            ).fetchone()
        return row[0] if row is not None else None
