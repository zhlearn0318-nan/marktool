"""进程内后台任务执行器（开发手册 §17.6）。

首期用线程池 + 状态持久化，足够跑通闭环；多进程/外部队列在扩展期替换。
任务执行超时由 limits.job_timeout_seconds 兜底。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import jobs, util
from .pipeline import run_job
from .storage import FileStorage
from .store import JobStore


class JobWorker:
    def __init__(self, store: JobStore, storage: FileStorage, settings: Any):
        self._store = store
        self._storage = storage
        self._settings = settings
        self._executor: ThreadPoolExecutor | None = None
        self._timeout = settings.limits.job_timeout_seconds

    def start(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._settings.limits.max_concurrent_jobs,
                thread_name_prefix="marktool")

    def stop(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    def submit(self, job_id: str) -> None:
        if self._executor is None:
            raise RuntimeError("worker 未启动")
        self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        timer = threading.Timer(self._timeout, self._mark_timeout, args=(job_id,))
        try:
            timer.start()
            run_job(job_id, self._store, self._storage, self._settings)
        except Exception:
            pass  # run_job 内部已做失败归置
        finally:
            timer.cancel()

    def _mark_timeout(self, job_id: str) -> None:
        job = self._store.get(job_id)
        if job and job["status"] not in jobs.TERMINAL:
            self._store.mark_terminal(
                job_id, status=jobs.FAILED, stage=jobs.STAGE_FAILED,
                updated_at=util.now_iso(), error_code=jobs.JOB_TIMEOUT,
                error_message="任务处理超时", retryable=True)
