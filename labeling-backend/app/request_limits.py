import json
import os
import uuid


class RequestBodyTooLarge(RuntimeError):
    pass


class MetadataRequestSizeLimitMiddleware:
    """同时约束 Content-Length 与分块传输的实际请求体大小。"""

    def __init__(self, app, max_bytes: int | None = None):
        self.app = app
        self.max_bytes = max_bytes or int(
            os.getenv("AIGC_MAX_REQUEST_BYTES", str(27 * 1024 * 1024))
        )

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("path") not in {
                "/api/v1/metadata-label-jobs",
                "/api/v1/metadata-repair-plans",
            }
            or scope.get("method") != "POST"
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(send):
        request_id = f"req_{uuid.uuid4().hex}"
        body = json.dumps(
            {
                "request_id": request_id,
                "error": {
                    "code": "REQUEST_TOO_LARGE",
                    "message": "multipart 请求整体超过项目安全上限。",
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"x-request-id", request_id.encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
