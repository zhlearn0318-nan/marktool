from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.metadata_routes import (
    router as metadata_router,
    shutdown_metadata_job_service,
)
from app.api.repair_routes import (
    router as repair_router,
    shutdown_metadata_repair_service,
)
from app.api.routes import router
from app.request_limits import MetadataRequestSizeLimitMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    shutdown_metadata_job_service()
    shutdown_metadata_repair_service()


app = FastAPI(title="AIGC 标识合规平台 (MVP)", lifespan=lifespan)
app.add_middleware(MetadataRequestSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(metadata_router)
app.include_router(repair_router)
