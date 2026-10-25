"""按模态把隐式标识写到既有适配器，并给出统一的解析 / 校验结果。

图片走 ``ImageMetadataService``。视频、M4A、MP3、Markdown、PDF、HTML、DOCX
走 ``app.adapters`` 里与单文件打标相同的适配器。字幕和纯文本没有这些容器，
仍用尾段写入。校验规则同一套：恰好一份、附录 E 七字段、回读与提交一致。
"""
from __future__ import annotations

from pathlib import Path

from ..adapters import get_adapter
from ..adapters.base import AdapterError
from ..core import aigc
from ..core.mimetype import SNIFF_BYTES, detect_mime
from ..core.reader import read_aigc_records
from ..labeling_error import LabelingError
from ..tools import ToolPaths
from .text_label import text_records, visible_text, write_text_label

ROLES = ("text", "image", "audio", "video")
ROLE_SUFFIX = {"text": "T", "image": "I", "audio": "A", "video": "V"}
# 与单文件 /api/v1/metadata-label-jobs 共用的文档载体。
_DOCUMENT_MIMES = frozenset({
    "text/markdown",
    "application/pdf",
    "text/html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})
_ROLE_MIMES = {
    "audio": frozenset({"audio/mp4", "audio/mpeg"}),
    "video": frozenset({"video/mp4"}),
}
_TRAILER_SUFFIXES = {".txt", ".vtt", ".srt"}


def derive_component_aigc(base: dict, role: str) -> dict:
    """成分文件使用派生编号，使每个内容文件的 ProduceID 仍然唯一（附录 E.e）。

    父编号留给重组后的那一份内容文件。传播编号在首次写入时与制作编号保持一致
    （附录 E 注 1）；若调用方已经给出不同的传播编号，则同样加角色后缀。
    """
    if role not in ROLE_SUFFIX:
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", f"不支持的模态角色: {role}")
    normalized = aigc.normalize_first_write(dict(base))
    errors = aigc.validate_aigc(normalized)
    if errors:
        raise LabelingError("AIGC_SCHEMA_INVALID", "AIGC 元数据不符合附录 E。", errors)
    suffix = ROLE_SUFFIX[role]
    derived = dict(normalized)
    parent_produce = normalized["ProduceID"]
    parent_propagate = normalized["PropagateID"]
    derived["ProduceID"] = f"{parent_produce}-{suffix}"
    if parent_propagate == parent_produce:
        derived["PropagateID"] = derived["ProduceID"]
    else:
        derived["PropagateID"] = f"{parent_propagate}-{suffix}"
    check = aigc.validate_aigc(derived)
    if check:
        raise LabelingError("AIGC_SCHEMA_INVALID", "派生后的成分标识未通过附录 E 校验。", check)
    return derived


def prepare_aigc(base: dict) -> dict:
    normalized = aigc.normalize_first_write(dict(base))
    errors = aigc.validate_aigc(normalized)
    if errors:
        raise LabelingError("AIGC_SCHEMA_INVALID", "AIGC 元数据不符合附录 E。", errors)
    return normalized


def _carrier_mime(role: str, path: Path) -> str | None:
    """选择与单文件打标相同的适配器。

    从 MP4 拆出的 AAC 经常仍带着 ``mp42`` 品牌，``detect_mime`` 会把它看成视频。
    多模态已经按角色把它存成无画面的 ``.m4a``，这里改走 M4A 适配器，不改全局识别。
    """
    sniffed = _sniff_mime(path)
    if role == "audio" and sniffed == "video/mp4" and path.suffix.lower() in {".m4a", ".m4b"}:
        return "audio/mp4"
    return sniffed


def _sniff_mime(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:SNIFF_BYTES]
    except OSError as exc:
        raise LabelingError("METADATA_READBACK_FAILED", f"无法读取文件: {exc}") from exc
    return detect_mime(head, path.name)


def _adapter_for_mime(mime: str, tools: ToolPaths):
    try:
        return get_adapter(
            mime, exiftool=tools.exiftool, ffprobe=tools.ffprobe,
            ffmpeg=tools.ffmpeg, exiftool_config=tools.exiftool_config,
        )
    except AdapterError as exc:
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", str(exc)) from exc


def _record_dicts(found) -> list[dict]:
    return [{
        "source": record.tag_key,
        "raw": record.raw,
        "aigc": record.aigc,
        "location": record.location,
    } for record in found]


def _records_from_adapter(path: Path, tools: ToolPaths, mime: str) -> list[dict]:
    adapter = _adapter_for_mime(mime, tools)
    try:
        found = adapter.detect_existing(path)
    except AdapterError as exc:
        raise LabelingError("METADATA_READBACK_FAILED", str(exc)) from exc
    return _record_dicts(found)


def _write_with_adapter(source: Path, destination: Path, aigc_obj: dict,
                        policy: str, tools: ToolPaths, mime: str) -> str:
    adapter = _adapter_for_mime(mime, tools)
    try:
        existing = adapter.detect_existing(source)
    except AdapterError as exc:
        raise LabelingError("METADATA_READBACK_FAILED", str(exc)) from exc
    if existing and policy == "reject":
        raise LabelingError("AIGC_METADATA_EXISTS", "文件已存在 AIGC 隐式标识，且策略为拒绝。")
    try:
        if existing and policy == "replace":
            cleaned = destination.with_name(destination.stem + ".clean" + destination.suffix)
            adapter.remove_aigc(source, cleaned)
            adapter.write_metadata(cleaned, destination, aigc_obj)
            cleaned.unlink(missing_ok=True)
        else:
            adapter.write_metadata(source, destination, aigc_obj)
    except AdapterError as exc:
        raise LabelingError("METADATA_WRITE_FAILED", str(exc)) from exc
    return adapter.carrier_id


def _records_from_reader(path: Path, tools: ToolPaths) -> list[dict]:
    try:
        found = read_aigc_records(str(path), exiftool=tools.exiftool, config=tools.exiftool_config)
    except Exception as exc:  # ReaderError 以及 exiftool 缺失
        raise LabelingError("METADATA_READBACK_FAILED", f"读取隐式标识失败: {exc}") from exc
    return _record_dicts(found)


def parse_file(role: str, path: str | Path, tools: ToolPaths | None = None) -> list[dict]:
    """解析单个成分文件中的隐式标识。``role`` 决定读取器。"""
    tools = tools or ToolPaths()
    file_path = Path(path)
    if role == "text":
        mime = _carrier_mime(role, file_path)
        if mime in _DOCUMENT_MIMES:
            return _records_from_adapter(file_path, tools, mime)
        return text_records(file_path)
    if role == "image":
        return _records_from_reader(file_path, tools)
    if role in ("audio", "video"):
        mime = _carrier_mime(role, file_path)
        if mime in _ROLE_MIMES[role]:
            return _records_from_adapter(file_path, tools, mime)
        return _records_from_reader(file_path, tools)
    raise LabelingError("UNSUPPORTED_MEDIA_TYPE", f"不支持的模态角色: {role}")


def label_file(role: str, src: str | Path, dst: str | Path, aigc_obj: dict,
               policy: str, tools: ToolPaths) -> str:
    """写入一份隐式标识，返回载体 id。``dst`` 必须是新路径。"""
    if policy not in ("reject", "replace"):
        raise LabelingError("AIGC_SCHEMA_INVALID", "existing_metadata_policy 必须为 reject 或 replace。")
    source = Path(src)
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if role == "text":
        mime = _carrier_mime(role, source)
        if mime in _DOCUMENT_MIMES:
            return _write_with_adapter(source, destination, aigc_obj, policy, tools, mime)
        if source.suffix.lower() in _TRAILER_SUFFIXES:
            write_text_label(source, destination, aigc_obj, policy=policy)
            return "text-trailer-aigc-v1"
        raise LabelingError(
            "UNSUPPORTED_MEDIA_TYPE",
            "文本支持 txt、vtt、srt 尾段，以及 Markdown、PDF、HTML、DOCX 的既有载体。",
        )
    if role == "image":
        return _label_image(source, destination, aigc_obj, policy, tools)
    if role in ("audio", "video"):
        mime = _carrier_mime(role, source)
        expected = _ROLE_MIMES[role]
        if mime not in expected:
            raise LabelingError(
                "UNSUPPORTED_MEDIA_TYPE",
                f"{role} 文件的实际类型是 {mime or '未知'}，不在已注册适配器中。",
            )
        return _write_with_adapter(source, destination, aigc_obj, policy, tools, mime)
    raise LabelingError("UNSUPPORTED_MEDIA_TYPE", f"不支持的模态角色: {role}")


def _label_image(src: Path, dst: Path, aigc_obj: dict, policy: str, tools: ToolPaths) -> str:
    from ..core.image_bridge import build_client
    from ..metadata.identifier_registry import SQLiteIdentifierRegistry
    from ..metadata.image_adapter import ImageMetadataError, ImageMetadataService

    client = build_client(tools.exiftool, tools.exiftool_config)
    registry_path = tools.registry_path or str(dst.parent / "aigc_identifiers.sqlite3")
    service = ImageMetadataService(client, SQLiteIdentifierRegistry(registry_path))
    try:
        result = service.write(
            str(src), str(dst), {"AIGC": aigc_obj},
            policy=policy,
            initial_write=aigc.first_write_consistent(aigc_obj),
        )
    except ImageMetadataError as exc:
        raise LabelingError(exc.code, str(exc), exc.details) from None
    return result.carrier


def check_item(check_id: str, clause: str, passed: bool, message: str,
               severity: str = "error") -> dict:
    return {
        "id": check_id,
        "clause": clause,
        "passed": bool(passed),
        "severity": severity,
        "message": message,
    }


def verify_implicit(role: str, path: str | Path, expected: dict | None,
                    tools: ToolPaths, *, integrity_src: str | Path | None = None,
                    duration_tolerance: float = 0.1) -> dict:
    """校验一个内容文件的隐式标识：份数、结构、回读一致性，以及可执行的完整性。"""
    records = parse_file(role, path, tools)
    parsed = [record["aigc"] for record in records if record.get("aigc")]
    checks = [
        check_item(
            "single_implicit_label", "6.1.c", len(records) == 1,
            "仅保留一份文件元数据隐式标识" if len(records) == 1
            else f"检出 {len(records)} 份文件元数据隐式标识，第 6.1.c 条要求仅一份",
        )
    ]
    schema_ok = len(parsed) == 1 and not aigc.validate_aigc(parsed[0])
    checks.append(check_item(
        "appendix_e_schema", "附录E", schema_ok,
        "隐式标识符合附录 E 七字段" if schema_ok else "隐式标识缺失或不符合附录 E",
    ))
    if expected is not None and schema_ok:
        matched = parsed[0] == expected
        checks.append(check_item(
            "readback_matches", "附录E", matched,
            "回读结果与写入对象一致" if matched else "回读结果与写入对象不一致",
        ))
    if integrity_src is not None and role == "text":
        mime = _carrier_mime(role, Path(path))
        if mime in _DOCUMENT_MIMES:
            checks.append(_adapter_integrity(mime, integrity_src, path, tools, duration_tolerance))
        else:
            try:
                same = visible_text(path) == visible_text(integrity_src)
            except OSError as exc:
                same = False
                checks.append(check_item("text_integrity", "6.1", False, f"无法比较正文: {exc}"))
            else:
                checks.append(check_item(
                    "text_integrity", "6.1", same,
                    "可见正文未被隐式标识改写" if same else "可见正文在写入前后不一致",
                ))
    if integrity_src is not None and role in ("audio", "video"):
        mime = _carrier_mime(role, Path(path))
        if mime in _ROLE_MIMES[role]:
            checks.append(_adapter_integrity(mime, integrity_src, path, tools, duration_tolerance))
        else:
            checks.append(check_item("media_integrity", "6.1", False, "无法按已注册适配器校验媒体完整性。"))
    if role == "image" and integrity_src is not None:
        checks.append(_image_pixel_check(integrity_src, path))
    passed = all(item["passed"] for item in checks if item["severity"] == "error")
    return {
        "role": role,
        "passed": passed,
        "count": len(records),
        "labels": parsed,
        "records": records,
        "checks": checks,
    }


def _adapter_integrity(mime: str, src: str | Path, dst: str | Path,
                       tools: ToolPaths, duration_tolerance: float) -> dict:
    adapter = _adapter_for_mime(mime, tools)
    try:
        media = adapter.media_integrity_check(src, dst, duration_tolerance=duration_tolerance)
    except AdapterError as exc:
        return check_item("media_integrity", "6.1", False, str(exc))
    return check_item(
        "media_integrity", "6.1", media.passed,
        "内容主体在写入标识后保持可用" if media.passed else f"媒体完整性未通过: {media.reason}",
    )


def _image_pixel_check(src: str | Path, dst: str | Path) -> dict:
    try:
        from PIL import Image
        import hashlib
        with Image.open(src) as left, Image.open(dst) as right:
            left.load()
            right.load()
            same = (
                left.size == right.size
                and left.mode == right.mode
                and hashlib.sha256(left.tobytes()).digest()
                == hashlib.sha256(right.tobytes()).digest()
            )
    except Exception as exc:  # 解码失败即完整性失败
        return check_item("image_integrity", "6.1", False, f"图片无法比较像素: {exc}")
    return check_item(
        "image_integrity", "6.1", same,
        "图片像素在写入元数据后保持不变" if same else "图片像素在写入元数据后发生变化",
    )


def all_passed(checks: list[dict]) -> bool:
    return all(item["passed"] for item in checks if item.get("severity", "error") == "error")
