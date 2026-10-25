"""文本隐式标识与显式要素规则。"""
from __future__ import annotations

import pytest

from app.labeling_error import LabelingError
from app.modalities.dispatch import verify_implicit
from app.modalities.text_label import parse_text_bytes, text_records, write_text_label
from app.scenes.explicit import (
    assert_interactive_explicit, assert_virtual_explicit, contrast_ratio, explicit_element_errors,
)
from app.tools import ToolPaths
from common import VALID_AIGC


def test_text_roundtrip_keeps_visible_body(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("正文不应被改写\n", encoding="utf-8")
    dest = tmp_path / "note-labeled.txt"
    write_text_label(source, dest, VALID_AIGC)
    visible, raws = parse_text_bytes(dest.read_bytes())
    assert visible.decode("utf-8") == "正文不应被改写\n"
    assert len(raws) == 1
    report = verify_implicit("text", dest, VALID_AIGC, ToolPaths(), integrity_src=source)
    assert report["passed"], report["checks"]
    assert report["labels"][0]["Label"] == "1"


def test_text_reject_existing_and_replace(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("hello", encoding="utf-8")
    once = tmp_path / "once.txt"
    write_text_label(source, once, VALID_AIGC)
    with pytest.raises(LabelingError) as raised:
        write_text_label(once, tmp_path / "again.txt", VALID_AIGC, policy="reject")
    assert raised.value.code == "AIGC_METADATA_EXISTS"
    replaced = tmp_path / "replaced.txt"
    other = dict(VALID_AIGC)
    other["Label"] = "2"
    write_text_label(once, replaced, other, policy="replace")
    records = text_records(replaced)
    assert len(records) == 1
    assert records[0]["aigc"]["Label"] == "2"
    assert parse_text_bytes(replaced.read_bytes())[0] == b"hello"


def test_two_trailers_fail_single_copy_rule(tmp_path):
    from app.modalities.text_label import MARKER
    from app.core import aigc
    body = b"hello" + MARKER + aigc.serialize_aigc(VALID_AIGC).encode("ascii") + b"\n"
    body += MARKER + aigc.serialize_aigc(VALID_AIGC).encode("ascii") + b"\n"
    path = tmp_path / "double.txt"
    path.write_bytes(body)
    report = verify_implicit("text", path, None, ToolPaths())
    assert report["passed"] is False
    assert report["count"] == 2


def test_explicit_elements_follow_clause_5():
    assert explicit_element_errors("AI生成") == []
    assert explicit_element_errors("人工智能生成合成") == []
    assert explicit_element_errors("人工智能")
    assert explicit_element_errors("生成内容")
    assert_virtual_explicit("人工智能生成合成", "bottom_right", "#FFFFFF", "#102033")
    with pytest.raises(LabelingError):
        assert_virtual_explicit("人工智能生成合成", "center", "#FFFFFF", "#102033")
    with pytest.raises(LabelingError):
        assert_virtual_explicit("人工智能生成合成", "bottom_right", "#FFFFFF", "#FFFFFF")
    assert contrast_ratio("#102033", "#F4EBD4") >= 3
    assert_interactive_explicit("AI生成", ["near_content", "interface_bottom"], True, "#102033", "#F4EBD4")
    with pytest.raises(LabelingError):
        assert_interactive_explicit("AI生成", ["near_content"], False, "#102033", "#F4EBD4")
    with pytest.raises(LabelingError):
        assert_interactive_explicit("AI生成", ["watermark"], True, "#102033", "#F4EBD4")
