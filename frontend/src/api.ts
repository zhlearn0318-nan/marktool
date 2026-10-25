import type {
  ComplianceReport,
  DetectResponse,
  HealthResponse,
  LabelJobRequest,
  LabelJobResponse,
  RepairJobResponse,
  RepairPlanOptions,
  RepairPlanResponse,
  SceneReport,
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

/** 按文件推导 modality（图片 → image，MP4 → video，音频 → audio，文档 → text）。
 *
 *  只是给后端的声明，不代替后端校验：后端按文件头复核，不符返回 422。
 *  认不出扩展名时按 .mp4 之外的都当 image 更危险，故回落到 video。
 *
 *  文档类（Markdown / PDF，以及 HTML / DOCX）**统一用 text**——它们是同一类
 *  东西，不该为四个格式各写一套逻辑；后端也用同一个值（app/core/jobs.py
 *  MODALITY_TEXT）。 */
export function modalityForFile(file: File): "image" | "video" | "audio" | "text" {
  const ext = file.name.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] ?? "";
  if (["jpg", "jpeg", "png"].includes(ext)) return "image";
  if (file.type.startsWith("image/")) return "image";
  if (["m4a", "mp3"].includes(ext) || file.type.startsWith("audio/")) return "audio";
  if (["md", "markdown", "pdf", "html", "htm", "docx"].includes(ext)) return "text";
  return "video";
}

/** 合规检测（POST /api/v1/compliance-inspect，只读，同步返回报告）。
 *  支持 JPEG / PNG / MP4 与文档（Markdown / PDF / HTML / DOCX），各格式返回同构报告。 */
export async function inspectMedia(file: File): Promise<ComplianceReport> {
  const form = new FormData();
  form.append("file", file);
  form.append("profile", "auto");
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

export async function createCompositeRepairPlan(file: File): Promise<import("./types").CompositeRepairPlan> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/v1/composite-repair-plans", { method: "POST", body: form });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.error?.message || "无法生成修复方案");
  return res.json();
}

export async function confirmCompositeRepair(planId: string, planHash: string): Promise<{
  validation?: { post_repair_conclusion?: string };
  links: { output: string };
}> {
  const res = await fetch("/api/v1/composite-repair-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_id: planId, plan_hash: planHash, confirmed: true, operator_label: "合规检测页" }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.error?.message || "修复执行失败");
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

/* ===== 修复工作台（/api/v1/metadata-repair-*） ===== */

/** 统一解出后端错误信封 {error:{code,message,details}}。
 *  修复工作台的失败**几乎都是业务性拒绝**（计划冲突 / 不可执行 / 来源不可信），
 *  只显示 HTTP 状态码会让人以为是服务器坏了，必须把 code 带出来。 */
async function repairError(res: Response, fallback: string): Promise<Error> {
  const body = await res.json().catch(() => null);
  const err = body?.error ?? body?.detail ?? null;
  const code = err?.code ? `${err.code}: ` : "";
  const message = err?.message || (typeof err === "string" ? err : "") || fallback;
  const details: { field: string; reason: string }[] = err?.details ?? [];
  const tail = details.length
    ? `（${details.map((d) => `${d.field} ${d.reason}`).join("；")}）`
    : "";
  return new Error(`${code}${message}${tail}（HTTP ${res.status}）`);
}

/** 第一步：上传图片，生成修复计划（POST /api/v1/metadata-repair-plans）。
 *  只生成计划、不落任何改动；有可信来源时经 options.trusted_input 传入。 */
export async function createRepairPlan(
  file: File,
  options?: RepairPlanOptions
): Promise<RepairPlanResponse> {
  const form = new FormData();
  form.append("file", file);
  if (options?.trusted_input) form.append("request", JSON.stringify(options));
  const res = await fetch("/api/v1/metadata-repair-plans", { method: "POST", body: form });
  if (!res.ok) throw await repairError(res, "生成修复计划失败");
  return res.json();
}

/** 重新拉取计划（GET /api/v1/metadata-repair-plans/{plan_id}） */
export async function getRepairPlan(planId: string): Promise<RepairPlanResponse> {
  const res = await fetch(`/api/v1/metadata-repair-plans/${planId}`);
  if (!res.ok) throw await repairError(res, "查询修复计划失败");
  return res.json();
}

/** 第二步：显式确认后才会真正执行（POST /api/v1/metadata-repair-jobs）。
 *  plan_hash 必须与计划页展示的那一份一致，否则后端按 409 计划冲突拒绝 ——
 *  这是防止「看到的计划」与「执行的计划」不是同一份的护栏，不要在前端改写它。 */
export async function confirmRepairPlan(
  planId: string,
  planHash: string,
  operatorLabel: string
): Promise<RepairJobResponse> {
  const res = await fetch("/api/v1/metadata-repair-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plan_id: planId,
      plan_hash: planHash,
      confirmed: true,
      operator_label: operatorLabel,
    }),
  });
  if (!res.ok) throw await repairError(res, "确认修复失败");
  return res.json();
}

/** 第三步：轮询修复任务（GET /api/v1/metadata-repair-jobs/{job_id}） */
export async function getRepairJob(jobId: string): Promise<RepairJobResponse> {
  const res = await fetch(`/api/v1/metadata-repair-jobs/${jobId}`);
  if (!res.ok) throw await repairError(res, "查询修复任务失败");
  return res.json();
}

/* ===== 多模态与场景（同一 labeling-backend，/api/v1） ===== */

async function compositeError(res: Response, fallback: string): Promise<Error> {
  const body = await res.json().catch(() => null);
  const err = body?.error ?? null;
  const code = err?.code ? `${err.code}: ` : "";
  const message = err?.message || fallback;
  return new Error(`${code}${message}（HTTP ${res.status}）`);
}

function aigcPayload(producer: string, label: string) {
  const produceId = crypto.randomUUID().toUpperCase();
  return {
    Label: label,
    ContentProducer: producer,
    ProduceID: produceId,
    ReservedCode1: "",
    ContentPropagator: producer,
    PropagateID: produceId,
    ReservedCode2: "",
  };
}

/** 上传一段视频，拆成画面、音频、字幕后分别打标，再校验重组文件。 */
export async function labelMultimodal(file: File, producer: string, label: string): Promise<SceneReport> {
  const form = new FormData();
  form.append("file", file);
  form.append(
    "request",
    JSON.stringify({
      standard: "GB45438-2025",
      existing_metadata_policy: "reject",
      AIGC: aigcPayload(producer, label),
    })
  );
  const res = await fetch("/api/v1/multimodal/label", { method: "POST", body: form });
  if (!res.ok) throw await compositeError(res, "多模态打标失败");
  return res.json();
}

export async function verifyMultimodal(file: File): Promise<SceneReport> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/v1/multimodal/verify", { method: "POST", body: form });
  if (!res.ok) throw await compositeError(res, "多模态校验失败");
  return res.json();
}

export async function labelVirtualScene(options: {
  producer: string;
  label: string;
  text: string;
  position: string;
  duringService: boolean;
  file?: File | null;
}): Promise<SceneReport> {
  const form = new FormData();
  if (options.file) form.append("file", options.file);
  form.append(
    "request",
    JSON.stringify({
      standard: "GB45438-2025",
      AIGC: aigcPayload(options.producer, options.label),
      explicit: {
        text: options.text,
        position: options.position,
        during_service: options.duringService,
        color: "#FFFFFF",
        background: "#102033",
      },
    })
  );
  const res = await fetch("/api/v1/scenes/virtual/label", { method: "POST", body: form });
  if (!res.ok) throw await compositeError(res, "虚拟场景打标失败");
  return res.json();
}

export async function labelInteractiveScene(options: {
  producer: string;
  label: string;
  text: string;
  placements: string[];
  color: string;
  background: string;
  content?: File | null;
  contentRole?: string;
}): Promise<SceneReport> {
  const form = new FormData();
  if (options.content) form.append("file", options.content);
  form.append(
    "request",
    JSON.stringify({
      standard: "GB45438-2025",
      content_role: options.content ? options.contentRole : undefined,
      AIGC: options.content ? aigcPayload(options.producer, options.label) : undefined,
      explicit: {
        text: options.text,
        placements: options.placements,
        persistent: true,
        color: options.color,
        background: options.background,
      },
    })
  );
  const res = await fetch("/api/v1/scenes/interactive/label", { method: "POST", body: form });
  if (!res.ok) throw await compositeError(res, "交互场景打标失败");
  return res.json();
}

export async function downloadComposite(url: string): Promise<Blob> {
  const res = await fetch(url);
  if (!res.ok) throw new Error("下载结果包失败");
  return res.blob();
}

/** 第四步：下载修复结果文件（GET /api/v1/metadata-repair-jobs/{job_id}/output） */
export async function downloadRepairOutput(jobId: string): Promise<Blob> {
  const res = await fetch(`/api/v1/metadata-repair-jobs/${jobId}/output`);
  if (!res.ok) throw await repairError(res, "下载修复结果失败");
  return res.blob();
}
