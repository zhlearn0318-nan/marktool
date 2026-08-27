"""测试共享常量。"""
from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
CLEAN_MP4 = FIXTURES / "sample_clean.mp4"
LEGACY_MP4 = FIXTURES / "sample_with_legacy_aigc.mp4"
EXIFTOOL_CONFIG = Path(__file__).parent.parent / "config" / "exiftool_aigc.config"

VALID_AIGC = {
    "Label": "1",
    "ContentProducer": "ORG_1565201000000016",
    "ProduceID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode1": "",
    "ContentPropagator": "ORG_1565201000000016",
    "PropagateID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode2": "",
}
