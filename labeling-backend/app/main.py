"""GB 45438-2025 元数据标注服务入口。

创建任务（multipart）→ 后台执行（检查/写入/回读/媒体校验）→ 轮询状态 → 下载。
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import health, inspect, jobs
from .api.schema import error_response
from .config import Settings, load_settings
from .core import util
from .core.errors import INVALID_MULTIPART, INTERNAL_ERROR, ApiError
from .core.storage import FileStorage
from .core.store import JobStore
from .core.worker import JobWorker


def create_app(settings: Settings | None = None,
               storage_root: str | None = None,
               db_path: str | None = None) -> FastAPI:
    settings = settings or load_settings()
    root = storage_root or settings.storage.root
    storage = FileStorage(root)
    store = JobStore(db_path or str(Path(root) / "jobs.db"))
    worker = JobWorker(store, storage, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .core.jobs import FAILED, SUCCEEDED
        try:
            # §12.3：启动时清理超过保留期的成功/失败任务（原文件+结果文件+记录）
            storage.cleanup_expired(store, util.now_iso(), (SUCCEEDED, FAILED))
        except Exception:
            pass
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="GB 45438-2025 元数据标注服务", version="0.1.0",
                  lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.storage = storage
    app.state.worker = worker

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # 缺少/非法的 multipart 字段属于"请求结构错误"（§11.2 INVALID_MULTIPART）
        rid = getattr(request.state, "request_id", "")
        return JSONResponse(status_code=400,
                            content=error_response(rid, INVALID_MULTIPART,
                                                   "请求结构错误：multipart 字段缺失或非法。"))

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        rid = getattr(request.state, "request_id", "")
        return JSONResponse(status_code=exc.status,
                            content=error_response(rid, exc.code, exc.message,
                                                   exc.field_errors))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "")
        return JSONResponse(status_code=500,
                            content=error_response(rid, INTERNAL_ERROR,
                                                   "服务器内部错误。"))

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(inspect.router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=app.state.settings.host, port=app.state.settings.port)
