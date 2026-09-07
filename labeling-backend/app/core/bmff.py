"""纯 Python ISO BMFF 顶层 box 解析器（开发手册 §9.1 / 方案文档 §3.2、§7）。

检测 MP4 顶层 box 结构完整性（ftyp / moov / mdat 存在且可遍历），并识别
C2PA 清单承载的顶层 `uuid` box。本模块只用标准库：按需 seek+read box 头，
不整读文件，适配大视频；box 尺寸不可信（越界/截断）即标记 truncated，
不因损坏 box 抛异常。

moov/udta/trak 内多份 XMP/meta 同名与重复 box，由 ExifTool 全标签扫描
（reader.read_aigc_records，`-json -a -G -s`）负责发现并分别记录，本模块
不重复解析其内部结构。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# C2PA 清单以顶层 uuid box 承载，ExifTool 记为 "uuidnetimpaniu"
# （ftyp 之后、moov 之前）。匹配：uuid box 的 16 字节 usertype 内含该 ASCII 标记。
C2PA_MARKER = b"netimpaniu"

# QuickTime 注释数据原子（旧载体 AIGC 承载位）。ilst 下 4cc = ©cmt。
QT_COMMENT_ATOM = b"\xa9cmt"

_HEADER = struct.Struct(">I4s")       # size + type
_LARGESIZE = struct.Struct(">Q")      # size==1 时的 8 字节扩展尺寸


@dataclass
class BmffProbe:
    """顶层 box 扫描结果。parse_ok=False 表示结构不可信。"""
    has_ftyp: bool = False
    has_moov: bool = False
    has_mdat: bool = False
    top_boxes: list = field(default_factory=list)   # {type, size, usertype_hex?}
    c2pa_uuid: list = field(default_factory=list)   # 命中的 usertype hex
    truncated: bool = False
    error: str | None = None

    @property
    def parse_ok(self) -> bool:
        return not self.truncated and self.error is None

    def to_dict(self) -> dict:
        return {
            "has_ftyp": self.has_ftyp,
            "has_moov": self.has_moov,
            "has_mdat": self.has_mdat,
            "truncated": self.truncated,
            "error": self.error,
            "c2pa_uuid": list(self.c2pa_uuid),
        }


def scan_top_level(path: str | Path, limit_boxes: int = 4096) -> BmffProbe:
    """遍历文件顶层 box 树，返回结构信息。任何越界/截断/损坏都不会抛出。"""
    probe = BmffProbe()
    try:
        with open(path, "rb") as f:
            total = f.seek(0, 2)
            f.seek(0)
            off = 0
            count = 0
            while off < total:
                if count >= limit_boxes:
                    probe.error = "box 数量异常（疑似非 MP4 或损坏）"
                    break
                raw = f.read(8)
                if len(raw) < 8:
                    probe.truncated = True
                    break
                size, btype = _HEADER.unpack(raw)
                hdr = 8
                if size == 1:                      # largesize 扩展
                    large = f.read(8)
                    if len(large) < 8:
                        probe.truncated = True
                        break
                    size = _LARGESIZE.unpack(large)[0]
                    hdr = 16
                elif size == 0:                    # 延伸至文件尾
                    size = total - off
                name = btype.decode("latin1")
                if size < hdr:
                    probe.error = f"非法 box 尺寸 {size}（{name} @ {off}）"
                    break
                if off + size > total:
                    probe.truncated = True
                    break

                entry: dict = {"type": name, "size": size}
                if name == "ftyp":
                    probe.has_ftyp = True
                elif name == "moov":
                    probe.has_moov = True
                elif name == "mdat":
                    probe.has_mdat = True
                elif name == "uuid":
                    # uuid box：内容头 16 字节即 usertype
                    if size - hdr < 16:
                        probe.error = f"uuid box 内容不足 16 字节（@ {off}）"
                        break
                    usertype = f.read(16)
                    if len(usertype) < 16:
                        probe.truncated = True
                        break
                    entry["usertype_hex"] = usertype.hex()
                    if C2PA_MARKER in usertype:
                        probe.c2pa_uuid.append(usertype.hex())

                probe.top_boxes.append(entry)
                off += size
                f.seek(off)
                count += 1
    except OSError as e:
        probe.error = f"文件读取失败: {e}"
    return probe


def scan_wiped_comment_atoms(path: str | Path, zero_scan_cap: int = 2 * 1024 * 1024):
    """探测 QuickTime 旧载体数据原子（moov/udta/meta/ilst/©cmt）是否被清零。

    只读结构：顶层找 moov → moov 内 udta → udta 内 meta（FullBox，内容自 +12 起）
    → meta 内 ilst → ilst 子原子中 4cc=©cmt 的数据盒（data，头部 16 字节，其后为
    载荷）。载荷非空且全部为 0x00 即判"被清零"。只认 ©cmt，不误伤 ©too 等常规
    原子。任何结构异常直接返回 []（绝不抛）。用 seek 顺序读，不整读文件。

    Returns:
        命中列表，每项 {atom, path, offset, payload_size}；未命中为空列表。
    """
    found: list[dict] = []

    def _u32(f, off: int) -> int | None:
        f.seek(off)
        b = f.read(4)
        return struct.unpack(">I", b)[0] if len(b) == 4 else None

    def _child(f, start: int, end: int, target: bytes):
        """在 [start,end) 内找第一个 type==target 的子盒，返回 (off,size) 或 None。"""
        f.seek(start)
        off = start
        while off + 8 <= end:
            f.seek(off)
            h = f.read(8)
            if len(h) < 8:
                return None
            size, btype = struct.unpack(">I4s", h)
            if size == 1:
                big = f.read(8)
                if len(big) < 8:
                    return None
                size = struct.unpack(">Q", big)[0]
            elif size == 0:
                size = end - off
            if size < 8 or off + size > end:
                return None
            if btype == target:
                return (off, size)
            off += size
        return None

    try:
        with open(path, "rb") as f:
            total = f.seek(0, 2)
            moov = _child(f, 0, total, b"moov")
            if not moov:
                return found
            moff, msize = moov
            udta = _child(f, moff + 8, moff + msize, b"udta")
            if not udta:
                return found
            uoff, usize = udta
            meta = _child(f, uoff + 8, uoff + usize, b"meta")
            if not meta:
                return found
            moff2, msize2 = meta
            ilst = _child(f, moff2 + 12, moff2 + msize2, b"ilst")   # meta 是 FullBox
            if not ilst:
                return found
            ioff, isize = ilst
            # 遍历 ilst 子原子，找 ©cmt
            off = ioff + 8
            end = ioff + isize
            while off + 8 <= end:
                f.seek(off)
                h = f.read(8)
                if len(h) < 8:
                    break
                size, btype = struct.unpack(">I4s", h)
                if size == 0:
                    size = end - off
                if size < 8 or off + size > end:
                    break
                if btype == QT_COMMENT_ATOM:
                    # 子盒内容自 +8 起为一个 'data' FullBox
                    data = _child(f, off + 8, off + size, b"data")
                    if data:
                        doff, dsize = data
                        payload_start = doff + 16            # size+type+verflags+locale
                        payload_len = dsize - 16
                        if payload_len > 0:
                            zeroed = True
                            seen = 0
                            while seen < payload_len:
                                step = min(zero_scan_cap, payload_len - seen)
                                f.seek(payload_start + seen)
                                chunk = f.read(step)
                                if not chunk or any(chunk):
                                    zeroed = False
                                    break
                                seen += step
                                if step >= zero_scan_cap:
                                    break                 # 超帽仍全 0 → 视为清零
                            if zeroed:
                                found.append({
                                    "atom": "©cmt",
                                    "path": "moov/udta/meta/ilst/©cmt",
                                    "offset": payload_start,
                                    "payload_size": payload_len,
                                })
                    off += size
                    continue
                off += size
    except OSError:
        return []
    return found
