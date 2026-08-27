"""配置加载（config/default_config.yaml + 环境变量覆盖）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PathsConfig:
    exiftool: str = "exiftool"
    ffprobe: str = "ffprobe"
    ffmpeg: str = "ffmpeg"
    exiftool_config: str = str(PROJECT_ROOT / "config" / "exiftool_aigc.config")


@dataclass
class StorageConfig:
    root: str = "./storage"
    max_file_bytes: int = 512 * 1024 * 1024
    output_retention_hours: int = 72
    job_retention_hours: int = 168


@dataclass
class LimitsConfig:
    max_concurrent_jobs: int = 4
    job_timeout_seconds: int = 600
    duration_tolerance_seconds: float = 0.1


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    paths: PathsConfig = field(default_factory=PathsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    capabilities: dict = field(default_factory=lambda: {
        "image/jpeg": False, "image/png": False, "video/mp4": True})


def load_settings(yaml_path: str | Path | None = None) -> Settings:
    s = Settings()
    if yaml_path is None:
        yaml_path = PROJECT_ROOT / "config" / "default_config.yaml"
    raw = _read_yaml(yaml_path)
    if not raw:
        return s

    server = raw.get("server", {})
    s.host = server.get("host", s.host)
    s.port = int(server.get("port", s.port))

    paths = raw.get("paths", {})
    s.paths.exiftool = paths.get("exiftool", s.paths.exiftool)
    s.paths.ffprobe = paths.get("ffprobe", s.paths.ffprobe)
    s.paths.ffmpeg = paths.get("ffmpeg", s.paths.ffmpeg)
    if paths.get("exiftool_config"):
        s.paths.exiftool_config = _resolve(paths["exiftool_config"])

    st = raw.get("storage", {})
    s.storage.root = st.get("root", s.storage.root)
    s.storage.max_file_bytes = int(st.get("max_file_bytes", s.storage.max_file_bytes))
    s.storage.output_retention_hours = int(st.get("output_retention_hours",
                                                  s.storage.output_retention_hours))
    s.storage.job_retention_hours = int(st.get("job_retention_hours",
                                               s.storage.job_retention_hours))

    lim = raw.get("limits", {})
    s.limits.max_concurrent_jobs = int(lim.get("max_concurrent_jobs", s.limits.max_concurrent_jobs))
    s.limits.job_timeout_seconds = int(lim.get("job_timeout_seconds", s.limits.job_timeout_seconds))
    s.limits.duration_tolerance_seconds = float(
        lim.get("duration_tolerance_seconds", s.limits.duration_tolerance_seconds))

    caps = raw.get("capabilities", {})
    if caps:
        s.capabilities = {k: bool(v) for k, v in caps.items()}
    return s


def _resolve(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def _read_yaml(path) -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}
