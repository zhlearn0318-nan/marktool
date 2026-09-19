import subprocess

import pytest

from app.metadata.exiftool_client import ExifToolClient, ExifToolExecutionError


def _client(tmp_path):
    executable = tmp_path / "exiftool.exe"
    executable.write_bytes(b"placeholder")
    config = tmp_path / "config"
    config.write_text("1;", encoding="utf-8")
    return ExifToolClient(str(executable), str(config), timeout_seconds=1)


def test_probe_version_accepts_realistic_version(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(
        client,
        "_run",
        lambda _arguments: subprocess.CompletedProcess([], 0, b"13.59\n", b""),
    )
    assert client.probe_version() == "13.59"


def test_probe_version_rejects_fake_output(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(
        client,
        "_run",
        lambda _arguments: subprocess.CompletedProcess([], 0, b"not-exiftool\n", b""),
    )
    with pytest.raises(ExifToolExecutionError):
        client.probe_version()


def test_probe_version_rejects_timeout(tmp_path, monkeypatch):
    client = _client(tmp_path)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("exiftool", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ExifToolExecutionError, match="超时"):
        client.probe_version()


def test_probe_version_rejects_nonzero_exit(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, b"", b"bad"),
    )
    with pytest.raises(ExifToolExecutionError, match="退出码 2"):
        client.probe_version()
