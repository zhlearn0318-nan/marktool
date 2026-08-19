import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence


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
        self.config_path = Path(config_path or Path(__file__).with_name("exiftool_aigc.config"))
        self.timeout_seconds = timeout_seconds
        if not self.config_path.is_file():
            raise ExifToolNotFoundError("未找到项目的 ExifTool AIGC 配置文件")

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
