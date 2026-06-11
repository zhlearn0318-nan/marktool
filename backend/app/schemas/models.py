from enum import Enum
from typing import Optional
from pydantic import BaseModel


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class MarkType(str, Enum):
    IMPLICIT_METADATA = "implicit_metadata"
    IMPLICIT_WATERMARK = "implicit_watermark"
    EXPLICIT_TEXT = "explicit_text"


class CheckItem(BaseModel):
    id: str
    title: str
    status: CheckStatus
    detail: str
    suggestion: Optional[str] = None
    mark_type: MarkType


class ProvenanceNode(BaseModel):
    role: str
    name: str
    id: Optional[str] = None


class ComplianceReport(BaseModel):
    items: list[CheckItem]
    rating: str
    suggestions: list[str]


class TamperReport(BaseModel):
    consistent: Optional[bool]
    findings: list[str]


class AIDetectionReport(BaseModel):
    enabled: bool = False
    probability: Optional[float] = None
    note: str


class Report(BaseModel):
    provenance: list[ProvenanceNode]
    compliance: ComplianceReport
    tamper: TamperReport
    ai_detection: AIDetectionReport


class DetectionResult(BaseModel):
    result_id: str
    filename: str
    modality: str
    items: list[CheckItem]
    aigc_metadata: Optional[dict] = None
