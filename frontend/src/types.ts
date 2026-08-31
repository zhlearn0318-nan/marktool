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

export type MetadataConclusion =
  | "not_found"
  | "compliant"
  | "noncompliant"
  | "indeterminate";

export interface MetadataCompliance {
  conclusion: MetadataConclusion;
  reason_codes: string[];
  repairability: string;
  detected_format: string;
  mime_type: string;
  file_sha256: string;
  pixel_sha256: string;
  record_count: number;
  aigc_metadata: Record<string, unknown> | null;
  issues: Array<{
    code: string;
    category: string;
    level: string;
    detail: string;
    standard_basis?: string | null;
  }>;
  project_policy?: {
    accepted: boolean;
    errors: string[];
  };
  cross_reader?: Record<string, unknown>;
  source_verification?: Record<string, unknown>;
  c2pa_presence?: Record<string, unknown>;
  extended_xmp?: boolean;
}

export interface DetectResponse {
  result_id: string;
  detection: {
    result_id: string;
    filename: string;
    modality: string;
    items: CheckItem[];
    aigc_metadata: Record<string, unknown> | null;
    metadata_compliance?: MetadataCompliance | null;
  };
  report: Report;
}

export interface IdentifierResponse {
  request_id: string;
  produce_id: string;
  generated_at: string;
}

export type AsyncJobStatus = "queued" | "running" | "succeeded" | "failed";

export interface JobValidation {
  read_back_succeeded: boolean;
  schema_valid: boolean;
  single_aigc_record: boolean;
  media_integrity_valid: boolean;
  [key: string]: unknown;
}

export interface MetadataLabelJob {
  request_id: string;
  job_id: string;
  status: AsyncJobStatus;
  stage: string;
  progress?: number | null;
  created_at: string;
  updated_at?: string;
  input?: Record<string, unknown>;
  output?: {
    file_name: string;
    mime_type: string;
    size_bytes: number;
    sha256: string;
    carrier: string;
    download_url: string;
    expires_at: string;
  } | null;
  embedded_metadata?: Record<string, unknown> | null;
  validation?: JobValidation | null;
  error?: { code: string; message: string; retryable?: boolean } | null;
  links: { self: string; output?: string | null };
}

export interface RepairPlan {
  request_id: string;
  plan_id: string;
  job_id?: string | null;
  status: string;
  created_at: string;
  expires_at: string;
  input: Record<string, unknown>;
  inspection: MetadataCompliance & Record<string, unknown>;
  repair_plan: {
    actions: Array<Record<string, unknown>>;
    blocking_reasons: string[];
    executable: boolean;
    field_changes: Array<Record<string, unknown>>;
    proposed_document: Record<string, unknown> | null;
    repairability: string;
    source_reference: string | null;
    source_type: string | null;
    warnings: string[];
    write_context: string;
  };
  plan_hash: string;
  confirmation: Record<string, unknown>;
  links: Record<string, string | null>;
}

export interface RepairJob {
  request_id: string;
  job_id: string;
  plan_id: string;
  status: AsyncJobStatus;
  stage: string;
  progress?: number | null;
  created_at: string;
  updated_at?: string;
  input?: Record<string, unknown>;
  confirmation?: Record<string, unknown>;
  output?: Record<string, unknown> | null;
  validation?: JobValidation | null;
  error?: { code: string; message: string } | null;
  links: Record<string, string | null>;
}
