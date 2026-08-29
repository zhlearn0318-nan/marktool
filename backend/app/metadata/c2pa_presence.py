from dataclasses import dataclass
from enum import Enum
from pathlib import Path


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_C2PA_MANIFEST_UUID = bytes.fromhex("6332706100110010800000aa00389b71")
_C2PA_LABEL = b"c2pa\x00"


class C2PAPresence(str, Enum):
    NOT_FOUND = "not_found"
    PRESENT_UNVERIFIED = "present_unverified"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class C2PAPresenceResult:
    status: C2PAPresence
    carrier: str | None = None
    detail: str | None = None


def _png_presence(path: Path) -> C2PAPresenceResult:
    with path.open("rb") as stream:
        if stream.read(8) != _PNG_SIGNATURE:
            return C2PAPresenceResult(
                C2PAPresence.INDETERMINATE,
                detail="文件不是有效的 PNG 签名",
            )
        while True:
            header = stream.read(8)
            if not header:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="PNG 缺少 IEND 数据块",
                )
            if len(header) != 8:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="PNG 数据块头不完整",
                )
            length = int.from_bytes(header[:4], "big")
            chunk_type = header[4:]
            if length > 512 * 1024 * 1024:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="PNG 数据块长度异常",
                )
            payload = stream.read(length)
            crc = stream.read(4)
            if len(payload) != length or len(crc) != 4:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="PNG 数据块不完整",
                )
            if chunk_type == b"caBX":
                return C2PAPresenceResult(
                    C2PAPresence.PRESENT_UNVERIFIED,
                    carrier="png:caBX",
                    detail="发现 C2PA 规范使用的 caBX 数据块；尚未验证清单或签名",
                )
            if chunk_type == b"IEND":
                return C2PAPresenceResult(C2PAPresence.NOT_FOUND)


def _jpeg_presence(path: Path) -> C2PAPresenceResult:
    app11_payloads: list[bytes] = []
    with path.open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            return C2PAPresenceResult(
                C2PAPresence.INDETERMINATE,
                detail="文件不是有效的 JPEG SOI 标记",
            )
        while True:
            prefix = stream.read(1)
            if not prefix:
                break
            if prefix != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="JPEG 标记不完整",
                )
            if marker in {b"\xd9", b"\xda"}:
                break
            if marker in {bytes([value]) for value in range(0xD0, 0xD8)} | {b"\x01"}:
                continue
            raw_length = stream.read(2)
            if len(raw_length) != 2:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="JPEG 数据段长度不完整",
                )
            length = int.from_bytes(raw_length, "big")
            if length < 2:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="JPEG 数据段长度无效",
                )
            payload = stream.read(length - 2)
            if len(payload) != length - 2:
                return C2PAPresenceResult(
                    C2PAPresence.INDETERMINATE,
                    detail="JPEG 数据段不完整",
                )
            if marker == b"\xeb":
                app11_payloads.append(payload)

    if not app11_payloads:
        return C2PAPresenceResult(C2PAPresence.NOT_FOUND)

    # APP11 也可承载非 C2PA 数据，因此不能仅凭 FFEB 判断存在。
    # C2PA Manifest Store 的 JUMBF 描述包含固定 UUID 和 c2pa 标签；
    # 合并 APP11 负载后再检查，可覆盖 UUID/标签跨分片的情况。
    joined = b"".join(app11_payloads)
    if _C2PA_MANIFEST_UUID in joined and _C2PA_LABEL in joined:
        return C2PAPresenceResult(
            C2PAPresence.PRESENT_UNVERIFIED,
            carrier="jpeg:APP11/JUMBF",
            detail="发现带 c2pa 标签和 Manifest Store UUID 的 APP11/JUMBF 数据；尚未验证签名",
        )
    return C2PAPresenceResult(C2PAPresence.NOT_FOUND)


def detect_c2pa_presence(file_path: str, format_name: str) -> C2PAPresenceResult:
    """只做保护性的存在性判断，不验证 C2PA 清单、证书链或签名。"""
    path = Path(file_path)
    try:
        if format_name.upper() == "PNG":
            return _png_presence(path)
        if format_name.upper() == "JPEG":
            return _jpeg_presence(path)
        return C2PAPresenceResult(
            C2PAPresence.INDETERMINATE,
            detail=f"当前未实现 {format_name} 的 C2PA 存在性检查",
        )
    except OSError as exc:
        return C2PAPresenceResult(
            C2PAPresence.INDETERMINATE,
            detail=f"读取 C2PA 承载位置失败: {exc}",
        )
