"""文件存储与原子发布（开发手册 §9.2 / §12）。

- 物理存储名一律用服务端生成 ID，不用用户文件名（防路径穿越/覆盖）。
- 结果文件先写临时文件，全部校验通过后再 os.replace 原子发布。
- 原文件与结果文件分离（§4.4），保留期可配置（§12.3）。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

import sqlite3  # noqa: F401  (仅为类型说明)


class StorageError(Exception):
    pass


_SAFE_NAME_RE = re.compile(r'[^\w.\-]')


def safe_display_name(name: str) -> str:
    """安全化下载文件名（§13）：只保留字母数字、点、横线、下划线。"""
    return _SAFE_NAME_RE.sub("_", os.path.basename(name)) or "file"


class FileStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.original_dir = self.root / "original"
        self.staging_dir = self.root / "staging"
        self.output_dir = self.root / "output"
        for d in (self.original_dir, self.staging_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

    def new_stored_name(self, suffix: str = "") -> str:
        return uuid.uuid4().hex + suffix

    def save_original(self, stream, suffix: str) -> tuple[str, str, int]:
        """保存上传的原文件，返回 (存储名, sha256, size)。分块写 + 边写边算哈希。"""
        stored = self.new_stored_name(suffix)
        path = self.original_dir / stored
        h = hashlib.sha256()
        size = 0
        with open(path, "wb") as f:
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
        return stored, h.hexdigest(), size

    def original_path(self, stored_name: str) -> Path:
        return self.original_dir / stored_name

    def staging_path(self, stored_name: str) -> Path:
        return self.staging_dir / stored_name

    def output_path(self, stored_name: str) -> Path:
        return self.output_dir / stored_name

    def sha256_of(self, path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def publish(self, tmp_path: str | Path, stored_name: str) -> Path:
        """原子发布：临时结果文件全部校验通过后改名进输出目录。"""
        final = self.output_path(stored_name)
        try:
            os.replace(str(tmp_path), str(final))
        except OSError as e:
            raise StorageError(f"结果文件发布失败: {e}") from None
        return final

    def discard(self, path: str | Path) -> None:
        try:
            if Path(path).is_file():
                os.remove(path)
        except OSError:
            pass

    def copy_to_staging(self, src: str | Path, suffix: str) -> Path:
        """把原文件复制到临时工作区（适配器在副本上操作，绝不碰原文件）。"""
        dst = self.staging_path(self.new_stored_name(suffix))
        shutil.copyfile(src, dst)
        return dst

    def cleanup_expired(self, store, cutoff_iso: str, statuses: tuple[str, ...]) -> int:
        """按保留策略清理过期任务（§12.3）：原文件、结果文件、任务记录一并清理。
        返回清理的任务数。原文件与结果文件分离（§4.4），各自目录分别删除。"""
        rows = store.list_expired(cutoff_iso, statuses)
        for row in rows:
            if row.get("output_stored_name"):
                self.discard(self.output_path(row["output_stored_name"]))
            if row.get("original_stored_name"):
                self.discard(self.original_path(row["original_stored_name"]))
        store.delete_jobs([r["job_id"] for r in rows])
        return len(rows)
