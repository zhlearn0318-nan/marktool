"""多模态内容：拆分、按模态隐式标识、解析、重组、校验。

GB 45438—2025 没有单独的“多模态元数据”结构。本模块按第 3.1 条的模态
（文本、图片、音频、视频）把复合内容拆成内容文件，每个文件按第 6.1 条和
附录 E 保留恰好一份隐式标识；重组后的那一份内容文件同样只保留一份
（第 6.1.c 条）。成分文件上的标识在进入重组容器之前去掉，避免容器扫描时
看到多份 AIGC。

重组形态：
- 只含视频、音频、文本 → 一个 MP4，标识写在容器 XMP（复用 Mp4Adapter）；
- 含图片，或没有视频轨的其他组合 → zip 包，根上只有一个 ``AIGC.json``。

显式标识（第 5 章）不在本流程里自动烧进画面或字幕。虚拟场景与交互场景
另见 ``app.scenes``。
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from ..core import aigc
from ..labeling_error import LabelingError
from ..modalities.dispatch import (
    ROLE_SUFFIX, all_passed, check_item, derive_component_aigc, label_file,
    parse_file, prepare_aigc, verify_implicit,
)
from ..modalities.text_label import MARKER, parse_text_bytes
from ..tools import ToolPaths
from .ffmpeg_util import duration_of, is_attached_picture, normalize_to_m4a, probe, run_tool

AIGC_JSON = "AIGC.json"
MANIFEST = "manifest.json"
_TEXT_SUFFIXES = {".txt", ".vtt", ".srt", ".md"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def text_to_vtt(source: Path, duration: float) -> str:
    visible, _ = parse_text_bytes(source.read_bytes())
    text = visible.decode("utf-8").lstrip("\ufeff")
    stripped = text.lstrip()
    if stripped.startswith("WEBVTT"):
        return text if text.endswith("\n") else text + "\n"
    if "-->" in text:
        lines = []
        for line in text.splitlines():
            if "-->" in line:
                line = line.replace(",", ".")
            lines.append(line)
        return "WEBVTT\n\n" + "\n".join(lines).strip() + "\n"
    end = _timestamp(duration if duration > 0 else 1.0)
    body = text.strip()
    return f"WEBVTT\n\n00:00:00.000 --> {end}\n{body}\n"


def _embedded_text(info: dict) -> str | None:
    tags = (info.get("format") or {}).get("tags") or {}
    for key in ("comment", "description", "lyrics", "synopsis"):
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if len(cleaned) < 1:
            continue
        lowered = cleaned.lower()
        if "lavf" in lowered or "lavc" in lowered or lowered.startswith("encoder"):
            continue
        return cleaned
    return None


def split_media(src: Path, dest_dir: Path, tools: ToolPaths) -> dict:
    """把一个容器拆成视频轨、音频轨、字幕或容器内文本。"""
    info = probe(src, tools.ffprobe)
    streams = info.get("streams") or []
    videos = [
        item for item in streams
        if item.get("codec_type") == "video" and not is_attached_picture(item)
    ]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    subtitles = [item for item in streams if item.get("codec_type") == "subtitle"]
    notes: list[str] = []
    roles: dict[str, Path] = {}
    audio_transcoded = False
    dest_dir.mkdir(parents=True, exist_ok=True)
    if len(audios) > 1:
        notes.append(f"检测到 {len(audios)} 条音频轨，只拆出第一条。")
    if len(subtitles) > 1:
        notes.append(f"检测到 {len(subtitles)} 条字幕轨，只拆出第一条。")
    if videos:
        target = dest_dir / "video.mp4"
        index = videos[0]["index"]
        run_tool([
            tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-map", f"0:{index}", "-an", "-sn", "-dn",
            "-c:v", "copy", str(target),
        ])
        roles["video"] = target
    if audios:
        target = dest_dir / "audio.m4a"
        index = audios[0]["index"]
        try:
            run_tool([
                tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src), "-map", f"0:{index}", "-vn", "-sn", "-dn",
                "-c:a", "copy", str(target),
            ])
        except LabelingError:
            target.unlink(missing_ok=True)
            run_tool([
                tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src), "-map", f"0:{index}", "-vn",
                "-c:a", "aac", "-b:a", "128k", str(target),
            ])
            audio_transcoded = True
            notes.append("音频轨无法原样复制进 M4A，已转码为 AAC。")
        roles["audio"] = target
    if subtitles:
        target = dest_dir / "subtitle.vtt"
        index = subtitles[0]["index"]
        run_tool([
            tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-map", f"0:{index}", "-f", "webvtt", str(target),
        ])
        roles["text"] = target
    else:
        embedded = _embedded_text(info)
        if embedded:
            target = dest_dir / "embedded.txt"
            target.write_text(embedded + "\n", encoding="utf-8")
            roles["text"] = target
            notes.append("未发现字幕轨，已从容器文本标签提取文本成分。")
    return {
        "roles": roles,
        "notes": notes,
        "audio_transcoded": audio_transcoded,
        "duration": duration_of(info),
    }


def _copy_named(src: Path, dest_dir: Path, name: str) -> Path:
    target = dest_dir / name
    shutil.copyfile(src, target)
    return target


def _accept_role_file(role: str, src: Path, dest_dir: Path, tools: ToolPaths) -> tuple[Path, bool]:
    """把调用方给出的成分整理成可打标的原始文件。返回路径和是否转码。"""
    if role == "text":
        if src.suffix.lower() not in _TEXT_SUFFIXES:
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "文本成分支持 txt、vtt、srt、md。")
        return _copy_named(src, dest_dir, "text" + src.suffix.lower()), False
    if role == "image":
        if src.suffix.lower() not in _IMAGE_SUFFIXES and not _is_image(src):
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "图片成分支持 JPEG 与 PNG。")
        suffix = ".jpg" if src.suffix.lower() in {".jpg", ".jpeg"} else ".png"
        if _is_jpeg(src):
            suffix = ".jpg"
        elif _is_png(src):
            suffix = ".png"
        return _copy_named(src, dest_dir, "image" + suffix), False
    if role == "audio":
        target = dest_dir / "audio.m4a"
        transcoded = normalize_to_m4a(src, target, ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe)
        return target, transcoded
    if role == "video":
        info = probe(src, tools.ffprobe)
        videos = [
            item for item in (info.get("streams") or [])
            if item.get("codec_type") == "video" and not is_attached_picture(item)
        ]
        if not videos:
            raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "视频成分里没有视频轨。")
        return _copy_named(src, dest_dir, "video.mp4"), False
    raise LabelingError("UNSUPPORTED_MEDIA_TYPE", f"不支持的模态角色: {role}")


def _is_jpeg(path: Path) -> bool:
    head = path.read_bytes()[:3]
    return head == b"\xff\xd8\xff"


def _is_png(path: Path) -> bool:
    return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _is_image(path: Path) -> bool:
    return _is_jpeg(path) or _is_png(path)


def recombine_kind(roles: set[str]) -> str:
    """含图片，或没有视频轨时，重组为只带一份 AIGC.json 的包。"""
    if "video" in roles and "image" not in roles:
        return "mp4"
    return "package"


def _mux_mp4(raw: dict[str, Path], dest: Path, tools: ToolPaths) -> None:
    video = raw["video"]
    info = probe(video, tools.ffprobe)
    duration = duration_of(info) or 1.0
    cmd = [tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
    maps = ["-map", "0:v:0"]
    codecs = ["-c:v", "copy"]
    next_index = 1
    if "audio" in raw:
        cmd.extend(["-i", str(raw["audio"])])
        maps.extend(["-map", f"{next_index}:a:0"])
        codecs.extend(["-c:a", "copy"])
        next_index += 1
    if "text" in raw:
        vtt = dest.with_name("subtitle-clean.vtt")
        vtt.write_text(text_to_vtt(raw["text"], duration), encoding="utf-8")
        cmd.extend(["-i", str(vtt)])
        maps.extend(["-map", f"{next_index}:0"])
        codecs.extend(["-c:s", "mov_text"])
    cmd.extend(maps)
    cmd.extend(codecs)
    cmd.append(str(dest))
    run_tool(cmd)


def _write_package(raw: dict[str, Path], dest: Path, aigc_obj: dict) -> None:
    payload_names = {}
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(AIGC_JSON, aigc.serialize_aigc(aigc_obj))
        for role, path in raw.items():
            name = f"payload/{role}{path.suffix.lower()}"
            payload_names[role] = name
            archive.write(path, name)
        manifest = {
            "standard": "GB45438-2025",
            "kind": "multimodal-package",
            "clause": "6.1",
            "files": payload_names,
        }
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))


def _subtitle_has_marker(path: Path, tools: ToolPaths, work: Path) -> bool:
    info = probe(path, tools.ffprobe)
    subtitles = [item for item in (info.get("streams") or []) if item.get("codec_type") == "subtitle"]
    if not subtitles:
        return False
    extracted = work / "composite-subtitle.vtt"
    index = subtitles[0]["index"]
    run_tool([
        tools.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-map", f"0:{index}", "-f", "webvtt", str(extracted),
    ])
    return MARKER in extracted.read_bytes()


def verify_composite_file(path: Path, kind: str, expected: dict | None,
                          tools: ToolPaths, *, roles: list[str] | None = None) -> dict:
    checks: list[dict] = []
    labels: list[dict] = []
    if kind == "mp4":
        records = parse_file("video", path, tools)
        labels = [item["aigc"] for item in records if item.get("aigc")]
        single = len(records) == 1 and len(labels) == 1
        checks.append(check_item(
            "single_implicit_label", "6.1.c", single,
            "重组后的视频文件仅保留一份隐式标识" if single
            else f"重组文件检出 {len(records)} 份隐式标识",
        ))
        schema_ok = single and not aigc.validate_aigc(labels[0])
        checks.append(check_item(
            "appendix_e_schema", "附录E", schema_ok,
            "重组文件的隐式标识符合附录 E" if schema_ok else "重组文件的隐式标识不符合附录 E",
        ))
        if expected is not None and schema_ok:
            matched = labels[0] == expected
            checks.append(check_item(
                "readback_matches", "附录E", matched,
                "重组文件回读结果与复合内容标识一致" if matched else "重组文件回读结果与复合内容标识不一致",
            ))
        try:
            leaked = _subtitle_has_marker(path, tools, path.parent)
            checks.append(check_item(
                "subtitle_without_second_label", "6.1.c", not leaked,
                "字幕轨正文不含文件元数据隐式标识" if not leaked else "字幕轨正文含有隐式标识分隔符",
            ))
        except LabelingError as exc:
            checks.append(check_item("subtitle_without_second_label", "6.1.c", False, exc.message))
        info = probe(path, tools.ffprobe)
        present = {item.get("codec_type") for item in info.get("streams") or []}
        if roles:
            expected_streams = {"video"}
            if "audio" in roles:
                expected_streams.add("audio")
            if "text" in roles:
                expected_streams.add("subtitle")
            missing = expected_streams - present
            checks.append(check_item(
                "recombined_streams", "3.1", not missing,
                "重组文件包含拆分时的视频、音频和文本轨" if not missing
                else f"重组文件缺少轨道: {sorted(missing)}",
            ))
        decode_ok = True
        try:
            run_tool([
                tools.ffmpeg, "-v", "error", "-hide_banner", "-t", "2",
                "-i", str(path), "-map", "0:v:0", "-f", "null", "-",
            ])
        except LabelingError as exc:
            decode_ok = False
            checks.append(check_item("playable", "6.1", False, exc.message))
        if decode_ok:
            checks.append(check_item("playable", "6.1", True, "重组视频可以解码。"))
    else:
        labels, package_checks = _verify_package(path, expected)
        checks.extend(package_checks)
    return {
        "kind": kind,
        "passed": all_passed(checks),
        "implicit_label": labels[0] if len(labels) == 1 else None,
        "implicit_labels": labels,
        "checks": checks,
    }


def _verify_package(path: Path, expected: dict | None) -> tuple[list[dict], list[dict]]:
    checks: list[dict] = []
    if not zipfile.is_zipfile(path):
        checks.append(check_item("package_format", "6.1", False, "重组包不是 zip。"))
        return [], checks
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        aigc_members = [name for name in names if Path(name).name == AIGC_JSON]
        single_name = aigc_members == [AIGC_JSON]
        checks.append(check_item(
            "single_implicit_label", "6.1.c", single_name,
            "重组包仅在 AIGC.json 保留一份隐式标识" if single_name
            else f"AIGC.json 的数量或位置不符合一份标识的要求: {aigc_members}",
        ))
        labels: list[dict] = []
        if single_name:
            raw = archive.read(AIGC_JSON).decode("utf-8")
            parsed = aigc.parse_aigc(raw)
            schema_ok = bool(parsed) and not aigc.validate_aigc(parsed)
            if parsed:
                labels.append(parsed)
            checks.append(check_item(
                "appendix_e_schema", "附录E", schema_ok,
                "AIGC.json 符合附录 E" if schema_ok else "AIGC.json 不符合附录 E",
            ))
            if expected is not None and schema_ok:
                matched = parsed == expected
                checks.append(check_item(
                    "readback_matches", "附录E", matched,
                    "重组包标识与复合内容标识一致" if matched else "重组包标识与复合内容标识不一致",
                ))
        manifest = {}
        if MANIFEST in names:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
        files = (manifest or {}).get("files") or {}
        leaked = False
        for role, name in files.items():
            if name not in names:
                checks.append(check_item("payload_present", "3.1", False, f"重组包缺少 {role} 成分。"))
                continue
            blob = archive.read(name)
            if role == "text":
                _visible, raws = parse_text_bytes(blob)
                leaked = leaked or bool(raws)
            elif role in {"image", "audio", "video"} and b"AIGC" in blob and b'"Label"' in blob:
                # 干净载荷不应再带附录 E 字符串。XMP 写入一定会同时出现这两个片段。
                leaked = leaked or b'"ContentProducer"' in blob
        checks.append(check_item(
            "payloads_without_second_label", "6.1.c", not leaked,
            "重组包内的成分载荷不再携带各自的隐式标识" if not leaked
            else "重组包的成分载荷里仍有隐式标识",
        ))
    return labels, checks


def _component_report(role: str, labeled: Path, raw: Path, expected: dict,
                      tools: ToolPaths, carrier: str) -> dict:
    verified = verify_implicit(role, labeled, expected, tools, integrity_src=raw)
    verified["file_name"] = labeled.name
    verified["carrier"] = carrier
    verified["produce_id"] = expected.get("ProduceID")
    return verified


def label_multimodal(
        output_dir: str | Path,
        aigc_obj: dict,
        *,
        policy: str = "reject",
        tools: ToolPaths | None = None,
        composite_video: str | Path | None = None,
        parts: dict[str, str | Path] | None = None,
) -> dict:
    tools = tools or ToolPaths()
    if policy not in ("reject", "replace"):
        raise LabelingError("AIGC_SCHEMA_INVALID", "existing_metadata_policy 必须为 reject 或 replace。")
    if composite_video and parts:
        raise LabelingError(
            "AMBIGUOUS_INPUT",
            "请只提交一个待拆分的视频，或分别提交各模态文件。",
        )
    parent = prepare_aigc(aigc_obj)
    work = Path(output_dir)
    raw_dir = work / "raw"
    component_dir = work / "components"
    recombined_dir = work / "recombined"
    for folder in (raw_dir, component_dir, recombined_dir):
        folder.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    audio_transcoded = False
    raw: dict[str, Path] = {}
    if composite_video:
        split = split_media(Path(composite_video), raw_dir, tools)
        raw = split["roles"]
        notes.extend(split["notes"])
        audio_transcoded = split["audio_transcoded"]
    else:
        for role, source in (parts or {}).items():
            if role not in ROLE_SUFFIX:
                raise LabelingError("UNSUPPORTED_MEDIA_TYPE", f"不支持的模态角色: {role}")
            path, transcoded = _accept_role_file(role, Path(source), raw_dir, tools)
            raw[role] = path
            audio_transcoded = audio_transcoded or transcoded
    if len(raw) < 2:
        raise LabelingError(
            "NOT_MULTIMODAL",
            "多模态标识至少需要两种模态。当前只得到: " + ("、".join(sorted(raw)) or "无"),
        )
    components = []
    for role in sorted(raw):
        expected = derive_component_aigc(parent, role)
        labeled = component_dir / raw[role].name
        carrier = label_file(role, raw[role], labeled, expected, policy, tools)
        components.append(_component_report(role, labeled, raw[role], expected, tools, carrier))
    kind = recombine_kind(set(raw))
    if kind == "mp4":
        unlabeled = recombined_dir / "unlabeled.mp4"
        composite = recombined_dir / "composite.mp4"
        _mux_mp4(raw, unlabeled, tools)
        carrier = label_file("video", unlabeled, composite, parent, "reject", tools)
        recombined = verify_composite_file(
            composite, "mp4", parent, tools, roles=sorted(raw))
        recombined["file_name"] = composite.name
        recombined["carrier"] = carrier
    else:
        composite = recombined_dir / "package.zip"
        _write_package(raw, composite, parent)
        recombined = verify_composite_file(composite, "package", parent, tools, roles=sorted(raw))
        recombined["file_name"] = composite.name
        recombined["carrier"] = "package-aigc-json-v1"
    passed = all(item["passed"] for item in components) and recombined["passed"]
    report = {
        "standard": "GB45438-2025",
        "operation": "multimodal_label",
        "passed": passed,
        "combination": "+".join(sorted(raw)),
        "recombined_kind": kind,
        "audio_transcoded": audio_transcoded,
        "notes": notes,
        "parent_produce_id": parent["ProduceID"],
        "components": components,
        "recombined": recombined,
        "compliance_unit": "per_content_file",
        "bundle_file": "bundle.zip",
    }
    (work / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bundle = work / "bundle.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in work.rglob("*"):
            if not path.is_file() or path == bundle or path.suffix == ".sqlite3":
                continue
            if "aigc_identifiers" in path.name:
                continue
            relative = path.relative_to(work).as_posix()
            if relative.startswith("raw/"):
                continue
            if relative.endswith("unlabeled.mp4") or relative.endswith("subtitle-clean.vtt"):
                continue
            archive.write(path, relative)
    if not passed:
        raise LabelingError("VERIFY_FAILED", "多模态标识写入后未通过校验。", report["components"])
    return report


def _materialize_bundle(path: Path, dest: Path) -> Path | None:
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "components/" not in "".join(names) and not any(name.startswith("components/") for name in names):
            if AIGC_JSON in names:
                return path
            return None
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        root = dest.resolve()
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            if target != root and root not in target.parents:
                raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "包路径越界。")
        archive.extractall(dest)
    return dest


def parse_multimodal_path(path: str | Path, tools: ToolPaths | None = None) -> dict:
    tools = tools or ToolPaths()
    source = Path(path)
    bundle_dir = _materialize_bundle(source, source.with_name(source.stem + ".parsed"))
    if bundle_dir is not None and (bundle_dir / "components").is_dir():
        files = []
        for role_file in sorted((bundle_dir / "components").iterdir()):
            role = _role_from_name(role_file.name)
            if role is None:
                continue
            records = parse_file(role, role_file, tools)
            files.append({
                "role": role,
                "file_name": role_file.name,
                "count": len(records),
                "labels": [item["aigc"] for item in records if item.get("aigc")],
            })
        recombined = list((bundle_dir / "recombined").glob("*")) if (bundle_dir / "recombined").is_dir() else []
        recombined_labels = []
        for item in recombined:
            if item.name.startswith("unlabeled") or item.suffix == ".vtt":
                continue
            kind = "package" if item.suffix == ".zip" else "mp4"
            verified = verify_composite_file(item, kind, None, tools)
            recombined_labels.append({
                "file_name": item.name,
                "count": len(verified["implicit_labels"]),
                "labels": verified["implicit_labels"],
            })
        return {"files": files, "recombined": recombined_labels}
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            if AIGC_JSON in archive.namelist():
                parsed = aigc.parse_aigc(archive.read(AIGC_JSON).decode("utf-8"))
                return {"files": [], "recombined": [{
                    "file_name": source.name, "count": 1 if parsed else 0,
                    "labels": [parsed] if parsed else [],
                }]}
    role = _role_from_name(source.name) or _role_from_header(source)
    if role is None:
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "无法判断文件模态。")
    records = parse_file(role, source, tools)
    return {"files": [{
        "role": role,
        "file_name": source.name,
        "count": len(records),
        "labels": [item["aigc"] for item in records if item.get("aigc")],
    }], "recombined": []}


def verify_multimodal_path(path: str | Path, tools: ToolPaths | None = None,
                           expected_aigc: dict | None = None) -> dict:
    tools = tools or ToolPaths()
    source = Path(path)
    bundle_dir = _materialize_bundle(source, source.with_name(source.stem + ".verified"))
    if bundle_dir is not None and (bundle_dir / "components").is_dir():
        components = []
        for role_file in sorted((bundle_dir / "components").iterdir()):
            role = _role_from_name(role_file.name)
            if role is None:
                continue
            components.append(verify_implicit(role, role_file, None, tools))
        recombined_reports = []
        folder = bundle_dir / "recombined"
        if folder.is_dir():
            for item in sorted(folder.iterdir()):
                if item.name.startswith("unlabeled") or item.name.endswith(".vtt"):
                    continue
                kind = "package" if item.suffix == ".zip" else "mp4"
                roles = [entry["role"] for entry in components]
                recombined_reports.append(
                    verify_composite_file(item, kind, expected_aigc, tools, roles=roles))
        passed = all(item["passed"] for item in components) and all(
            item["passed"] for item in recombined_reports)
        return {
            "standard": "GB45438-2025",
            "operation": "multimodal_verify",
            "passed": passed,
            "components": components,
            "recombined": recombined_reports[0] if len(recombined_reports) == 1 else recombined_reports,
        }
    if zipfile.is_zipfile(source) and _zip_has(source, AIGC_JSON):
        report = verify_composite_file(source, "package", expected_aigc, tools)
        report["operation"] = "multimodal_verify"
        report["standard"] = "GB45438-2025"
        return report
    role = _role_from_name(source.name) or _role_from_header(source)
    if role is None:
        raise LabelingError("UNSUPPORTED_MEDIA_TYPE", "无法判断文件模态。")
    verified = verify_implicit(role, source, expected_aigc, tools)
    verified["operation"] = "multimodal_verify"
    verified["standard"] = "GB45438-2025"
    return verified


def _zip_has(path: Path, name: str) -> bool:
    with zipfile.ZipFile(path) as archive:
        return name in archive.namelist()


def _role_from_name(name: str) -> str | None:
    lowered = name.lower()
    stem = Path(lowered).stem
    suffix = Path(lowered).suffix
    if stem in {"video", "audio", "image", "text", "subtitle", "embedded"}:
        return {"subtitle": "text", "embedded": "text"}.get(stem, stem)
    if suffix in _TEXT_SUFFIXES:
        return "text"
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in {".m4a", ".wav", ".mp3"}:
        return "audio"
    if suffix in {".mp4", ".mov"} and stem.startswith("composite"):
        return "video"
    if suffix in {".mp4", ".mov"}:
        return "video"
    return None


def _role_from_header(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return None
    if head[:3] == b"\xff\xd8\xff":
        return "image"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video"
    if head.startswith(b"RIFF"):
        return "audio"
    return "text" if b"\x00" not in head[:8] else None
