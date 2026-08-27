"""通用小工具。"""
from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """UTC ISO 8601 字符串（§7.1），例如 2026-08-16T08:30:00Z。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_hours_iso(iso: str, hours: int) -> str:
    from datetime import timedelta
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
