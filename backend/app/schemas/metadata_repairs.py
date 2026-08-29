from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.metadata.repair_planner import TrustedRepairInput


class RepairPlanOptions(BaseModel):
    """创建修复计划时可选的、由用户明确提供的可信信息。"""

    model_config = ConfigDict(extra="forbid")

    trusted_input: Optional[TrustedRepairInput] = None


class RepairConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=120)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]
    operator_label: str = Field(min_length=1, max_length=200)


class RepairPlanResponse(BaseModel):
    request_id: str
    plan_id: str
    job_id: Optional[str] = None
    status: str
    created_at: str
    expires_at: str
    input: dict[str, Any]
    inspection: dict[str, Any]
    repair_plan: dict[str, Any]
    plan_hash: str
    confirmation: dict[str, Any]
    links: dict[str, Optional[str]]


class RepairJobAcceptedResponse(BaseModel):
    request_id: str
    job_id: str
    plan_id: str
    status: str
    stage: str
    progress: Optional[int] = None
    created_at: str
    links: dict[str, str]


class RepairJobStatusResponse(BaseModel):
    request_id: str
    job_id: str
    plan_id: str
    status: str
    stage: str
    progress: Optional[int] = None
    created_at: str
    updated_at: str
    input: dict[str, Any]
    confirmation: dict[str, Any]
    output: Optional[dict[str, Any]] = None
    validation: Optional[dict[str, Any]] = None
    error: Optional[dict[str, Any]] = None
    links: dict[str, Optional[str]]
