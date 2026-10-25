import { useEffect, useState } from "react";
import { Alert, Collapse, Tag, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import PageBanner from "../components/PageBanner";
import ToolSteps from "../components/ToolSteps";
import {
  confirmCompositeRepair,
  confirmExplicitPreview,
  confirmRepairPlan,
  createCompositeRepairPlan,
  createExplicitPreview,
  createRepairPlan,
  downloadRepairOutput,
  getHealth,
  getRepairJob,
  inspectMedia,
} from "../api";
import { C2PA_ZH, MEDIA_STATUS_ZH, REASON_ZH, REPAIR_ZH } from "../reasonCodes";
import type {
  ComplianceCandidate,
  ComplianceConclusion,
  ComplianceIssue,
  ComplianceReport,
  CompositeRepairPlan,
  ConflictGroup,
  ExplicitPreview,
  RepairJobResponse,
} from "../types";

const AIGC_FIELDS = [
  "Label",
  "ContentProducer",
  "ProduceID",
  "ReservedCode1",
  "ContentPropagator",
  "PropagateID",
  "ReservedCode2",
] as const;

const CONCLUSION_META: Record<
  ComplianceConclusion,
  { label: string; alert: "success" | "error" | "info" | "warning"; desc: string }
> = {
  compliant: {
    label: "合规",
    alert: "success",
    desc: "检出唯一一份标识，且通过 GB 45438—2025 国标结构与字段检查，无需修复。",
  },
  noncompliant: {
    label: "不合规",
    alert: "error",
    desc: "已确认存在国标问题（缺字段 / JSON 错误 / 多份记录等），生成修复建议。",
  },
  not_found: {
    label: "未检出标识",
    alert: "info",
    desc: "文件中未检出 AIGC 文件元数据隐式标识。",
  },
  indeterminate: {
    label: "无法判定",
    alert: "warning",
    desc: "元数据载体不可完整读取（结构损坏 / 截断 / 工具分歧），停止自动处理，需人工复核。",
  },
};

const SEVERITY_TAG: Record<string, "error" | "warning" | "default"> = {
  error: "error",
  warn: "warning",
  info: "default",
};

/** 与后端能力表一致（§4.5 各模态共用同一套检测接口） */
const SUPPORTED_MIMES = ["video/mp4", "image/jpeg", "image/png", "text/markdown",
  "application/pdf", "text/html",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"];
const ACCEPT = ".mp4,.jpg,.jpeg,.png,.md,.markdown,.pdf,.html,.htm,.docx,.zip";

function chip(label: string, value: string | undefined | null, color?: string) {
  return value ? <Tag color={color}>{label}: {value}</Tag> : null;
}

function IssueRow({ issue }: { issue: ComplianceIssue }) {
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        alignItems: "baseline",
        padding: "6px 0",
        borderBottom: "1px dashed var(--hairline)",
      }}
    >
      <Tag color={SEVERITY_TAG[issue.severity]} style={{ marginInlineEnd: 0, flexShrink: 0 }}>
        {issue.severity}
      </Tag>
      <div style={{ fontSize: 13, lineHeight: 1.6 }}>
        <code style={{ color: "var(--gold-700)" }}>{issue.code}</code>
        <span style={{ marginLeft: 8, color: "var(--text-600)" }}>{issue.message}</span>
        {issue.field ? (
          <div style={{ color: "var(--text-400)", fontSize: 12 }}>字段: {issue.field}</div>
        ) : null}
      </div>
    </div>
  );
}

function fieldText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ConflictTable({
  groups,
  picked,
  onPick,
  ready,
  repairing,
  onRepair,
}: {
  groups: ConflictGroup[];
  picked: Record<string, string>;
  onPick: (groupId: string, candidateId: string) => void;
  ready: boolean;
  repairing: boolean;
  onRepair: () => void;
}) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="ui-card-head" style={{ borderBottom: "none", padding: "0 0 6px" }}>
        <h3 style={{ fontSize: 14 }}>冲突的多份标识</h3>
      </div>
      <div style={{ fontSize: 13, color: "var(--text-600)", lineHeight: 1.7, marginBottom: 10 }}>
        同一内容文件里有多份互相矛盾的隐式标识。点选要保留的一份后，再按所选标识修复；未选择时不会自动挑选，也不会新造制作编号。
      </div>
      {groups.map((group) => (
        <div key={group.id} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, marginBottom: 6, color: "var(--ink-900)" }}>
            <strong>{group.file}</strong>
            <span style={{ color: "var(--text-400)", marginLeft: 8 }}>
              差异字段：{group.differing_fields.join("、") || "—"}
            </span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="spec-table">
              <thead>
                <tr>
                  <th>选用</th>
                  <th>来源位置</th>
                  {AIGC_FIELDS.map((name) => (
                    <th
                      key={name}
                      style={group.differing_fields.includes(name) ? { background: "#f4ead2" } : undefined}
                    >
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {group.candidates.map((candidate) => (
                  <tr key={candidate.id}>
                    <td>
                      <input
                        type="radio"
                        name={`conflict-${group.id}`}
                        checked={picked[group.id] === candidate.id}
                        aria-label={`${group.file} ${candidate.location}`}
                        onChange={() => onPick(group.id, candidate.id)}
                      />
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>{candidate.location}</td>
                    {AIGC_FIELDS.map((name) => {
                      const differs = candidate.differing_fields.includes(name);
                      return (
                        <td
                          key={name}
                          style={differs ? { background: "#f4ead2", fontWeight: 700, color: "var(--ink-900)" } : undefined}
                        >
                          {fieldText(candidate.fields[name])}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <button
        className="btn-gold"
        disabled={!ready || repairing}
        onClick={onRepair}
        style={{ opacity: !ready || repairing ? 0.6 : 1 }}
      >
        {repairing ? "正在按所选标识修复…" : "按所选标识修复"}
      </button>
    </div>
  );
}

function ExplicitPreviewPanel({
  preview,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: ExplicitPreview;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontSize: 13, color: "var(--text-600)", lineHeight: 1.7 }}>{preview.notice}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 10 }}>
        <figure style={{ margin: 0 }}>
          <img alt="原图" src={preview.before_url} style={{ width: "100%", border: "1px solid var(--hairline)", borderRadius: 8 }} />
          <figcaption style={{ fontSize: 12, color: "var(--text-400)", marginTop: 4 }}>原图</figcaption>
        </figure>
        <figure style={{ margin: 0 }}>
          <img alt="修复后的预览" src={preview.after_url} style={{ width: "100%", border: "1px solid var(--hairline)", borderRadius: 8 }} />
          <figcaption style={{ fontSize: 12, color: "var(--text-400)", marginTop: 4 }}>修复后</figcaption>
        </figure>
      </div>
      <div style={{ fontSize: 13, color: "var(--text-600)", lineHeight: 1.7, marginTop: 8 }}>{preview.position_rationale}</div>
      <div style={{ overflowX: "auto", marginTop: 8 }}>
        <table className="spec-table">
          <thead>
            <tr>
              <th>文件</th>
              <th>字段</th>
              <th>改动</th>
            </tr>
          </thead>
          <tbody>
            {preview.changes.map((change) => (
              <tr key={`${change.file}-${change.field}`}>
                <td>{change.file}</td>
                <td>{change.field}</td>
                <td>{change.action}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button className="btn-gold" disabled={busy} onClick={onConfirm} style={{ opacity: busy ? 0.6 : 1 }}>
          {busy ? "正在按预览修复…" : "确认并修复"}
        </button>
        <button className="btn-ghost-dark" disabled={busy} onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}

function CandidateRow({ cand }: { cand: ComplianceCandidate }) {
  return (
    <div
      style={{
        padding: "8px 0",
        borderBottom: "1px dashed var(--hairline)",
        fontSize: 13,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <code style={{ color: "var(--ink-900)" }}>{cand.tag}</code>
        <Tag color={cand.parseable ? "success" : "error"}>
          {cand.parseable ? "可解析为国标结构" : "无法解析"}
        </Tag>
      </div>
      {cand.location ? (
        <div style={{ color: "var(--text-400)", marginTop: 4, fontSize: 12 }}>
          位置: {cand.location}
        </div>
      ) : null}
      {cand.parsed_fields && cand.parsed_fields.length ? (
        <div style={{ color: "var(--text-400)", marginTop: 4, fontSize: 12 }}>
          已解析字段: {cand.parsed_fields.join(" · ")}
        </div>
      ) : null}
      {cand.raw_preview ? (
        <div className="code-block" style={{ marginTop: 6 }}>
          <pre style={{ margin: 0 }}>{cand.raw_preview}</pre>
        </div>
      ) : null}
    </div>
  );
}

export default function MediaInspectPage() {
  const [serviceUp, setServiceUp] = useState<boolean | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [repairPlan, setRepairPlan] = useState<CompositeRepairPlan | null>(null);
  const [repairing, setRepairing] = useState(false);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [explicitPreview, setExplicitPreview] = useState<ExplicitPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    getHealth()
      .then((h) => setServiceUp(SUPPORTED_MIMES.some((m) => h.capabilities?.[m])))
      .catch(() => setServiceUp(false));
  }, []);

  async function submit() {
    if (!file) {
      message.warning("请先选择待检测的文件");
      return;
    }
    setLoading(true);
    setReport(null);
    setPicked({});
    setExplicitPreview(null);
    try {
      setRepairPlan(null);
      setReport(await inspectMedia(file));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function planRepair() {
    if (!file) return;
    setRepairing(true);
    try {
      setRepairPlan(await createCompositeRepairPlan(file));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setRepairing(false);
    }
  }

  function pickCandidate(groupId: string, candidateId: string) {
    setPicked((current) => ({ ...current, [groupId]: candidateId }));
  }

  async function saveAndRecheck(blob: Blob, name: string) {
    if (!file) return;
    const repaired = new File([blob], name, { type: file.type || blob.type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = repaired.name;
    anchor.click();
    URL.revokeObjectURL(url);
    setFile(repaired);
    setRepairPlan(null);
    setPicked({});
    setExplicitPreview(null);
    setReport(await inspectMedia(repaired));
    message.success("已下载修复结果，并完成复检");
  }

  async function waitRepairJob(jobId: string): Promise<RepairJobResponse> {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      const job = await getRepairJob(jobId);
      if (job.status === "succeeded" || job.status === "failed") return job;
      await new Promise((resolve) => window.setTimeout(resolve, 800));
    }
    throw new Error("修复任务超时");
  }

  async function repairSelected() {
    if (!file || !report?.conflict_groups?.length) return;
    const groups = report.conflict_groups;
    const selection: Record<string, string> = {};
    for (const group of groups) {
      const candidateId = picked[group.id];
      if (!candidateId) return;
      selection[group.id] = candidateId;
    }
    const choice = groups.length === 1
      ? { selected_candidate_id: selection[groups[0].id] }
      : { selections: selection };
    setRepairing(true);
    try {
      if (report.subject) {
        const plan = await createCompositeRepairPlan(file, choice);
        if (!plan.repair_plan.executable) {
          message.error(plan.repair_plan.blocking_reasons.join("；") || "所选标识不能自动修复");
          return;
        }
        const job = await confirmCompositeRepair(plan.plan_id, plan.plan_hash);
        const output = await fetch(job.links.output);
        if (!output.ok) throw new Error("无法下载修复结果");
        await saveAndRecheck(await output.blob(), `repaired-${file.name}`);
        return;
      }
      const plan = await createRepairPlan(file, choice);
      if (!plan.repair_plan.executable) {
        message.error(plan.repair_plan.blocking_reasons.join("；") || "所选标识不能自动修复");
        return;
      }
      const accepted = await confirmRepairPlan(plan.plan_id, plan.plan_hash, "合规检测页");
      const job = await waitRepairJob(accepted.job_id);
      if (job.status !== "succeeded" || !job.output) {
        throw new Error(job.error?.message || "修复失败");
      }
      await saveAndRecheck(await downloadRepairOutput(job.job_id), job.output.file_name);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setRepairing(false);
    }
  }

  async function generatePreview() {
    if (!file) return;
    setPreviewing(true);
    try {
      setExplicitPreview(await createExplicitPreview(file));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setPreviewing(false);
    }
  }

  async function confirmPreview() {
    if (!explicitPreview) return;
    setRepairing(true);
    try {
      const job = await confirmExplicitPreview(explicitPreview.preview_id);
      const output = await fetch(job.links.output);
      if (!output.ok) throw new Error("无法下载修复结果");
      await saveAndRecheck(await output.blob(), `repaired-${file?.name || "scene.zip"}`);
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setRepairing(false);
    }
  }

  async function runRepair() {
    if (!file || !repairPlan) return;
    setRepairing(true);
    try {
      const job = await confirmCompositeRepair(repairPlan.plan_id, repairPlan.plan_hash);
      const output = await fetch(job.links.output);
      if (!output.ok) throw new Error("无法下载修复结果");
      const blob = await output.blob();
      const repaired = new File([blob], `repaired-${file.name}`, { type: file.type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = repaired.name;
      anchor.click();
      URL.revokeObjectURL(url);
      setFile(repaired);
      setRepairPlan(null);
      setReport(await inspectMedia(repaired));
      message.success("已下载修复结果，并完成复检");
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setRepairing(false);
    }
  }

  const meta = CONCLUSION_META[report?.conclusion ?? "indeterminate"];
  // 「合规」「未检出标识」这两档的码只是把结论重说一遍（AIGC_COMPLIANT /
  // AIGC_NOT_FOUND），拼进标题是噪音；只有不合规/无法判定时才补原因。
  const showReason =
    report?.reason_code &&
    report.conclusion !== "compliant" &&
    report.conclusion !== "not_found";
  // 无法判定时的直接原因：后端对每种损坏信号给了不同文案，界面直接挑出来。
  const isIndet = report?.conclusion === "indeterminate";
  const cause = isIndet
    ? (report.issues.find(
        (i) => i.severity === "error" && i.code === "UNREADABLE_CARRIER"
      ) ?? report.issues.find((i) => i.severity === "error")) ?? null
    : null;
  const mediaHint =
    isIndet && report?.media
      ? report.media.probe_error || report.media.decode_error || null
      : null;

  return (
    <>
      <PageBanner
        eyebrow="COMPLIANCE · 图片 / 视频 / 文本"
        title="合规检测"
        sub="检查单模态文件、多模态文件或结果包、虚拟场景包和交互场景包。自动识别类型，检测过程不修改原文件。"
      />

      <section className="section">
        <ToolSteps items={['上传文件', '核查元数据', '查看结论']} active={report ? 2 : loading ? 1 : 0} />
        {serviceUp === false && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 18 }}
            message="合规检测服务不可用"
            description="请确认本地服务已启动，稍后刷新页面重试。"
          />
        )}

        <div className="grid-2" style={{ alignItems: "start" }}>
          {/* ===== 左侧：文件上传 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">↑</span>
              <h3>上传待检测文件</h3>
            </div>
            <div className="ui-card-body">
              <Upload.Dragger
                disabled={loading}
                accept={ACCEPT}
                maxCount={1}
                beforeUpload={(f) => {
                  setFile(f);
                  setReport(null);
                  setExplicitPreview(null);
                  setPicked({});
                  return false;
                }}
                showUploadList={false}
              >
                <p className="ant-upload-drag-icon" style={{ color: "var(--gold-600)" }}>
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽文件到此处</p>
                <p className="ant-upload-hint">
                  单模态文件、多模态 MP4 / 结果包、虚拟场景包或交互场景包。按内容识别，不修改原文件。
                </p>
              </Upload.Dragger>

              {file ? (
                <div
                  style={{
                    marginTop: 12,
                    fontSize: 13,
                    color: "var(--gold-700)",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span style={{ color: "var(--text-400)" }}>已选择</span>
                  <strong style={{ color: "var(--ink-900)" }}>{file.name}</strong>
                  <span style={{ color: "var(--text-400)" }}>
                    （{(file.size / 1048576).toFixed(2)} MB）
                  </span>
                </div>
              ) : null}

              <button
                className="btn-gold"
                onClick={submit}
                disabled={loading || !file}
                style={{ width: "100%", marginTop: 18, opacity: loading || !file ? 0.6 : 1 }}
              >
                {loading ? "检测中…" : "开始合规检测"}
              </button>

              <div
                style={{
                  marginTop: 14,
                  fontSize: 12,
                  color: "var(--text-400)",
                  lineHeight: 1.7,
                }}
              >
                结论分四档：合规 / 不合规 / 未检出标识 / 无法判定；并输出问题清单、修复可行性、媒体状态与 C2PA 存在性。
              </div>
            </div>
          </div>

          {/* ===== 右侧：检测报告 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">◉</span>
              <h3>检测结论与明细</h3>
            </div>
            <div className="ui-card-body">
              {!report && (
                <div
                  style={{
                    padding: "28px 0",
                    textAlign: "center",
                    color: "var(--text-400)",
                  }}
                >
                  <InboxOutlined
                    style={{ fontSize: 26, color: "var(--gold-600)", marginBottom: 8 }}
                  />
                  <div style={{ fontSize: 13 }}>检测完成后这里显示国标结论与问题清单。</div>
                </div>
              )}

              {report && (
                <div>
                  <Alert
                    type={meta.alert}
                    showIcon
                    message={`${meta.label}${showReason ? ` · ${REASON_ZH[report.reason_code!] ?? report.reason_code}` : ""}`}
                    description={meta.desc}
                  />

                  {isIndet && cause && (
                    <div
                      style={{
                        marginTop: 12,
                        padding: "10px 12px",
                        borderRadius: 8,
                        background: "var(--warn-bg, rgba(255, 230, 204, 0.6))",
                        border: "1px solid var(--hairline)",
                      }}
                    >
                      <div
                        style={{
                          fontWeight: 600,
                          color: "var(--ink-900)",
                          marginBottom: 6,
                          fontSize: 13,
                        }}
                      >
                        无法判定的直接原因
                      </div>
                      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                        <Tag color="error" style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                          {cause.code}
                        </Tag>
                        <span style={{ color: "var(--text-600)", lineHeight: 1.6 }}>
                          {cause.message}
                        </span>
                      </div>
                      {mediaHint && (
                        <div
                          style={{
                            marginTop: 6,
                            color: "var(--text-400)",
                            fontSize: 12,
                            lineHeight: 1.6,
                          }}
                        >
                          探测诊断: {mediaHint}
                        </div>
                      )}
                    </div>
                  )}

                  {report.subject ? (
                    <div style={{ marginTop: 12, fontSize: 13, color: "var(--text-600)" }}>
                      识别为 <strong style={{ color: "var(--ink-900)" }}>{report.subject.label}</strong>
                      。每个内容文件单独判断隐式标识；场景包同时检查显式提示。
                    </div>
                  ) : null}
                  {report.units && report.units.length > 0 ? (
                    <div style={{ marginTop: 10 }}>
                      {report.units.map((unit) => (
                        <div key={unit.name} style={{ fontSize: 13, padding: "4px 0", color: "var(--text-600)" }}>
                          <strong>{unit.name}</strong> · {CONCLUSION_META[unit.conclusion]?.label ?? unit.conclusion}
                          · {unit.record_count} 份隐式标识
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {report.explicit_preview?.available ? (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 13, color: "var(--text-600)", lineHeight: 1.7, marginBottom: 8 }}>
                        场景显式提示不合格。先看修复预览，确认后才写入。这一步和隐式标识的冲突选择分开处理。
                      </div>
                      <button className="btn-gold" disabled={previewing || repairing} onClick={() => void generatePreview()}>
                        {previewing ? "正在生成预览…" : "生成修复预览"}
                      </button>
                      {explicitPreview ? (
                        <ExplicitPreviewPanel
                          preview={explicitPreview}
                          busy={repairing}
                          onConfirm={() => void confirmPreview()}
                          onCancel={() => setExplicitPreview(null)}
                        />
                      ) : null}
                    </div>
                  ) : null}
                  {report.conflict_groups && report.conflict_groups.length > 0 ? (
                    <ConflictTable
                      groups={report.conflict_groups}
                      picked={picked}
                      onPick={pickCandidate}
                      ready={report.conflict_groups.every((group) => Boolean(picked[group.id]))}
                      repairing={repairing}
                      onRepair={() => void repairSelected()}
                    />
                  ) : null}
                  {report.subject && report.repairability === "auto_fixable" && file
                    && !(report.conflict_groups && report.conflict_groups.length > 0) ? (
                    <div style={{ marginTop: 12 }}>
                      <button className="btn-gold" disabled={repairing} onClick={() => void planRepair()}>
                        {repairing ? "正在处理…" : "生成修复方案"}
                      </button>
                      {repairPlan ? (
                        <div style={{ marginTop: 10, fontSize: 13, color: "var(--text-600)" }}>
                          {repairPlan.repair_plan.executable
                            ? repairPlan.repair_plan.actions.join("；")
                            : repairPlan.repair_plan.blocking_reasons.join("；")}
                          {repairPlan.repair_plan.executable ? (
                            <div>
                              <button className="btn-gold" style={{ marginTop: 8 }} disabled={repairing} onClick={() => void runRepair()}>
                                确认执行并复检
                              </button>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  <div style={{ marginTop: 14, display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {chip("置信度", report.confidence === "high" ? "高" : "低")}
                    {chip("媒体状态", MEDIA_STATUS_ZH[report.media_status ?? ""])}
                    {chip(
                      "尺寸",
                      report.media?.width && report.media?.height
                        ? `${report.media.width}×${report.media.height}`
                        : undefined
                    )}
                    {chip("C2PA", C2PA_ZH[report.c2pa_presence] ?? report.c2pa_presence)}
                    {chip("修复", report.repairability ? REPAIR_ZH[report.repairability] : undefined)}
                    {chip("检出记录", String(report.record_count))}
                  </div>

                  <div style={{ marginTop: 18 }}>
                    <div className="ui-card-head" style={{ borderBottom: "none", padding: "0 0 4px" }}>
                      <h3 style={{ fontSize: 14 }}>问题清单</h3>
                    </div>
                    {report.issues.length === 0 ? (
                      <div style={{ fontSize: 13, color: "var(--text-600)", padding: "6px 0" }}>
                        ✓ 未发现国标结构问题
                      </div>
                    ) : (
                      <div>
                        {report.issues.map((it, idx) => (
                          <IssueRow key={idx} issue={it} />
                        ))}
                      </div>
                    )}
                  </div>

                  <div style={{ marginTop: 18 }}>
                    <div className="ui-card-head" style={{ borderBottom: "none", padding: "0 0 4px" }}>
                      <h3 style={{ fontSize: 14 }}>AIGC 候选标识（{report.record_count}）</h3>
                    </div>
                    {report.candidates.length === 0 ? (
                      <div style={{ fontSize: 13, color: "var(--text-600)", padding: "6px 0" }}>
                        {report.conclusion === "indeterminate"
                          ? "未读到可解析的记录——载体不可靠，以上方问题清单为准，请人工复核。"
                          : "未检出任何含 AIGC 的元数据记录。"}
                      </div>
                    ) : (
                      <div>
                        {report.candidates.map((c, idx) => (
                          <CandidateRow key={idx} cand={c} />
                        ))}
                      </div>
                    )}
                  </div>

                  <Collapse
                    ghost
                    style={{ marginTop: 14 }}
                    items={[
                      {
                        key: "raw",
                        label: "查看原始检测报告（JSON）与文件/检测器信息",
                        children: (
                          <div>
                            <div
                              style={{
                                fontSize: 12,
                                color: "var(--text-400)",
                                lineHeight: 1.9,
                                marginBottom: 8,
                              }}
                            >
                              文件: {report.file_name ?? "-"} · {report.detected_mime_type} ·{" "}
                              {(report.size_bytes ?? 0 / 1048576).toFixed(2)} MB · SHA-256:{" "}
                              {(report.sha256 ?? "-").slice(0, 16)}… · 请求号: {report.request_id}
                              <br />
                              检测器: {report.detector_version} · ExifTool:{" "}
                              {report.exiftool_version ?? "-"} · 耗时: {report.elapsed_ms} ms
                              <br />
                              {/* BMFF box 结构是 MP4 专有；图片与文档报告 applicable=false，整行不显示 */}
                              {report.bmff.applicable === false ? (
                                <>载体结构: {report.bmff.note ?? "图片无 BMFF box 结构"}</>
                              ) : (
                                <>
                                  容器结构: ftyp={String(report.bmff.has_ftyp)} · moov=
                                  {String(report.bmff.has_moov)} · mdat=
                                  {String(report.bmff.has_mdat)}
                                  {" · "}C2PA uuid: {report.bmff.c2pa_uuid.length}
                                </>
                              )}
                            </div>
                            <div className="code-block">
                              <pre style={{ margin: 0, maxHeight: 280, overflow: "auto" }}>
                                {JSON.stringify(report, null, 2)}
                              </pre>
                            </div>
                          </div>
                        ),
                      },
                    ]}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
