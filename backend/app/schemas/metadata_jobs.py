from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class AIGCFields(BaseModel):
    """GB 45438—2025 附录 E 的固定七字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    Label: Literal["1", "2", "3"]
    ContentProducer: str
    ProduceID: str
    ReservedCode1: str
    ContentPropagator: str
    PropagateID: str
    ReservedCode2: str


class MetadataLabelRequest(BaseModel):
    """multipart 中 request 部分的公共接口模型。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    standard: Literal["GB45438-2025"]
    modality: Literal["image", "video"]
    existing_metadata_policy: Literal["reject", "replace"] = "reject"
    AIGC: AIGCFields

    def aigc_document(self) -> dict:
        return {"AIGC": self.AIGC.model_dump()}


class IdentifierResponse(BaseModel):
    request_id: str
    produce_id: str
    generated_at: str


class JobLinks(BaseModel):
    self: str
    output: Optional[str] = None


class JobAcceptedResponse(BaseModel):
    request_id: str
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    stage: str
    progress: Optional[int] = None
    created_at: str
    links: JobLinks


class JobInput(BaseModel):
    original_file_name: str
    detected_mime_type: str
    size_bytes: int
    sha256: str


class JobOutput(BaseModel):
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    carrier: str
    download_url: str
    expires_at: str


class JobValidation(BaseModel):
    read_back_succeeded: bool
    schema_valid: bool
    single_aigc_record: bool
    media_integrity_valid: bool


class JobFailure(BaseModel):
    code: str
    message: str
    retryable: bool


class JobStatusResponse(BaseModel):
    request_id: str
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    stage: str
    progress: Optional[int] = None
    created_at: str
    updated_at: str
    input: JobInput
    output: Optional[JobOutput] = None
    embedded_metadata: Optional[dict[str, Any]] = None
    validation: Optional[JobValidation] = None
    error: Optional[JobFailure] = None
    links: JobLinks


class FieldError(BaseModel):
    field: str
    reason: str


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    field_errors: Optional[list[FieldError]] = None


class ApiErrorResponse(BaseModel):
    request_id: str
    error: ApiErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok"]
    capabilities: dict[str, bool]
