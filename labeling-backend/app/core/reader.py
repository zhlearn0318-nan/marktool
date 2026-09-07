"""共享 AIGC 读取器（开发手册 §14：写入与检测复用同一套读取校验能力）。

用 ExifTool 遍历文件全部元数据标签，找出所有含 AIGC 的记录。
检测"已有标识"、回读校验、"整体替换"前的定位都依赖本模块——
写入器与检测器共用它，避免出现"自己写入成功、自己检测器判失败"。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from . import aigc


class ReaderError(Exception):
    pass


@dataclass
class AIGCRecord:
    """文件内找到的一份 AIGC 标识。tag_key 形如 'XMP:AIGC'、'QuickTime:Comment'。

    location：人类可读的载体位置（物理落点探测见 2026-09-07：规范 XMP 在顶层
    Adobe-XMP uuid 盒；QuickTime 旧载体在 moov/udta/meta/ilst）。仅用于诊断文案，
    不影响判定逻辑。
    """
    tag_key: str
    raw: str
    aigc: dict | None          # 解析成功的内层 AIGC 对象，否则 None
    location: str | None = None


def carrier_location(tag_key: str) -> str | None:
    """按 ExifTool 组把 tag_key 映射成载体位置描述（诊断用，仅增不改判定）。

    - XMP（规范新载体）：顶层 Adobe-XMP uuid 盒（usertype be7acfcb…）内 XMP 包。
    - QuickTime（旧载体）：moov/udta/meta/ilst 的数据原子（如 ©cmt）。
    其余（uuid 盒、其他命名空间）返回 None，交由上层用 tag_key 原文兜底。
    """
    if tag_key.startswith("XMP"):
        return "XMP 新载体（顶层 Adobe-XMP uuid 盒内 XMP 包）"
    if tag_key.startswith("QuickTime"):
        return "QuickTime 旧载体（moov/udta/meta/ilst 数据原子）"
    return None


def exiftool_tags(path: str, exiftool: str, config: str | None) -> dict:
    """ExifTool 全标签 JSON 读取：-a 保留重复、-G 带组、-s 短标签名。"""
    cmd = [exiftool, "-json", "-a", "-G", "-s", path]
    if config:
        cmd[1:1] = ["-config", config]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        raise ReaderError("exiftool 读取超时") from None
    if out.returncode != 0:
        raise ReaderError(f"exiftool 调用失败: {out.stderr.strip()[:200]}")
    try:
        data = json.loads(out.stdout)
    except ValueError:
        raise ReaderError("exiftool 输出无法解析") from None
    return data[0] if data else {}


# exiftool 的结构回显字段：值是文件路径/名称/类型等磁盘属性，不是文件内可承载
# 标识的元数据。国标隐式标识只可能写在 XMP / QuickTime 等媒体元数据标签里；
# 若不排除，仓库/上传路径含 "AIGC" 字样时 SourceFile、File:Directory 的值会把
# 干净文件误判为"已有标识"。
_ECHO_PREFIXES = ("SourceFile", "File:", "ExifTool:")


def _looks_like_aigc(tag_key: str, value: str) -> bool:
    """标签名含 AIGC，或值内含 "AIGC" 字样（子串匹配）。

    与现有检测器 aigc_check.py 的判定（`"AIGC" in v`）保持一致（手册 §14），
    避免"写入器认识、检测器不认识"的分叉。值中出现 AIGC 字样的标签按已有
    标识对待（reject 时保守拒绝，replace 时整体移除）。结构回显字段除外——
    它们不是候选载体。
    """
    if tag_key.startswith(_ECHO_PREFIXES):
        return False
    return "AIGC" in tag_key or "AIGC" in value


def read_aigc_records(path: str, exiftool: str = "exiftool",
                      config: str | None = None) -> list[AIGCRecord]:
    """扫描文件全部元数据标签，返回所有 AIGC 记录（保持标签顺序）。"""
    meta = exiftool_tags(path, exiftool, config)
    records: list[AIGCRecord] = []
    for key, val in meta.items():
        values = [str(v) for v in val] if isinstance(val, list) else [str(val)]
        for v in values:
            if _looks_like_aigc(key, v):
                records.append(AIGCRecord(tag_key=key, raw=v,
                                          aigc=aigc.parse_aigc(v),
                                          location=carrier_location(key)))
    return records
