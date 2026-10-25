"""共享测试夹具。"""
from __future__ import annotations

import os

import pytest

from common import EXIFTOOL_CONFIG, VALID_AIGC


@pytest.fixture
def exiftool_config():
    return str(EXIFTOOL_CONFIG)


@pytest.fixture
def valid_aigc():
    return dict(VALID_AIGC)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """``AIGC_REQUIRE_TOOLS=1`` 时，把"跳过"直接判为失败。

    平时没装 ExifTool / ffmpeg 的机器靠 skip 跑其余测试，是合理的；但那样跑出来
    的"全绿"里可能一条真活都没干——**在另一套系统上做验收时，这种假绿灯比红灯更
    危险**。所以上机验证（见桌面文档 windows-portability-checklist.md）必须带上这个
    开关：设了就不许有任何跳过，缺工具当场红。
    """
    report = yield
    if report.skipped and os.environ.get("AIGC_REQUIRE_TOOLS"):
        report.outcome = "failed"
        report.longrepr = (
            "AIGC_REQUIRE_TOOLS=1：本环境的验收不允许跳过任何测试，"
            "但这条被跳过了（通常是缺 ExifTool / ffmpeg / ffprobe）。"
        )
    return report
