"""修复台存储的「内容指纹」列迁移。

修复台原本只服务图片，把内容指纹列直接叫 ``pixel_sha256 NOT NULL``。接入视频
与文本后，视频没有像素、文本更没有，列名必须回到模态中立，并多出一列说明
这枚指纹是什么（``fingerprint_kind``：pixel / stream / body / content）。

迁移用 ``ALTER TABLE ... RENAME COLUMN`` 而不是重建表：三种模态都有指纹，
``NOT NULL`` 同样成立；旧值本就全是像素哈希，无需搬运。本文件锁住这条路径——
线上库是旧形态，改错了就是"修复台打不开"。
"""
from __future__ import annotations

import sqlite3

from app.metadata.repair_store import SQLiteMetadataRepairStore

_LEGACY_PLANS_DDL = """
CREATE TABLE metadata_repair_plans (
    plan_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    status TEXT NOT NULL,
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
)
"""


def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _seed_legacy_database(path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        connection.execute(_LEGACY_PLANS_DDL)
        connection.execute(
            "INSERT INTO metadata_repair_plans ("
            "plan_id, request_id, status, created_at, updated_at, expires_at,"
            "original_file_name, detected_mime_type, input_size_bytes, input_sha256,"
            "pixel_sha256, input_path, inspection_json, draft_json, plan_hash"
            ") VALUES ('plan_1','req_1','pending','t','t','t','a.png','image/png',"
            "10,'abc','deadbeef','/tmp/a.png','{}','{}','hash_1')")
        connection.commit()
    finally:
        connection.close()


def test_legacy_column_is_renamed_and_backfilled(tmp_path):
    database = tmp_path / "repairs.sqlite3"
    _seed_legacy_database(database)

    SQLiteMetadataRepairStore(str(database))       # 构造即迁移

    connection = sqlite3.connect(str(database))
    try:
        columns = _column_names(connection, "metadata_repair_plans")
        assert "content_fingerprint" in columns
        assert "pixel_sha256" not in columns
        assert "fingerprint_kind" in columns

        row = connection.execute(
            "SELECT content_fingerprint, fingerprint_kind "
            "FROM metadata_repair_plans WHERE plan_id = 'plan_1'").fetchone()
    finally:
        connection.close()

    # 旧值是像素哈希，原样保留；旧行全部来自图片通路，kind 回填成 pixel
    assert row == ("deadbeef", "pixel")


def test_migration_is_idempotent(tmp_path):
    database = tmp_path / "repairs.sqlite3"
    _seed_legacy_database(database)

    SQLiteMetadataRepairStore(str(database))
    SQLiteMetadataRepairStore(str(database))       # 再开一次不得报错或改坏数据

    connection = sqlite3.connect(str(database))
    try:
        row = connection.execute(
            "SELECT content_fingerprint, fingerprint_kind "
            "FROM metadata_repair_plans WHERE plan_id = 'plan_1'").fetchone()
    finally:
        connection.close()
    assert row == ("deadbeef", "pixel")


def test_fresh_database_has_the_new_shape(tmp_path):
    database = tmp_path / "repairs.sqlite3"
    SQLiteMetadataRepairStore(str(database))

    connection = sqlite3.connect(str(database))
    try:
        columns = _column_names(connection, "metadata_repair_plans")
    finally:
        connection.close()

    assert "content_fingerprint" in columns
    assert "fingerprint_kind" in columns
    assert "pixel_sha256" not in columns
