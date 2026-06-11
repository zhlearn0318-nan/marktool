import io
from fastapi.testclient import TestClient
from PIL import Image
from app.main import app
from tests.fixtures import make_png, VALID_AIGC

client = TestClient(app)


def _png_bytes(tmp_path, aigc=None):
    p = make_png(tmp_path / "x.png", aigc_dict=aigc)
    return open(p, "rb").read()


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_detect_compliant_image(tmp_path):
    data = _png_bytes(tmp_path, aigc=VALID_AIGC)
    r = client.post("/api/detect", files={"file": ("x.png", data, "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["compliance"]["rating"] in ("A", "B")
    assert body["report"]["provenance"][0]["role"] == "ContentProducer"


def test_detect_unmarked_image(tmp_path):
    data = _png_bytes(tmp_path, aigc=None)
    r = client.post("/api/detect", files={"file": ("x.png", data, "image/png")})
    body = r.json()
    assert body["report"]["compliance"]["rating"] == "不合规"
    assert len(body["report"]["compliance"]["suggestions"]) >= 1


def test_detect_rejects_non_image():
    r = client.post("/api/detect",
                    files={"file": ("a.txt", b"not an image", "text/plain")})
    assert r.status_code == 400


def test_get_report_roundtrip(tmp_path):
    data = _png_bytes(tmp_path, aigc=VALID_AIGC)
    rid = client.post("/api/detect",
                      files={"file": ("x.png", data, "image/png")}).json()["result_id"]
    r = client.get(f"/api/report/{rid}")
    assert r.status_code == 200
    assert r.json()["result_id"] == rid


def test_get_report_404():
    assert client.get("/api/report/nope").status_code == 404
