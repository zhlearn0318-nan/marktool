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
