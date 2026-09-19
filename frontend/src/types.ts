export type CheckStatus = "pass" | "fail" | "warn";

export interface CheckItem {
  id: string;
  title: string;
  status: CheckStatus;
  detail: string;
  suggestion?: string | null;
  mark_type: string;
}

export interface ProvenanceNode {
  role: string;
  name: string;
  id?: string | null;
}

export interface Report {
  provenance: ProvenanceNode[];
  compliance: { items: CheckItem[]; rating: string; suggestions: string[] };
  tamper: { consistent: boolean | null; findings: string[] };
  ai_detection: { enabled: boolean; probability: number | null; note: string };
}

export interface DetectResponse {
  result_id: string;
  detection: {
    result_id: string;
    filename: string;
    modality: string;
    items: CheckItem[];
    aigc_metadata: Record<string, unknown> | null;
  };
  report: Report;
}

/** 打标任务请求（对应 labeling-backend POST /api/v1/metadata-label-jobs 的 request 字段）
 *  modality 必须与文件真实类型一致（JPEG/PNG → image，MP4 → video），
 *  后端按文件头复核，不符返回 422 MODALITY_MISMATCH。 */
export interface LabelJobRequest {
  standard: string;
  modality: "image" | "video";
  existing_metadata_policy: "reject" | "replace";
  AIGC: {
    Label: string;
    ContentProducer: string;
    ProduceID: string;
    ReservedCode1: string;
    ContentPropagator: string;
    PropagateID: string;
    ReservedCode2: string;
  };
}

export interface LabelJobResponse {
  request_id: string;
  job_id: string;
  status: string;
  stage: string;
  progress: number | null;
  created_at: string;
  updated_at?: string;
  links: { self: string; output: string | null };
  input?: {
    original_file_name: string;
    detected_mime_type: string;
    size_bytes: number;
    sha256: string;
  };
  output?: {
    file_name: string;
    mime_type: string;
    size_bytes: number;
    sha256: string;
    carrier: string;
    download_url: string;
    expires_at: string;
  };
  embedded_metadata?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  error?: { code: string; message: string; retryable: boolean };
}

export interface HealthResponse {
  status: string;
  capabilities: Record<string, boolean>;
}

/* ===== 修复工作台（/api/v1/metadata-repair-*，仅 JPEG/PNG） =====

   流程是「计划 → 显式确认 → 异步执行 → 审计」四步，且计划不可直接执行：
   必须先 POST 计划拿到 plan_id + plan_hash，确认时原样回传这两个值，
   后端据此校验「人看到的那份计划」与「要执行的那份计划」是同一份。 */

export type RepairRepairability = "confirmable" | "manual_review" | "forbidden" | "not_applicable";

/** 可信来源类型。修复**不新增标识**，所以这里全部是「已有标识的来源可被证实」
 *  的途径；没有 derived_from_file —— 那是系统自己从文件推导出来的，不由调用方声明。 */
export type RepairSourceType =
  | "identifier_registry"
  | "audit_history"
  | "verified_provider_record"
  | "authorized_manual";

/** 计划里回带的来源：上面四种，外加系统派生的「原文件记录」。 */
export type RepairPlanSourceType = RepairSourceType | "derived_from_file";

export type RepairWriteContext = "unknown" | "initial_generation" | "propagation";

/** 计划参数的可选部分。
 *
 *  只在「标识存在、但已损坏到无法从文件恢复」时才有意义：它让操作人用一份
 *  权威七字段整体覆盖旧记录，而不是让系统去猜。注意它**救不了两种情形**——
 *  文件里压根没标识（not_found，应走打标流程），或存在 Extended XMP /
 *  C2PA 未验签 / 交叉读取失败等阻塞项。这两种情况下后端在读到可信来源之前
 *  就已判定不可执行，前端不要承诺能靠填表绕过。 */
export interface RepairPlanOptions {
  trusted_input?: {
    AIGC: LabelJobRequest["AIGC"];
    source_type: RepairSourceType;
    /** 来源凭证，1–500 字符。审计要凭它回溯，必填。 */
    source_reference: string;
    write_context: RepairWriteContext;
  };
}

export interface RepairFieldChange {
  field: string;
  before: unknown;
  after: unknown;
  reason: string;
}

export interface RepairPlanDraft {
  repairability: RepairRepairability;
  executable: boolean;
  source_type?: RepairPlanSourceType | null;
  source_reference?: string | null;
  write_context: RepairWriteContext;
  proposed_document?: { AIGC: Record<string, unknown> } | null;
  actions: string[];
  field_changes: RepairFieldChange[];
  /** 阻塞项：为真时 executable 必为 false，界面必须显式列出而不是只灰掉按钮 */
  blocking_reasons: string[];
  warnings: string[];
}

/** 计划里的检测块，字段与队友检测器的 model_dump 一致 */
export interface RepairInspection {
  conclusion: ComplianceConclusion;
  reason_codes: string[];
  repairability: "confirmable" | "manual_review" | "forbidden" | "not_applicable";
  detected_format: string;
  mime_type: string;
  file_sha256: string;
  pixel_sha256: string;
  record_count: number;
  aigc_metadata?: { AIGC?: Record<string, unknown> } | null;
  issues: ComplianceIssue[];
  project_policy: { accepted: boolean; errors: string[] };
  cross_reader: { status: string; detail?: string | null };
  source_verification: { status: string; details: string[] };
  c2pa_presence: { status: string; carrier?: string | null; detail?: string | null };
  extended_xmp: boolean;
}

export interface RepairPlanResponse {
  request_id: string;
  plan_id: string;
  job_id: string | null;
  /** pending / expired / confirmed …（expired 后不可再执行，但原件仍按保留期留存） */
  status: string;
  created_at: string;
  expires_at: string;
  input: {
    original_file_name: string;
    detected_mime_type: string;
    size_bytes: number;
    sha256: string;
    pixel_sha256: string;
    /** 原文件是否还在（过期清理后为 false，此时不能再确认执行） */
    available: boolean;
    expires_at: string | null;
  };
  inspection: RepairInspection;
  repair_plan: RepairPlanDraft;
  plan_hash: string;
  confirmation: {
    required: boolean;
    identity_verified: boolean;
    method: string | null;
    operator_label: string | null;
  };
  links: { self: string; create_job: string; job: string | null };
}

export interface RepairJobResponse {
  request_id: string;
  job_id: string;
  plan_id: string;
  status: string;
  stage: string;
  progress: number | null;
  created_at: string;
  updated_at?: string;
  input: { original_file_name: string | null; sha256: string | null };
  confirmation: {
    method: string | null;
    operator_label: string | null;
    identity_verified: boolean;
  };
  output?: {
    file_name: string;
    mime_type: string;
    size_bytes: number;
    sha256: string;
    available: boolean;
    download_url: string | null;
    expires_at: string;
  } | null;
  /** 修复后的复检：post_repair_conclusion 应为 compliant，
   *  pixel_sha256_unchanged 必须为 true（修复只动元数据，不动像素） */
  validation?: {
    post_repair_conclusion?: string;
    pixel_sha256_unchanged?: boolean;
    post_repair_inspection?: Record<string, unknown>;
  } | null;
  error?: { code: string; message: string; retryable: boolean } | null;
  links: { self: string; output: string | null };
}

/* ===== 合规检测（POST /api/v1/compliance-inspect 只读，JPEG/PNG/MP4 同构报告） ===== */

export type ComplianceConclusion =
  | "compliant"          // 检出唯一一份标识且通过国标结构
  | "noncompliant"       // 缺字段/JSON 错误/多份记录等
  | "not_found"          // 未检出隐式标识
  | "indeterminate";     // 载体不可读，无法判定

export type ComplianceSeverity = "error" | "warn" | "info";

export interface ComplianceIssue {
  code: string;
  severity: ComplianceSeverity;
  message: string;
  field?: string | null;
}

export interface ComplianceCandidate {
  tag: string;
  parseable: boolean;
  /** 物理位置，如「JPEG APP1 段（标准 XMP）」「PNG 文本块… 第 2 份（共 2 份）」。
   *  多份记录时靠它定位是哪一份出的问题。 */
  location?: string | null;
  raw_preview?: string;
  parsed_fields?: string[];
}

export interface ComplianceReport {
  request_id: string;
  file_name: string | null;
  detected_mime_type: string;
  size_bytes: number | null;
  sha256: string | null;
  record_count: number;
  candidates: ComplianceCandidate[];
  issues: ComplianceIssue[];
  conclusion: ComplianceConclusion;
  reason_code: string | null;
  repairability: "auto_fixable" | "needs_human" | "forbidden" | null;
  c2pa_presence: "absent" | "present_unverified" | "indeterminate";
  media_status: "ok" | "degraded" | "unreadable" | null;
  confidence: "high" | "low";
  /** MP4 走 ffprobe（streams / duration / decode_smoke），
   *  图片走 Pillow（mode / width / height / pixels_sha256）。共用 media_status。 */
  media: {
    media_status?: string | null;
    format_name?: string | null;
    probe_error?: string | null;
    note?: string | null;
    // MP4
    duration?: string | null;
    streams?: { codec_type: string; codec_name: string; width: number | null; height: number | null }[];
    has_video?: boolean;
    decode_smoke?: string;
    decode_error?: string | null;
    // 图片
    mode?: string;
    width?: number;
    height?: number;
    pixels_sha256?: string;
  };
  /** MP4 专有。图片报告同样带这个块，但 `applicable: false` 且各字段为 null，
   *  前端据此整块隐藏。注意 MP4 报告里**没有** applicable 键（视为适用）。 */
  bmff: {
    applicable?: boolean;
    note?: string | null;
    has_ftyp: boolean | null;
    has_moov: boolean | null;
    has_mdat: boolean | null;
    truncated: boolean | null;
    error: string | null;
    c2pa_uuid: string[];
  };
  registry: {
    mode: string;
    reason?: string;
    produce_id?: string;
    known?: boolean;
  };
  detector_version: string;
  exiftool_version: string | null;
  elapsed_ms: number;
}
