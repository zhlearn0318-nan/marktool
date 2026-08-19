from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.metadata_routes import router as metadata_router
from app.api.routes import router

app = FastAPI(title="AIGC 标识合规平台 (MVP)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(metadata_router)
