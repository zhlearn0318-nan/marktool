import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence


# 合并后图片与视频共用同一份 ExifTool 配置（项目根 config/exiftool_aigc.config）。
# 原实现取的是本模块同目录下的副本，会在仓库里形成第二份命名空间定义；
# 两个交付线必须落在同一个命名空间上，因此这里改为指向项目级唯一配置。
_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "exiftool_aigc.config"
)


class ExifToolNotFoundError(RuntimeError):
    pass


class ExifToolExecutionError(RuntimeError):
    pass


class ExifToolClient:
    """以参数数组封装 ExifTool，业务代码不直接拼接命令。"""

    def __init__(
        self,
        executable: Optional[str] = None,
        config_path: Optional[str] = None,
        timeout_seconds: int = 30,
    ):
        self.executable = self._resolve_executable(executable)
        self.config_path = Path(
            config_path or os.getenv("AIGC_EXIFTOOL_CONFIG") or _DEFAULT_CONFIG_PATH
        )
        self.timeout_seconds = timeout_seconds
        if not self.config_path.is_file():
            raise ExifToolNotFoundError("未找到项目的 ExifTool AIGC 配置文件")
        if not os.access(self.config_path, os.R_OK):
            raise ExifToolNotFoundError("项目的 ExifTool AIGC 配置文件不可读")

    @staticmethod
    def _resolve_executable(explicit_path: Optional[str]) -> str:
        candidate = explicit_path or os.getenv("EXIFTOOL_PATH")
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                return str(path)
            raise ExifToolNotFoundError("EXIFTOOL_PATH 指向的文件不存在")

        discovered = shutil.which("exiftool") or shutil.which("exiftool.exe")
        if discovered:
            return discovered
        raise ExifToolNotFoundError(
            "未找到 ExifTool；请安装后设置 EXIFTOOL_PATH 环境变量"
        )

    def _command(self, argument_file: str) -> list[str]:
        return [
            self.executable,
            "-config",
            str(self.config_path),
            "-charset",
            "exiftool=UTF8",
            "-charset",
            "filename=UTF8",
            "-@",
            argument_file,
        ]

    def _run(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        # Windows 会按当前代码页重编码命令行。把实际参数放进 UTF-8 argfile，
        # 才能可靠处理中文字段值和 Unicode 文件名。
        argument_file: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                suffix=".exiftool.args",
                delete=False,
            ) as stream:
                argument_file = stream.name
                for argument in arguments:
                    value = str(argument)
                    if "\n" in value or "\r" in value:
                        raise ExifToolExecutionError("ExifTool 参数不能包含换行")
                    stream.write(value + "\n")
            completed = subprocess.run(
                self._command(argument_file),
                shell=False,
                check=False,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExifToolExecutionError("ExifTool 处理超时") from exc
        except OSError as exc:
            raise ExifToolExecutionError("ExifTool 无法启动") from exc
        finally:
            if argument_file:
                Path(argument_file).unlink(missing_ok=True)

        if completed.returncode != 0:
            raise ExifToolExecutionError(
                f"ExifTool 执行失败（退出码 {completed.returncode}）"
            )
        return completed

    @staticmethod
    def _require_updated(completed: subprocess.CompletedProcess[bytes]) -> None:
        output = completed.stdout.decode("utf-8", "replace").lower()
        if "1 image files updated" not in output and "1 images updated" not in output:
            raise ExifToolExecutionError("ExifTool 未确认图片元数据已更新")

    def read_raw_xmp(self, file_path: str) -> str:
        completed = self._run(["-b", "-XMP", file_path])
        return completed.stdout.decode("utf-8-sig", "replace")

    def probe_version(self) -> str:
        """实际启动 ExifTool，返回经过格式校验的版本号。"""
        completed = self._run(["-ver"])
        version = completed.stdout.decode("ascii", "replace").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)+", version):
            raise ExifToolExecutionError("ExifTool 返回了无法识别的版本信息")
        return version

    def read_known_aigc_values(self, file_path: str) -> list[str]:
        completed = self._run([
            "-a",
            "-s3",
            "-XMP-aigc:AIGC",
            "-XMP-aigc:metadata",
            file_path,
        ])
        text = completed.stdout.decode("utf-8", "replace")
        return [line.strip() for line in text.splitlines() if line.strip()]

    def remove_known_aigc(self, file_path: str) -> None:
        completed = self._run([
            "-api",
            "IgnoreMinorErrors=1",
            "-overwrite_original",
            "-XMP-aigc:AIGC=",
            "-XMP-aigc:metadata=",
            file_path,
        ])
        self._require_updated(completed)

    def write_aigc(self, file_path: str, serialized_document: str) -> None:
        completed = self._run([
            "-overwrite_original",
            f"-XMP-aigc:AIGC={serialized_document}",
            file_path,
        ])
        self._require_updated(completed)
