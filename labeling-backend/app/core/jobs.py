"""任务状态机与阶段定义（开发手册 §8）。

status 表示面向用户的总体状态（有限集合）；stage 表示内部阶段（可增加，
但已有含义不改变）。二者分开，前端只需对少量状态编写逻辑。
"""
from __future__ import annotations

# ---- status（§8.1）----
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
TERMINAL = frozenset({SUCCEEDED, FAILED})

# ---- stage（§8.1 推荐值）----
STAGE_QUEUED = "queued"
STAGE_VALIDATING_REQUEST = "validating_request"
STAGE_INSPECTING_FILE = "inspecting_file"
STAGE_CHECKING_EXISTING = "checking_existing_metadata"
STAGE_WRITING = "writing_metadata"
STAGE_VERIFYING_METADATA = "verifying_metadata"
STAGE_VERIFYING_MEDIA = "verifying_media"
STAGE_PUBLISHING = "publishing_output"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"

# ---- 已有标识策略（§4.3/§7.2）----
POLICY_REJECT = "reject"
POLICY_REPLACE = "replace"
POLICIES = frozenset({POLICY_REJECT, POLICY_REPLACE})

# ---- 模态（§7.2）----
MODALITY_IMAGE = "image"
MODALITY_VIDEO = "video"

# 标准字段（§7.2 request）
STANDARD = "GB45438-2025"
