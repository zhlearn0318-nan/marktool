"""GB 45438-2025 元数据标注服务入口。

创建任务（multipart）→ 后台执行（检查/写入/回读/媒体校验）→ 轮询状态 → 下载。
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import health, inspect, jobs
from .api import repair_routes
from .api.repair_routes import (
    get_metadata_repair_service,
    shutdown_metadata_repair_service,
)
from .api.schema import error_response
from .config import Settings, load_settings
from .core import util
from .core.image_bridge import build_repair_service
from .core.errors import INVALID_MULTIPART, INTERNAL_ERROR, ApiError
from .core.storage import FileStorage
from .core.store import JobStore
from .core.worker import JobWorker
from .request_limits import MetadataRequestSizeLimitMiddleware

# 以 multipart 接收上传的端点：这些端点的表单结构错误按 §11.2 报 400。
_MULTIPART_PATHS = frozenset({
    "/api/v1/metadata-label-jobs",
    "/api/v1/compliance-inspect",
    "/api/v1/metadata-repair-plans",
})


def create_app(settings: Settings | None = None,
               storage_root: str | None = None,
               db_path: str | None = None) -> FastAPI:
    settings = settings or load_settings()
    if storage_root:
        # 覆盖要落到 settings 上，而不是只改本函数里的局部变量：
        # 编号登记库路径是经 settings.storage.root 推导的，只改局部会让
        # 任务文件在临时目录、登记库却写进仓库 ./storage —— 测试之间互相污染。
        settings = replace(
            settings, storage=replace(settings.storage, root=storage_root)
        )
    root = settings.storage.root
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
        # 修复工作台持有线程池与 SQLite 连接，需显式收尾。
        # 先关本应用自建的那个，再关模块级单例（若曾被直接调用过）。
        app.state.repair_service.close()
        shutdown_metadata_repair_service()

    app = FastAPI(title="GB 45438-2025 元数据标注服务", version="0.1.0",
                  lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.storage = storage
    app.state.worker = worker

    # 修复工作台的服务按本应用配置构造，而不是让它用自己的默认路径 ——
    # 否则它的编号登记库会落到 <repo>/data/ 下，与打标流水线和 /compliance-inspect
    # 用的 <storage.root>/ 不是一个库，跨流程的编号复用检查会静默失效。
    app.state.repair_service = build_repair_service(settings)
    app.dependency_overrides[get_metadata_repair_service] = (
        lambda: app.state.repair_service
    )

    # §13 请求体上限：约束 Content-Length 与分块传输的实际字节数。
    # 在 request_id 中间件之前注册，使其位于内层——413 响应仍会带上 X-Request-Id。
    app.add_middleware(MetadataRequestSizeLimitMiddleware)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # multipart 端点：缺少/非法的表单字段属于"请求结构错误"
        # （§11.2 INVALID_MULTIPART → 400）。
        # 其余端点（修复工作台的 JSON 请求体）保留 FastAPI 默认语义 422，
        # 否则会把"字段值不合法"误报成"请求结构错误"。
        if request.url.path not in _MULTIPART_PATHS:
            return await request_validation_exception_handler(request, exc)
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
    # 修复工作台（计划 → 显式确认 → 异步执行 → 审计）。路径独立，与标注/检测不冲突。
    app.include_router(repair_routes.router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=app.state.settings.host, port=app.state.settings.port)
