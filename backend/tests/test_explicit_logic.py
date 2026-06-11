from app.engine.detectors.explicit_mark_detector import evaluate_explicit_mark


def test_compliant_corner_mark():
    # 1000x1000 图，右下角，字高 80(>50)，含关键词
    r = evaluate_explicit_mark("AI生成", (820, 900, 980, 980), (1000, 1000))
    assert r["compliant"] is True


def test_too_small_height_not_compliant():
    r = evaluate_explicit_mark("AI生成", (820, 950, 980, 980), (1000, 1000))  # 字高30<50
    assert r["height_ok"] is False
    assert r["compliant"] is False


def test_no_keyword_not_compliant():
    r = evaluate_explicit_mark("版权所有", (820, 900, 980, 980), (1000, 1000))
    assert r["has_keyword"] is False
    assert r["compliant"] is False
