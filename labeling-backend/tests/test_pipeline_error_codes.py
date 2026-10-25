"""流水线失败码必须真的存在（开发手册 §11.2）。

起因是一个已存在但一直没被触发的缺陷：``pipeline.py`` 里十处
``raise PipelineError(jobs.XXX, ...)`` 引用的常量其实定义在 ``app.core.errors``，
``app.core.jobs`` 上**没有**这些名字。这些分支一旦真的执行，抛出的会是
``AttributeError``，被 ``run_job`` 的兜底 ``except Exception`` 接住，于是
"媒体完整性失败"变成"内部错误: module 'app.core.jobs' has no attribute …"——
用户看到的是服务故障，而不是文件问题的具体原因。

静态那条测试防的是**整类**问题（以后再加分支也不会重犯），行为那条证明
失败路径现在真的会把正确错误码送到用户面前。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.base import MediaReport
from app.config import Settings
from app.core import jobs
from app.main import create_app
from tests.common import VALID_AIGC, make_markdown

PIPELINE_SOURCE = Path(__file__).resolve().parents[1] / "app" / "core" / "pipeline.py"


def test_every_jobs_constant_referenced_by_pipeline_exists():
    """静态扫描：``jobs.X`` 里的每个名字都必须真的挂在 jobs 模块上。"""
    import re

    source = PIPELINE_SOURCE.read_text(encoding="utf-8")
    referenced = set(re.findall(r"\bjobs\.([A-Z_]+)\b", source))
    missing = sorted(n for n in referenced if not hasattr(jobs, n))
    assert not missing, (
        f"pipeline.py 引用了 jobs 模块上不存在的常量 {missing}；"
        "错误码应改用 app.core.errors 里的名字")


def _client(tmp_path) -> TestClient:
    settings = Settings()
    settings.storage.root = str(tmp_path / "storage")
    settings.storage.max_file_bytes = 10 * 1024 * 1024
    app = create_app(settings=settings,
                     storage_root=str(tmp_path / "storage"),
                     db_path=str(tmp_path / "jobs.db"))
    return TestClient(app)


def test_media_integrity_failure_reports_its_own_code(tmp_path, monkeypatch):
    """媒体完整性失败要报 MEDIA_INTEGRITY_FAILED，不能退化成 INTERNAL_ERROR。"""
    from app.adapters.markdown import MarkdownAdapter

    def always_fail(self, src, dst, duration_tolerance=0.1):
        return MediaReport(passed=False, reason="注入的完整性失败")

    monkeypatch.setattr(MarkdownAdapter, "media_integrity_check", always_fail)

    src = make_markdown(tmp_path / "a.md", "# 标题\n\n正文。\n")
    request = json.dumps({"standard": "GB45438-2025", "modality": "text",
                          "existing_metadata_policy": "reject", "AIGC": VALID_AIGC})

    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/metadata-label-jobs",
            files={"file": ("a.md", src.read_bytes(), "application/octet-stream")},
            data={"request": request})
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]

        deadline = time.time() + 30
        while time.time() < deadline:
            job = client.get(f"/api/v1/metadata-label-jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.2)

    assert job["status"] == "failed", job
    assert job["error"]["code"] == "MEDIA_INTEGRITY_FAILED", job["error"]
    assert "注入的完整性失败" in job["error"]["message"]
