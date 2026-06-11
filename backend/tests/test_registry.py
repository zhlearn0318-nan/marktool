from app.engine.base import Detector, DetectionContext
from app.engine.registry import DetectorRegistry
from app.schemas.models import CheckItem, CheckStatus, MarkType


class _OK(Detector):
    name = "ok"
    modality = "image"
    mark_type = MarkType.IMPLICIT_METADATA

    def applicable(self, ctx):
        return True

    def detect(self, ctx):
        return [CheckItem(id="ok.1", title="ok", status=CheckStatus.PASS,
                          detail="d", mark_type=self.mark_type)]


class _Boom(Detector):
    name = "boom"
    modality = "image"
    mark_type = MarkType.EXPLICIT_TEXT

    def applicable(self, ctx):
        return True

    def detect(self, ctx):
        raise RuntimeError("kaboom")


def test_runs_matching_modality_and_collects_items():
    reg = DetectorRegistry()
    reg.register(_OK())
    ctx = DetectionContext(file_path="x", image=None)
    items = reg.run_all(ctx, modality="image")
    assert [i.id for i in items] == ["ok.1"]


def test_detector_exception_becomes_warn_item():
    reg = DetectorRegistry()
    reg.register(_Boom())
    ctx = DetectionContext(file_path="x", image=None)
    items = reg.run_all(ctx, modality="image")
    assert items[0].status == CheckStatus.WARN
    assert "kaboom" in items[0].detail


def test_skips_other_modality():
    reg = DetectorRegistry()
    d = _OK()
    d.modality = "audio"
    reg.register(d)
    ctx = DetectionContext(file_path="x", image=None)
    assert reg.run_all(ctx, modality="image") == []
