from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from PIL import Image
from app.schemas.models import CheckItem, MarkType


@dataclass
class DetectionContext:
    file_path: str
    image: Optional[Image.Image]
    target_regulation: str = "CN_GB45438"
    cache: dict[str, Any] = field(default_factory=dict)


class Detector(ABC):
    name: str = "base"
    modality: str = "image"
    mark_type: MarkType = MarkType.IMPLICIT_METADATA

    @abstractmethod
    def applicable(self, ctx: DetectionContext) -> bool:
        ...

    @abstractmethod
    def detect(self, ctx: DetectionContext) -> list[CheckItem]:
        ...
