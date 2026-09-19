import type {
  ComplianceReport,
  DetectResponse,
  HealthResponse,
  LabelJobRequest,
  LabelJobResponse,
} from "./types";

export async function detectImage(
  file: File,
  targetRegulation = "CN_GB45438"
): Promise<DetectResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("target_regulation", targetRegulation);
  const res = await fetch("/api/detect", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(err.detail || "检测失败");
  }
  return res.json();
}

/** 按文件推导 modality（JPEG/PNG → image，其余 → video）。
 *
 *  只是给后端的声明，不代替后端校验：后端按文件头复核，不符返回 422。
 *  认不出扩展名时按 .mp4 之外的都当 image 更危险，故回落到 video。 */
export function modalityForFile(file: File): "image" | "video" {
  const ext = file.name.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] ?? "";
  if (["jpg", "jpeg", "png"].includes(ext)) return "image";
  if (file.type.startsWith("image/")) return "image";
  return "video";
}

/** 合规检测（POST /api/v1/compliance-inspect，只读，同步返回报告）。
 *  支持 JPEG / PNG / MP4，三种格式返回同构报告。 */
export async function inspectMedia(file: File): Promise<ComplianceReport> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/v1/compliance-inspect", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    const msg =
      (err && err.error && err.error.message) ||
      (err && err.detail) ||
      `合规检测失败（HTTP ${res.status}）`;
    throw new Error(msg);
  }
  return res.json();
}

/** 打标服务健康检查 + 能力列表（/api/v1/health） */
export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/v1/health");
  if (!res.ok) throw new Error("打标服务不可用");
  return res.json();
}

/** 创建打标任务（POST /api/v1/metadata-label-jobs），JPEG / PNG / MP4 共用 */
export async function createLabelJob(
  file: File,
  request: LabelJobRequest
): Promise<LabelJobResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("request", JSON.stringify(request));
  const res = await fetch("/api/v1/metadata-label-jobs", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    const msg =
      (err && err.error && err.error.message) ||
      (err && err.detail) ||
      `创建任务失败（HTTP ${res.status}）`;
    throw new Error(msg);
  }
  return res.json();
}

/** 查询任务状态（GET /api/v1/metadata-label-jobs/{job_id}） */
export async function getLabelJob(jobId: string): Promise<LabelJobResponse> {
  const res = await fetch(`/api/v1/metadata-label-jobs/${jobId}`);
  if (!res.ok) throw new Error("查询任务失败");
  return res.json();
}

/** 下载打标结果文件（GET /api/v1/metadata-label-jobs/{job_id}/output） */
export async function downloadLabelJob(jobId: string): Promise<Blob> {
  const res = await fetch(`/api/v1/metadata-label-jobs/${jobId}/output`);
  if (!res.ok) throw new Error("下载结果文件失败");
  return res.blob();
}
