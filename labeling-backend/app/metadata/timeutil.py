"""时间工具。

原本这两个函数定义在队友的 ``job_service.py`` 里，而 ``repair_service.py``
只用到它们两个。合并时我们没有搬 ``job_service``（它与本项目的
``app/core/pipeline.py`` + ``app/core/store.py`` 是同一件事的两套实现，
并存会出现两个任务存储），所以把这两个纯函数抽到这里，
让修复簇不再依赖任务队列层。

行为与原实现逐字一致：UTC 时间、秒级精度、``+00:00`` 写成 ``Z``。
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
