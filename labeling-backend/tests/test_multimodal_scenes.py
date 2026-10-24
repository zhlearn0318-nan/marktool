"""多模态拆分/重组，以及虚拟场景、交互场景的端到端用例。"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from app.adapters.video import Mp4Adapter
from app.core import aigc
from app.labeling_error import LabelingError
from app.modalities.audio_label import AudioAdapter
from app.multimodal.service import (
    label_multimodal, parse_multimodal_path, verify_multimodal_path,
)
from app.scenes.interactive_scene import label_interactive_scene, verify_interactive_scene
from app.scenes.virtual_scene import label_virtual_scene, parse_virtual_scene, verify_virtual_scene
from app.tools import ToolPaths
from common import EXIFTOOL_CONFIG, VALID_AIGC, make_png
from media_samples import make_av_subtitle_video, make_silent_video, make_tone

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("exiftool") is None,
                                 reason="需要 ffmpeg 与 exiftool")


def _tools(tmp_path: Path) -> ToolPaths:
    return ToolPaths(
        exiftool="exiftool", ffprobe="ffprobe", ffmpeg="ffmpeg",
        exiftool_config=str(EXIFTOOL_CONFIG),
        registry_path=str(tmp_path / "ids.sqlite3"),
    )


def _aigc(produce_id: str, label: str = "1") -> dict:
    return {
        "Label": label,
        "ContentProducer": "ORG_1565201000000016",
        "ProduceID": produce_id,
        "ReservedCode1": "",
        "ContentPropagator": "ORG_1565201000000016",
        "PropagateID": produce_id,
        "ReservedCode2": "",
    }


def test_audio_m4a_roundtrip(tmp_path):
    source = make_tone(tmp_path)
    dest = tmp_path / "labeled.m4a"
    adapter = AudioAdapter(exiftool="exiftool", ffprobe="ffprobe", ffmpeg="ffmpeg",
                           exiftool_config=str(EXIFTOOL_CONFIG))
    adapter.write_metadata(source, dest, VALID_AIGC)
    records = adapter.detect_existing(dest)
    assert len(records) == 1
    assert records[0].aigc == VALID_AIGC
    media = adapter.media_integrity_check(source, dest)
    assert media.passed, media.reason


def test_video_with_audio_and_subtitles_roundtrip(tmp_path):
    source = make_av_subtitle_video(tmp_path)
    tools = _tools(tmp_path)
    report = label_multimodal(
        tmp_path / "out", _aigc("PID-MM-AVT-1"), tools=tools, composite_video=source,
    )
    assert report["passed"] is True
    assert report["combination"] == "audio+text+video"
    assert report["recombined_kind"] == "mp4"
    by_role = {item["role"]: item for item in report["components"]}
    assert set(by_role) == {"audio", "text", "video"}
    assert by_role["video"]["labels"][0]["ProduceID"] == "PID-MM-AVT-1-V"
    assert by_role["audio"]["labels"][0]["ProduceID"] == "PID-MM-AVT-1-A"
    assert by_role["text"]["labels"][0]["ProduceID"] == "PID-MM-AVT-1-T"
    assert report["recombined"]["implicit_label"]["ProduceID"] == "PID-MM-AVT-1"
    composite = tmp_path / "out" / "recombined" / "composite.mp4"
    parsed = parse_multimodal_path(composite, tools)
    assert parsed["files"][0]["count"] == 1
    verified = verify_multimodal_path(tmp_path / "out" / "bundle.zip", tools)
    assert verified["passed"] is True
    # 字幕轨里不应再出现隐式标识分隔符（第 6.1.c 条：重组文件只留容器上那一份）。
    import subprocess
    extracted = tmp_path / "back.vtt"
    subprocess.check_call([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(composite), "-map", "0:s:0", "-f", "webvtt", str(extracted),
    ])
    text = extracted.read_text(encoding="utf-8")
    assert "GB45438-AIGC" not in text
    assert "合成字幕示例" in text

    raw = aigc.serialize_aigc(_aigc("PID-MM-AVT-1"))
    Mp4Adapter(exiftool="exiftool", exiftool_config=str(EXIFTOOL_CONFIG))._exiftool_cmd(
        ["-overwrite_original", "-QuickTime:Comment=" + raw], str(composite),
    )
    tainted = verify_multimodal_path(composite, tools)
    assert tainted["passed"] is False


def test_single_video_track_is_not_multimodal(tmp_path):
    source = make_silent_video(tmp_path)
    with pytest.raises(LabelingError) as raised:
        label_multimodal(tmp_path / "out", _aigc("PID-SINGLE"), tools=_tools(tmp_path),
                         composite_video=source)
    assert raised.value.code == "NOT_MULTIMODAL"


def test_text_image_package_has_one_aigc_json(tmp_path):
    image = make_png(tmp_path / "still.png", size=(80, 60))
    text = tmp_path / "caption.txt"
    text.write_text("配图说明", encoding="utf-8")
    tools = _tools(tmp_path)
    report = label_multimodal(
        tmp_path / "out", _aigc("PID-TI-1"), tools=tools,
        parts={"text": text, "image": image},
    )
    assert report["passed"] is True
    assert report["recombined_kind"] == "package"
    package = tmp_path / "out" / "recombined" / "package.zip"
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        assert names.count("AIGC.json") == 1
        parsed = aigc.parse_aigc(archive.read("AIGC.json").decode("utf-8"))
        assert parsed["ProduceID"] == "PID-TI-1"
        payload = archive.read("payload/image.png")
        assert b"ContentProducer" not in payload
        _visible, raws = __import__(
            "app.modalities.text_label", fromlist=["parse_text_bytes"]
        ).parse_text_bytes(archive.read("payload/text.txt"))
        assert raws == []
    labeled_text = (tmp_path / "out" / "components" / "text.txt").read_bytes()
    assert b"GB45438-AIGC" in labeled_text


def test_audio_text_and_image_audio_packages(tmp_path):
    tone = make_tone(tmp_path)
    text = tmp_path / "lyric.txt"
    text.write_text("歌词一行", encoding="utf-8")
    tools = _tools(tmp_path)
    audio_text = label_multimodal(
        tmp_path / "at", _aigc("PID-AT-1"), tools=tools,
        parts={"audio": tone, "text": text},
    )
    assert audio_text["recombined_kind"] == "package"
    assert audio_text["passed"] is True
    image = make_png(tmp_path / "cover.png", size=(64, 64))
    image_audio = label_multimodal(
        tmp_path / "ia", _aigc("PID-IA-1"), tools=_tools(tmp_path / "ia-ids"),
        parts={"image": image, "audio": tone},
    )
    assert image_audio["passed"] is True
    assert image_audio["combination"] == "audio+image"
    all_three = label_multimodal(
        tmp_path / "iat", _aigc("PID-IAT-1"), tools=_tools(tmp_path / "iat-ids"),
        parts={"image": image, "audio": tone, "text": text},
    )
    assert all_three["passed"] is True
    assert all_three["recombined_kind"] == "package"
    assert all_three["combination"] == "audio+image+text"


def test_video_and_image_recombine_as_package(tmp_path):
    """含图片时不能塞进单一 MP4，重组为只带一份 AIGC.json 的包。"""
    video = make_silent_video(tmp_path)
    image = make_png(tmp_path / "poster.png", size=(96, 64))
    report = label_multimodal(
        tmp_path / "out", _aigc("PID-VI-1"), tools=_tools(tmp_path),
        parts={"video": video, "image": image},
    )
    assert report["passed"] is True
    assert report["recombined_kind"] == "package"
    assert report["combination"] == "image+video"


def test_virtual_scene_label_parse_verify_and_tamper(tmp_path):
    tools = _tools(tmp_path)
    report = label_virtual_scene(tmp_path / "scene", _aigc("PID-VS-1"), tools=tools)
    assert report["passed"] is True
    clauses = {item["clause"] for item in report["checks"]}
    assert "5.5.b" in clauses
    assert "5.5.f" in clauses
    assert "6.1.c" in clauses
    package = tmp_path / "scene" / "virtual-scene.zip"
    parsed = parse_virtual_scene(package, tools)
    assert parsed["count"] == 1
    assert parsed["implicit_labels"][0]["ProduceID"] == "PID-VS-1"
    assert parsed["explicit_text"] == "人工智能生成合成"

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package) as archive, zipfile.ZipFile(tampered, "w") as output:
        descriptor = json.loads(archive.read("scene.json"))
        box = descriptor["explicit"]["box"]
        from PIL import Image
        frame_path = tmp_path / "frame.png"
        frame_path.write_bytes(archive.read("start_frame.png"))
        with Image.open(frame_path) as image:
            changed = image.convert("RGB")
            pixel = changed.getpixel((box[0] + 1, box[1] + 1))
            changed.putpixel((box[0] + 1, box[1] + 1), (0, 255, 0) if pixel != (0, 255, 0) else (255, 0, 0))
            changed.save(frame_path, format="PNG")
        output.writestr("scene.json", archive.read("scene.json"))
        output.writestr("start_frame.png", frame_path.read_bytes())
    pixel_report = verify_virtual_scene(tampered, tools)
    assert pixel_report["passed"] is False

    text_tamper = tmp_path / "text.zip"
    with zipfile.ZipFile(package) as archive, zipfile.ZipFile(text_tamper, "w") as output:
        descriptor = json.loads(archive.read("scene.json"))
        descriptor["explicit"]["text"] = "普通标题"
        output.writestr("scene.json", json.dumps(descriptor))
        output.writestr("start_frame.png", archive.read("start_frame.png"))
    text_report = verify_virtual_scene(text_tamper, tools)
    assert text_report["passed"] is False
    assert any(item["id"] == "explicit_elements" and not item["passed"] for item in text_report["checks"])


def test_virtual_scene_rejects_noncompliant_explicit_text(tmp_path):
    with pytest.raises(LabelingError) as raised:
        label_virtual_scene(
            tmp_path / "bad", _aigc("PID-VS-BAD"), text="仅供参考", tools=_tools(tmp_path),
        )
    assert raised.value.code == "EXPLICIT_LABEL_INVALID"


def test_interactive_scene_with_and_without_content(tmp_path):
    tools = _tools(tmp_path)
    bare = label_interactive_scene(tmp_path / "ui", tools=tools, text="人工智能生成合成")
    assert bare["passed"] is True
    assert bare["implicit_label"] is None
    assert any(item["clause"] == "5.6.c" and item["passed"] for item in bare["checks"])

    note = tmp_path / "reply.txt"
    note.write_text("这是一轮回复", encoding="utf-8")
    labeled = label_interactive_scene(
        tmp_path / "ui-file", tools=tools, text="AI生成",
        placements=["near_content", "interface_bottom"],
        content=note, content_role="text", aigc_obj=_aigc("PID-IS-1"),
    )
    assert labeled["passed"] is True
    assert labeled["implicit_label"]["ProduceID"] == "PID-IS-1"
    package = tmp_path / "ui-file" / "interactive-scene.zip"
    again = verify_interactive_scene(package, tools)
    assert again["passed"] is True

    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({
        "standard": "GB45438-2025",
        "scene_type": "interactive",
        "explicit": {
            "text": "AI生成",
            "placements": ["near_content"],
            "persistent": False,
            "color": "#102033",
            "background": "#F4EBD4",
        },
    }), encoding="utf-8")
    failed = verify_interactive_scene(broken, tools)
    assert failed["passed"] is False
