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

/** 视频打标任务请求（对应 labeling-backend POST /api/v1/metadata-label-jobs 的 request 字段） */
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

/* ===== MP4 合规检测（POST /api/v1/compliance-inspect 只读） ===== */

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
  media: {
    media_status?: string | null;
    format_name?: string | null;
    duration?: string | null;
    has_video?: boolean;
    probe_error?: string | null;
    decode_smoke?: string;
    decode_error?: string | null;
    note?: string;
  };
  bmff: {
    has_ftyp: boolean;
    has_moov: boolean;
    has_mdat: boolean;
    truncated: boolean;
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
