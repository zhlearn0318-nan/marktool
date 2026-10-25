"""编号登记库覆盖三种模态（开发手册 §6.1 b）。

背景：登记原先只发生在**图片写入服务内部**；视频/文档走适配器路径时根本不
登记，于是修复工作台的"来源核对"关卡对它们永远查不到数据，检测报告的
``registry`` 块也恒为 skipped。本文件锁住接线结果。

接线成立的前提是**内容指纹必须在写入标识前后保持不变**——否则登记库核对会把
每一次正常写入都误判成"同号异内容"，把正常业务拦成失败。所以本文件的重点
是"写入前后指纹相等"，它比"登记表里有行"更能说明接线是活的。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import get_adapter
from app.config import Settings
from app.main import create_app
from app.metadata.identifier_registry import SQLiteIdentifierRegistry
from tests.common import (CLEAN_MP4, EXIFTOOL_CONFIG, VALID_AIGC, make_markdown,
                          make_pdf)


# ---- 夹具 -----------------------------------------------------------------

def _adapter(mime: str, exiftool_config):
    return get_adapter(mime, exiftool="exiftool", ffprobe="ffprobe",
                       ffmpeg="ffmpeg", exiftool_config=exiftool_config)


@pytest.fixture
def client(tmp_path):
    """不需要 ExifTool 的客户端：Markdown 通路完全不碰外部工具。"""
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = 10 * 1024 * 1024
    app = create_app(settings=settings,
                     storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    with TestClient(app) as c:
        yield c


def _req(policy="reject", obj=None):
    return json.dumps({"standard": "GB45438-2025", "modality": "text",
                       "existing_metadata_policy": policy,
                       "AIGC": obj or VALID_AIGC})


def _upload(client, data: bytes, fname: str, policy="reject", obj=None):
    return client.post("/api/v1/metadata-label-jobs",
                       files={"file": (fname, data, "application/octet-stream")},
                       data={"request": _req(policy, obj)})


def _wait_terminal(client, job_id, timeout=30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/v1/metadata-label-jobs/{job_id}").json()
        if d["status"] in ("succeeded", "failed"):
            return d
        time.sleep(0.2)
    raise AssertionError("任务超时未结束")


def _registry_of(tmp_path) -> SQLiteIdentifierRegistry:
    return SQLiteIdentifierRegistry(str(tmp_path / "storage" / "aigc_identifiers.sqlite3"))


# ---- 指纹：写入标识前后必须相等（接线成立的前提）---------------------------

def test_markdown_fingerprint_survives_labeling(tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    adapter = _adapter("text/markdown", str(EXIFTOOL_CONFIG))
    before = adapter.content_fingerprint(src)

    dst = tmp_path / "out.md"
    adapter.write_metadata(src, dst, VALID_AIGC)

    after = adapter.content_fingerprint(dst)
    assert before == after, "正文指纹被写入标识改变了，登记库会误报同号异内容"
    assert before[1] == "body"
    assert len(before[0]) == 64 and int(before[0], 16) >= 0


def test_registry_binds_to_the_published_file_not_the_input(tmp_path, client):
    """登记绑定的必须是**发布出去的成品**，否则 replace 之后核对会误报冲突。

    这条不是理论洁癖：replace 策略会删掉正文里的旧载体注释，剩下的空行让正文
    哈希与原文件不同。若按原文件登记，日后拿成品来核对就是"同号异内容"，
    正常文件会被拦在修复之外。
    """
    body = "# 标题\n\n正文。\n"
    src = tmp_path / "dual.md"
    src.write_text(f"---\nAIGC: '{{\"AIGC\":{{}}}}'\n---\n"
                   f"{body}\n<!-- AIGC: {{\"AIGC\":{{}}}} -->\n", encoding="utf-8")

    job = _wait_terminal(client,
                         _upload(client, src.read_bytes(), "dual.md",
                                 policy="replace").json()["job_id"])
    assert job["status"] == "succeeded", job

    output = client.get(
        f"/api/v1/metadata-label-jobs/{job['job_id']}/output").content
    published = tmp_path / "published.md"
    published.write_bytes(output)

    adapter = _adapter("text/markdown", str(EXIFTOOL_CONFIG))
    registry = _registry_of(tmp_path)
    assert registry.lookup("producer", VALID_AIGC["ContentProducer"],
                           VALID_AIGC["ProduceID"]) \
        == adapter.content_fingerprint(published)[0], \
        "登记值与成品指纹对不上，修复工作台的来源核对会把这份文件判成冲突"


def test_markdown_fingerprint_distinguishes_bodies(tmp_path):
    adapter = _adapter("text/markdown", str(EXIFTOOL_CONFIG))
    a = make_markdown(tmp_path / "a.md", "# 甲\n\n内容一。\n")
    b = make_markdown(tmp_path / "b.md", "# 乙\n\n内容二。\n")
    assert adapter.content_fingerprint(a) != adapter.content_fingerprint(b)


def test_pdf_fingerprint_survives_labeling(exiftool_config, tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=3)
    adapter = _adapter("application/pdf", exiftool_config)
    before = adapter.content_fingerprint(src)

    dst = tmp_path / "out.pdf"
    adapter.write_metadata(src, dst, VALID_AIGC)

    after = adapter.content_fingerprint(dst)
    assert before == after
    assert before[1] == "content"


def test_pdf_fingerprint_distinguishes_same_page_count_content(exiftool_config, tmp_path):
    """页数相同的两份不同文档必须区分得开。

    这条是原来的漏洞：指纹只有"页数 + %%EOF"，两份三页的文档算出来一模一样，
    登记库核对"是不是同一份内容"时等于没查。改一个字（长度不变，xref 仍正确）
    就必须换指纹——**区分内容**才是指纹存在的意义。
    """
    adapter = _adapter("application/pdf", exiftool_config)
    original = make_pdf(tmp_path / "a.pdf", pages=3)
    altered = tmp_path / "b.pdf"
    altered.write_bytes(original.read_bytes().replace(b"(Page 1)", b"(Paqe 1)"))

    assert adapter.content_fingerprint(original) != adapter.content_fingerprint(altered)


def test_mp4_fingerprint_distinguishes_different_media(exiftool_config, tmp_path):
    """同参数、不同画面的两段视频必须区分得开。

    这条用 ffprobe 摘要**反证**：两段片子的摘要完全一样（容器格式、时长、轨道、
    编解码、分辨率都相同），早先的指纹就是拿摘要算的，所以那时候这两段片子
    指纹相同——`content_fingerprint` 退化成常量，登记库核对毫无意义。
    现在指纹取 mdat 载荷，能分开。
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("需要 ffmpeg 生成两段同参数视频")
    clips = {}
    for name, source in (("a", "testsrc"), ("b", "testsrc2")):
        path = tmp_path / f"{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"{source}=size=320x240:rate=25:duration=2",
             "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)],
            check=True, capture_output=True)
        clips[name] = path
    adapter = _adapter("video/mp4", exiftool_config)
    signatures = {n: adapter._signature(adapter._ffprobe_json(p))
                  for n, p in clips.items()}
    assert signatures["a"] == signatures["b"], "夹具没造出同参数视频，这条测试就失去意义"

    assert adapter.content_fingerprint(clips["a"]) != adapter.content_fingerprint(
        clips["b"])


def test_mp4_fingerprint_survives_labeling(exiftool_config, tmp_path):
    adapter = _adapter("video/mp4", exiftool_config)
    before = adapter.content_fingerprint(CLEAN_MP4)

    dst = tmp_path / "out.mp4"
    adapter.write_metadata(CLEAN_MP4, dst, VALID_AIGC)

    after = adapter.content_fingerprint(dst)
    assert before == after, "容器被重写后流签名变了，登记库会误报同号异内容"
    assert before[1] == "stream"


# ---- 打标登记：三种模态的指纹种类互不相同 ---------------------------------

def test_fingerprint_kind_is_declared_per_modality(exiftool_config, tmp_path):
    kinds = {
        "text/markdown": _adapter("text/markdown", exiftool_config),
        "application/pdf": _adapter("application/pdf", exiftool_config),
        "video/mp4": _adapter("video/mp4", exiftool_config),
    }
    paths = {
        "text/markdown": make_markdown(tmp_path / "a.md"),
        "application/pdf": make_pdf(tmp_path / "a.pdf"),
        "video/mp4": CLEAN_MP4,
    }
    actual = {m: a.content_fingerprint(paths[m])[1] for m, a in kinds.items()}
    assert actual == {"text/markdown": "body",
                      "application/pdf": "content",
                      "video/mp4": "stream"}


# ---- 经 API 的登记与冲突 ---------------------------------------------------

def test_labeling_registers_both_roles(client, tmp_path):
    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    job = _wait_terminal(client, _upload(client, src.read_bytes(), "a.md").json()["job_id"])
    assert job["status"] == "succeeded", job

    registry = _registry_of(tmp_path)
    producer = registry.lookup("producer", VALID_AIGC["ContentProducer"],
                               VALID_AIGC["ProduceID"])
    propagator = registry.lookup("propagator", VALID_AIGC["ContentPropagator"],
                                 VALID_AIGC["PropagateID"])
    assert producer is not None, "打标后编号未登记，修复工作台的来源核对将无据可查"
    assert propagator == producer, "首写时传播者与生产者应指向同一份内容指纹"

    adapter = _adapter("text/markdown", str(EXIFTOOL_CONFIG))
    assert producer == adapter.content_fingerprint(src)[0]


def test_same_identifier_for_same_content_is_allowed(client, tmp_path):
    """同号 + 同内容 → 放行。证明绑定的是内容指纹，不是文件名。"""
    body = "# 标题\n\n正文。\n"
    for name in ("a.md", "b.md"):
        src = make_markdown(tmp_path / name, body)
        job = _wait_terminal(client,
                             _upload(client, src.read_bytes(), name).json()["job_id"])
        assert job["status"] == "succeeded", job


def test_same_identifier_for_different_content_is_rejected(client, tmp_path):
    """同号 + 异内容 → 拦下，错误码与图片侧同一词表。"""
    first = make_markdown(tmp_path / "a.md", "# 甲\n\n内容一。\n")
    job1 = _wait_terminal(client,
                          _upload(client, first.read_bytes(), "a.md").json()["job_id"])
    assert job1["status"] == "succeeded", job1

    second = make_markdown(tmp_path / "b.md", "# 乙\n\n内容二。\n")
    job2 = _wait_terminal(client,
                          _upload(client, second.read_bytes(), "b.md").json()["job_id"])
    assert job2["status"] == "failed", job2
    assert job2["error"]["code"] == "AIGC_IDENTIFIER_DUPLICATE"
    assert "ProduceID" in job2["error"]["message"]

    # 被拦下的任务不得留下产出，也不得把第二条登记写进库里
    assert job2.get("output") is None
    registry = _registry_of(tmp_path)
    assert registry.lookup("producer", VALID_AIGC["ContentProducer"],
                           VALID_AIGC["ProduceID"]) is not None
    rows = registry._connect().execute(
        "SELECT COUNT(*) FROM aigc_identifiers").fetchone()[0]
    assert rows == 2, "producer + propagator 两条，冲突任务不得追加记录"
