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
