from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.request_limits import MetadataRequestSizeLimitMiddleware


def _limited_app(max_bytes: int) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetadataRequestSizeLimitMiddleware, max_bytes=max_bytes)

    @app.post("/api/v1/metadata-label-jobs")
    async def consume(request: Request):
        return {"size": len(await request.body())}

    return app


def test_chunked_body_without_content_length_is_counted():
    client = TestClient(_limited_app(10))
    response = client.post(
        "/api/v1/metadata-label-jobs",
        content=iter([b"123456", b"78901"]),
        headers={"transfer-encoding": "chunked"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_body_at_limit_is_accepted():
    client = TestClient(_limited_app(10))
    response = client.post(
        "/api/v1/metadata-label-jobs",
        content=b"1234567890",
    )
    assert response.status_code == 200
    assert response.json() == {"size": 10}
