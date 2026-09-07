"""BMFF 顶层 box 解析器测试（合规方案 §3.2 / §7 / §8.2）。

手工构造顶层 box 字节，覆盖：ftyp/moov/mdat 完整性判定、C2PA 顶层 uuid
识别（netimpaniu）、非 C2PA uuid 不误判、截断/越界/非法尺寸、size==0 与
size==1(largesize) 两种扩展尺寸、只读不整读（探测请求的字节即可判结构）。
"""
from __future__ import annotations

import struct

from app.core.bmff import C2PA_MARKER, scan_top_level
from tests.common import CLEAN_MP4


def _box(btype: bytes, payload: bytes = b"", *, largesize: int | None = None,
         to_eof: bool = False) -> bytes:
    """构造一个顶层 box：标准头 / size==0 延伸至文件尾 / size==1 largesize。"""
    if to_eof:
        return struct.pack(">I4s", 0, btype) + payload
    if largesize is not None:
        # [size==1][type][8 字节 largesize][payload]，头 16 字节
        return struct.pack(">I4sQ", 1, btype, largesize) + payload
    return struct.pack(">I4s", len(payload) + 8, btype) + payload


def _scan(boxes: list[bytes]) -> bytes:
    return b"".join(boxes)


def _write(tmp_path, data: bytes, name: str = "clip.mp4"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_valid_structure_full_boxes(tmp_path):
    """ftyp/free/mdat/moov 齐全且可遍历 → parse_ok，三项全真，无 C2PA。"""
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00\x00\x00\x00"),
        _box(b"free", b"\x00" * 8),
        _box(b"mdat", b"\x00" * 64),
        _box(b"moov", b"\x00" * 64),
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert probe.parse_ok and not probe.truncated and probe.error is None
    assert probe.has_ftyp and probe.has_moov and probe.has_mdat
    assert probe.c2pa_uuid == []
    d = probe.to_dict()
    assert d["has_ftyp"] is True and d["c2pa_uuid"] == []


def test_c2pa_uuid_manifest_detected(tmp_path):
    """usertype 内含 netimpaniu 的顶层 uuid box → 记入 c2pa_uuid。"""
    usertype = b"ABCDEF" + C2PA_MARKER              # 6+10=16 字节，含清单标记
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00\x00\x00\x00"),
        _box(b"uuid", usertype + b"\x00" * 8),      # usertype 后跟内容
        _box(b"mdat", b"\x00" * 32),
        _box(b"moov", b"\x00" * 32),
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert probe.parse_ok
    assert len(probe.c2pa_uuid) == 1
    assert probe.c2pa_uuid[0] == usertype.hex()


def test_uuid_non_c2pa_not_counted(tmp_path):
    """普通 uuid（usertype 不含标记）被遍历但不当作 C2PA。"""
    usertype = b"\x00" * 16
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00" * 4),
        _box(b"uuid", usertype + b"\x00" * 8),
        _box(b"moov", b"\x00" * 16),
        _box(b"mdat", b"\x00" * 16),
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert probe.parse_ok
    assert probe.c2pa_uuid == []
    assert any(b["type"] == "uuid" for b in probe.top_boxes)


def test_size_zero_box_runs_to_eof(tmp_path):
    """size==0 的最后一个 box（mdat）延伸到文件尾，正常解析。"""
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00" * 4),
        _box(b"moov", b"\x00" * 16),
        _box(b"mdat", b"\x00" * 24, to_eof=True),
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert probe.parse_ok
    assert probe.has_mdat and probe.has_moov


def test_largesize_box(tmp_path):
    """size==1 + 8 字节 largesize（>4GB 文件的标准写法）→ 正确推进到文件尾。"""
    payload = b"\x00" * 48
    largesize = 16 + len(payload)                    # 头 16 + 内容
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00" * 4),
        _box(b"moov", b"\x00" * 16),
        _box(b"mdat", payload, largesize=largesize),
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert probe.parse_ok
    assert probe.has_mdat
    assert probe.top_boxes[-1]["size"] == largesize


def test_truncated_box_flags_not_trusted(tmp_path):
    """box 头声明尺寸超出文件尾 → truncated，不把结构当可信。"""
    # mdat 头声明 10008 字节，实际只剩 16 字节内容 → 越界截断
    truncated_mdat = struct.pack(">I4s", 10_000 + 8, b"mdat") + b"\x00" * 16
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00" * 4),
        truncated_mdat,
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert probe.truncated and not probe.parse_ok
    assert not probe.has_mdat                       # 越界 box 不算存在


def test_illegal_box_size_flags_error(tmp_path):
    """box 尺寸小于头长（非法）→ error，不静默继续。"""
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00" * 4),
        struct.pack(">I4s", 3, b"moov"),
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert not probe.parse_ok
    assert probe.error is not None
    assert "非法 box 尺寸" in probe.error


def test_corrupt_uuid_short_payload_flags_error(tmp_path):
    """uuid box 内容不足 16 字节 usertype → error（结构不可信）。"""
    data = _scan([
        _box(b"ftyp", b"isom" + b"\x00" * 4),
        struct.pack(">I4s", 12, b"uuid") + b"\x00" * 4,
    ])
    probe = scan_top_level(_write(tmp_path, data))
    assert not probe.parse_ok
    assert probe.error is not None
    assert "uuid box" in probe.error


def test_real_clean_mp4_fixture(tmp_path):
    """真实 fixture：只读探测顶层结构，不修改源文件。"""
    probe = scan_top_level(CLEAN_MP4)
    assert probe.parse_ok
    assert probe.has_ftyp and probe.has_moov and probe.has_mdat
    assert probe.c2pa_uuid == []
