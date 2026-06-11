import os
import tempfile
import uuid

_RESULTS: dict[str, dict] = {}
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "aigc_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_upload(filename: str, data: bytes) -> tuple[str, str]:
    result_id = uuid.uuid4().hex
    ext = os.path.splitext(filename or "")[1] or ".bin"
    path = os.path.join(UPLOAD_DIR, result_id + ext)
    with open(path, "wb") as f:
        f.write(data)
    return result_id, path


def store_result(result_id: str, payload: dict) -> None:
    _RESULTS[result_id] = payload


def get_result(result_id: str) -> dict | None:
    return _RESULTS.get(result_id)
