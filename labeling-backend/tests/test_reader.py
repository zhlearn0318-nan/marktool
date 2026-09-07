"""共享读取器回归测试。

覆盖：exiftool 结构回显字段（SourceFile / File:Directory，值为文件路径）不得
被当作 AIGC 候选——仓库/上传路径含 "AIGC" 字样时曾把干净文件误判为已有标识。
reader 与写入器检测（detect_existing）、合规检测器共用（手册 §14），此修复
须保证两边都无回归。
"""
from __future__ import annotations

import shutil

from app.core.reader import read_aigc_records
from tests.common import CLEAN_MP4, EXIFTOOL_CONFIG


def test_no_false_positive_from_path_echo(tmp_path):
    """文件所在路径含 "AIGC" 时，结构回显字段不得算作标识。"""
    nest = tmp_path / "AIGC项目" / "video"
    nest.mkdir(parents=True)
    p = nest / "clean.mp4"
    shutil.copyfile(CLEAN_MP4, p)

    records = read_aigc_records(str(p), exiftool="exiftool",
                                config=str(EXIFTOOL_CONFIG))
    assert records == []
    assert all(not r.tag_key.startswith(("SourceFile", "File:", "ExifTool:"))
               for r in records)


def test_real_xmp_aigc_still_detected(tmp_path):
    """真实写入的 XMP-aigc:AIGC 标识仍然被扫到（排除回显不误伤真标识）。"""
    nest = tmp_path / "AIGC_path_here"
    nest.mkdir(parents=True)
    p = nest / "labeled.mp4"
    shutil.copyfile(CLEAN_MP4, p)

    from app.adapters import Mp4Adapter
    from app.core import aigc
    from tests.common import VALID_AIGC

    Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))._exiftool_cmd(
        ["-overwrite_original", "-XMP-aigc:AIGC=" + aigc.serialize_aigc(VALID_AIGC)],
        str(p))

    records = read_aigc_records(str(p), exiftool="exiftool",
                                config=str(EXIFTOOL_CONFIG))
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC
