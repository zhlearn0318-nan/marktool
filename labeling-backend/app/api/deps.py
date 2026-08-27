"""FastAPI 依赖：访问应用级单例（settings/store/storage/worker）。"""
from __future__ import annotations

from fastapi import Request

from ..config import Settings
from ..core.storage import FileStorage
from ..core.store import JobStore
from ..core.worker import JobWorker


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_storage(request: Request) -> FileStorage:
    return request.app.state.storage


def get_worker(request: Request) -> JobWorker:
    return request.app.state.worker
