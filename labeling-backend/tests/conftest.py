"""共享测试夹具。"""
from __future__ import annotations

import pytest

from common import EXIFTOOL_CONFIG, VALID_AIGC


@pytest.fixture
def exiftool_config():
    return str(EXIFTOOL_CONFIG)


@pytest.fixture
def valid_aigc():
    return dict(VALID_AIGC)
