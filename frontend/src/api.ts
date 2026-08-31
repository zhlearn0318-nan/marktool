import type {
  DetectResponse,
  IdentifierResponse,
  MetadataLabelJob,
  RepairJob,
  RepairPlan,
} from "./types";

interface ApiErrorBody {
  detail?: string;
  error?: {
    code?: string;
    message?: string;
    existing_metadata?: Record<string, unknown>;
  };
}

export class ApiError extends Error {
  status: number;
  code?: string;
  body?: ApiErrorBody;

  constructor(message: string, status: number, body?: ApiErrorBody) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error?.code;
    this.body = body;
  }
}

async function expectJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(
      body.error?.message || body.detail || `请求失败（HTTP ${res.status}）`,
      res.status,
      body
    );
  }
  return res.json() as Promise<T>;
}

export async function detectImage(
  file: File,
  targetRegulation = "CN_GB45438"
): Promise<DetectResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("target_regulation", targetRegulation);
  const res = await fetch("/api/detect", { method: "POST", body: form });
  return expectJson<DetectResponse>(res);
}

export async function generateProduceId(): Promise<IdentifierResponse> {
  return expectJson<IdentifierResponse>(
    await fetch("/api/v1/metadata-label-identifiers", { method: "POST" })
  );
}

export async function createMetadataLabelJob(
  file: File,
  input: {
    label: "1" | "2" | "3";
    producer: string;
    produceId: string;
    existingPolicy: "reject" | "replace";
  }
): Promise<MetadataLabelJob> {
  const form = new FormData();
  form.append("file", file);
  form.append(
    "request",
    JSON.stringify({
      standard: "GB45438-2025",
      modality: "image",
      existing_metadata_policy: input.existingPolicy,
      AIGC: {
        Label: input.label,
        ContentProducer: input.producer,
        ProduceID: input.produceId,
        ReservedCode1: "",
        ContentPropagator: input.producer,
        PropagateID: input.produceId,
        ReservedCode2: "",
      },
    })
  );
  return expectJson<MetadataLabelJob>(
    await fetch("/api/v1/metadata-label-jobs", { method: "POST", body: form })
  );
}

export async function getMetadataLabelJob(jobId: string): Promise<MetadataLabelJob> {
  return expectJson<MetadataLabelJob>(
    await fetch(`/api/v1/metadata-label-jobs/${encodeURIComponent(jobId)}`)
  );
}

export async function downloadMetadataLabelOutput(jobId: string): Promise<Response> {
  const res = await fetch(
    `/api/v1/metadata-label-jobs/${encodeURIComponent(jobId)}/output`
  );
  if (!res.ok) await expectJson<never>(res);
  return res;
}

export async function createRepairPlan(file: File): Promise<RepairPlan> {
  const form = new FormData();
  form.append("file", file);
  return expectJson<RepairPlan>(
    await fetch("/api/v1/metadata-repair-plans", { method: "POST", body: form })
  );
}

export async function createRepairJob(
  plan: RepairPlan,
  operatorLabel: string
): Promise<RepairJob> {
  return expectJson<RepairJob>(
    await fetch("/api/v1/metadata-repair-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        plan_id: plan.plan_id,
        plan_hash: plan.plan_hash,
        confirmed: true,
        operator_label: operatorLabel,
      }),
    })
  );
}

export async function getRepairJob(jobId: string): Promise<RepairJob> {
  return expectJson<RepairJob>(
    await fetch(`/api/v1/metadata-repair-jobs/${encodeURIComponent(jobId)}`)
  );
}

export async function downloadRepairOutput(jobId: string): Promise<Response> {
  const res = await fetch(
    `/api/v1/metadata-repair-jobs/${encodeURIComponent(jobId)}/output`
  );
  if (!res.ok) await expectJson<never>(res);
  return res;
}
