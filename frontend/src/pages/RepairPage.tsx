import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Checkbox,
  Collapse,
  Input,
  Progress,
  Select,
  Tag,
  Upload,
  message,
} from "antd";
import { InboxOutlined } from "@ant-design/icons";
import PageBanner from "../components/PageBanner";
import {
  confirmRepairPlan,
  createRepairPlan,
  downloadRepairOutput,
  getHealth,
  getRepairJob,
  getRepairPlan,
} from "../api";
import {
  C2PA_ZH,
  REASON_ZH,
  REPAIR_ZH,
  REPAIR_ERR_ZH,
  SOURCE_TYPE_ZH,
  WRITE_CONTEXT_ZH,
} from "../reasonCodes";
import type {
  RepairJobResponse,
  RepairPlanDraft,
  RepairPlanResponse,
  RepairSourceType,
  RepairWriteContext,
} from "../types";
import type { View } from "../nav";

/** 修复工作台只处理位图：写入/回读/像素指纹校验都建立在 JPEG/PNG 载体上 */
const ACCEPT = ".jpg,.jpeg,.png,image/jpeg,image/png";

const STAGE_ZH: Record<string, string> = {
  queued: "排队中",
  archiving_original: "归档原件",
  writing_metadata: "写入元数据",
  verifying_metadata: "回读校验",
  publishing_output: "生成结果文件",
  completed: "已完成",
  failed: "已失败",
  files_purged: "结果文件已过保留期清理",
};

const SOURCE_OPTIONS = (Object.keys(SOURCE_TYPE_ZH) as RepairSourceType[]).map((v) => ({
  value: v,
  label: SOURCE_TYPE_ZH[v],
}));

const CONTEXT_OPTIONS = (Object.keys(WRITE_CONTEXT_ZH) as RepairWriteContext[]).map((v) => ({
  value: v,
  label: WRITE_CONTEXT_ZH[v],
}));

const EMPTY_AIGC = {
  Label: "1",
  ContentProducer: "",
  ProduceID: "",
  ReservedCode1: "",
  ContentPropagator: "",
  PropagateID: "",
  ReservedCode2: "",
};

function newId(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function short(value: unknown, max = 60) {
  if (value === null || value === undefined || value === "") return "（空）";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div style={{ fontSize: 12, color: "var(--text-400)", marginBottom: 4 }}>{label}</div>
      {children}
    </label>
  );
}

/** 计划草稿：动作 + 字段级变更 + 阻塞项。变更表是「看清改什么」的核心，
 *  所以 before/after 逐字段列出，而不是只给一句结论。 */
function PlanDetail({ draft }: { draft: RepairPlanDraft }) {
  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <Tag color={draft.executable ? "success" : "default"}>
          {draft.executable ? "可执行" : "不可执行"}
        </Tag>
        <Tag color={draft.repairability === "confirmable" ? "blue" : "warning"}>
          {REPAIR_ZH[draft.repairability] ?? draft.repairability}
        </Tag>
        {draft.source_type ? (
          <Tag>来源: {SOURCE_TYPE_ZH[draft.source_type] ?? draft.source_type}</Tag>
        ) : null}
        <Tag>写入语境: {WRITE_CONTEXT_ZH[draft.write_context] ?? draft.write_context}</Tag>
      </div>

      {draft.blocking_reasons.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 12 }}
          message="阻塞项（未解决前不可执行）"
          description={
            <ul style={{ margin: "4px 0 0", paddingInlineStart: 18 }}>
              {draft.blocking_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          }
        />
      )}

      {draft.warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 10 }}
          message="提醒"
          description={
            <ul style={{ margin: "4px 0 0", paddingInlineStart: 18 }}>
              {draft.warnings.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          }
        />
      )}

      <div style={{ marginTop: 14, fontSize: 13 }}>
        <div style={{ fontWeight: 600, color: "var(--ink-900)", marginBottom: 6 }}>
          将执行的动作（{draft.actions.length}）
        </div>
        {draft.actions.length === 0 ? (
          <div style={{ color: "var(--text-400)" }}>无——当前文件不需要改动。</div>
        ) : (
          <ol style={{ margin: 0, paddingInlineStart: 20, color: "var(--text-600)" }}>
            {draft.actions.map((a, i) => (
              <li key={i} style={{ lineHeight: 1.8 }}>
                {a}
              </li>
            ))}
          </ol>
        )}
      </div>

      {draft.field_changes.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontWeight: 600, color: "var(--ink-900)", marginBottom: 6, fontSize: 13 }}>
            字段变更（{draft.field_changes.length}）
          </div>
          {draft.field_changes.map((c, i) => (
            <div
              key={i}
              style={{
                padding: "8px 0",
                borderBottom: "1px dashed var(--hairline)",
                fontSize: 13,
              }}
            >
              <code style={{ color: "var(--ink-900)" }}>{c.field}</code>
              <div style={{ marginTop: 4, color: "var(--text-600)" }}>
                <span style={{ color: "var(--text-400)" }}>{short(c.before, 40)}</span>
                <span style={{ margin: "0 8px", color: "var(--gold-700)" }}>→</span>
                <strong>{short(c.after, 40)}</strong>
              </div>
              <div style={{ color: "var(--text-400)", fontSize: 12, marginTop: 2 }}>{c.reason}</div>
            </div>
          ))}
        </div>
      )}

      {draft.proposed_document && (
        <Collapse
          ghost
          style={{ marginTop: 10 }}
          items={[
            {
              key: "doc",
              label: "查看修复后的完整标识（写入内容）",
              children: (
                <div className="code-block">
                  <pre style={{ margin: 0, maxHeight: 240, overflow: "auto" }}>
                    {JSON.stringify(draft.proposed_document, null, 2)}
                  </pre>
                </div>
              ),
            },
          ]}
        />
      )}
    </div>
  );
}

export default function RepairPage({ onNavigate }: { onNavigate?: (v: View) => void }) {
  const [serviceUp, setServiceUp] = useState<boolean | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<RepairPlanResponse | null>(null);
  const [job, setJob] = useState<RepairJobResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [downloading, setDownloading] = useState(false);

  // 可信来源（可选）。缺标识的文件没有它就无解——不许凭空生成编号，
  // 所以这块是"可选但常常必需"，默认收起而不是隐藏。
  const [useTrusted, setUseTrusted] = useState(false);
  const [aigc, setAigc] = useState({ ...EMPTY_AIGC });
  const [sourceType, setSourceType] = useState<RepairSourceType>("authorized_manual");
  const [sourceRef, setSourceRef] = useState("");
  const [writeContext, setWriteContext] = useState<RepairWriteContext>("initial_generation");

  const [operatorLabel, setOperatorLabel] = useState("");
  const [ack, setAck] = useState(false);

  const timer = useRef<number | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setServiceUp(Boolean(h.capabilities?.["image/png"] || h.capabilities?.["image/jpeg"])))
      .catch(() => setServiceUp(false));
  }, []);

  // 轮询直到终态。终态之外都继续轮询：stage 会自己前进，不需要前端猜进度。
  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    timer.current = window.setInterval(async () => {
      try {
        setJob(await getRepairJob(job.job_id));
      } catch (e) {
        message.error((e as Error).message);
        if (timer.current) window.clearInterval(timer.current);
      }
    }, 1000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [job]);

  // 首次生成：GB 要求传播方/传播编号与制作者一致，故同一份编号做默认值。
  useEffect(() => {
    setAigc((prev) => ({
      ...prev,
      ProduceID: prev.ProduceID || newId("PRD"),
      PropagateID: prev.PropagateID || newId("PRP"),
    }));
  }, []);

  const isInitial = writeContext === "initial_generation";

  function reset() {
    setPlan(null);
    setJob(null);
    setAck(false);
  }

  async function makePlan() {
    if (!file) {
      message.warning("请先选择待修复的 JPEG / PNG");
      return;
    }
    if (useTrusted && !sourceRef.trim()) {
      message.warning("请填写来源凭证：审计要凭它回溯，不能留空");
      return;
    }
    setPlanning(true);
    reset();
    try {
      const options = useTrusted
        ? {
            trusted_input: {
              AIGC: isInitial
                ? {
                    ...aigc,
                    ContentPropagator: aigc.ContentProducer,
                    PropagateID: aigc.ProduceID,
                  }
                : aigc,
              source_type: sourceType,
              source_reference: sourceRef.trim(),
              write_context: writeContext,
            },
          }
        : undefined;
      setPlan(await createRepairPlan(file, options));
      message.success("修复计划已生成，请核对后确认执行");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setPlanning(false);
    }
  }

  async function confirm() {
    if (!plan) return;
    setBusy(true);
    try {
      const accepted = await confirmRepairPlan(plan.plan_id, plan.plan_hash, operatorLabel.trim());
      setJob(accepted);
      // 计划状态随之流转，重新拉一次以拿到 job_id 关联
      getRepairPlan(plan.plan_id)
        .then(setPlan)
        .catch(() => undefined);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    if (!job?.output?.download_url) return;
    setDownloading(true);
    try {
      const blob = await downloadRepairOutput(job.job_id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = job.output.file_name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  const draft = plan?.repair_plan ?? null;
  const canConfirm = Boolean(
    plan && draft?.executable && plan.status === "pending" && plan.input.available && ack && operatorLabel.trim()
  );
  const running = Boolean(job && job.status !== "succeeded" && job.status !== "failed");

  return (
    <>
      <PageBanner
        eyebrow="REPAIR · JPEG / PNG"
        title="元数据修复工作台"
        sub="按 GB 45438—2025 修复图片 AIGC 隐式标识：先查看计划，再显式确认执行。修复只改元数据、不动像素，原件保留且全程留审计。"
      />

      <section className="section">
        {serviceUp === false && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 18 }}
            message="修复服务不可用"
            description="无法连接 /api/v1（labeling-backend，端口 8002）。请先启动后端服务后再试。"
          />
        )}

        <div className="grid-2" style={{ alignItems: "start" }}>
          {/* ===== 左：上传 + 可信来源 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">↑</span>
              <h3>1 · 上传待修复图片</h3>
            </div>
            <div className="ui-card-body">
              <Upload.Dragger
                accept={ACCEPT}
                maxCount={1}
                beforeUpload={(f) => {
                  setFile(f);
                  reset();
                  return false;
                }}
                showUploadList={false}
              >
                <p className="ant-upload-drag-icon" style={{ color: "var(--gold-600)" }}>
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽 JPEG / PNG 到此处</p>
                <p className="ant-upload-hint">单文件 · 只改元数据不改像素 · 原件保留</p>
              </Upload.Dragger>

              {file ? (
                <div style={{ marginTop: 12, fontSize: 13, display: "flex", gap: 8 }}>
                  <span style={{ color: "var(--text-400)" }}>已选择</span>
                  <strong style={{ color: "var(--ink-900)" }}>{file.name}</strong>
                  <span style={{ color: "var(--text-400)" }}>
                    （{(file.size / 1048576).toFixed(2)} MB）
                  </span>
                </div>
              ) : null}

              <div style={{ marginTop: 16 }}>
                <Checkbox checked={useTrusted} onChange={(e) => setUseTrusted(e.target.checked)}>
                  标识已损坏到无法从文件恢复，由我提供权威来源
                </Checkbox>
                <div style={{ fontSize: 12, color: "var(--text-400)", marginTop: 4, lineHeight: 1.7 }}>
                  例如 JSON 已损坏、或身份与编号无法从文件内容推定。提交的七字段必须有凭证可回溯，
                  系统不会自行编造制作者与编号。
                  <br />
                  注意：本工作台只修正<strong>已有但坏了</strong>的标识，
                  <strong>不负责给没有标识的文件新增</strong>——那属于打标流程。
                </div>
              </div>

              {useTrusted && (
                <div style={{ marginTop: 12 }}>
                  <Field label="制作者 ContentProducer">
                    <Input
                      value={aigc.ContentProducer}
                      onChange={(e) => setAigc({ ...aigc, ContentProducer: e.target.value })}
                      placeholder="如：ORG_1565201000000016"
                    />
                  </Field>
                  <Field label="制作编号 ProduceID">
                    <Input
                      value={aigc.ProduceID}
                      onChange={(e) => setAigc({ ...aigc, ProduceID: e.target.value })}
                    />
                  </Field>
                  <Field label="标签 Label">
                    <Select
                      value={aigc.Label}
                      onChange={(v) => setAigc({ ...aigc, Label: v })}
                      options={[
                        { value: "1", label: "1 · AI 生成" },
                        { value: "2", label: "2 · AI 生成并深度合成" },
                        { value: "3", label: "3 · 疑似 AI 生成" },
                      ]}
                      style={{ width: "100%" }}
                    />
                  </Field>

                  <Field label="写入语境">
                    <Select
                      value={writeContext}
                      onChange={(v) => setWriteContext(v)}
                      options={CONTEXT_OPTIONS}
                      style={{ width: "100%" }}
                    />
                  </Field>

                  {isInitial ? (
                    <div style={{ fontSize: 12, color: "var(--text-400)", marginBottom: 10 }}>
                      首次生成写入：国标要求传播方/传播编号与制作者一致，提交时会自动同步，
                      无需另行填写。
                    </div>
                  ) : (
                    <>
                      <Field label="传播方 ContentPropagator">
                        <Input
                          value={aigc.ContentPropagator}
                          onChange={(e) => setAigc({ ...aigc, ContentPropagator: e.target.value })}
                        />
                      </Field>
                      <Field label="传播编号 PropagateID">
                        <Input
                          value={aigc.PropagateID}
                          onChange={(e) => setAigc({ ...aigc, PropagateID: e.target.value })}
                        />
                      </Field>
                    </>
                  )}

                  <Field label="来源类型">
                    <Select
                      value={sourceType}
                      onChange={(v) => setSourceType(v)}
                      options={SOURCE_OPTIONS}
                      style={{ width: "100%" }}
                    />
                  </Field>
                  <Field label="来源凭证 source_reference（必填，写入审计）">
                    <Input
                      value={sourceRef}
                      onChange={(e) => setSourceRef(e.target.value)}
                      placeholder="如：工单号 / 登记库记录 ID / 授权文件编号"
                    />
                  </Field>

                  <div style={{ fontSize: 12, color: "var(--text-400)", lineHeight: 1.7 }}>
                    字段取值须落在国标字符范围内：可打印 ASCII，
                    不含空格、双引号、反斜杠，也不含中文。机构名请用登记编号（如统一社会信用代码），
                    不要直接写中文名称——否则后端会以 TRUSTED_METADATA_INVALID 拒绝。
                  </div>
                </div>
              )}

              <button
                className="btn-gold"
                onClick={makePlan}
                disabled={planning || !file}
                style={{ width: "100%", marginTop: 14, opacity: planning || !file ? 0.6 : 1 }}
              >
                {planning ? "生成中…" : "生成修复计划"}
              </button>
            </div>
          </div>

          {/* ===== 右：计划 → 确认 → 结果 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">◉</span>
              <h3>2 · 修复计划与执行</h3>
            </div>
            <div className="ui-card-body">
              {!plan && !job && (
                <div style={{ padding: "28px 0", textAlign: "center", color: "var(--text-400)" }}>
                  <InboxOutlined style={{ fontSize: 26, color: "var(--gold-600)", marginBottom: 8 }} />
                  <div style={{ fontSize: 13 }}>
                    生成计划后这里显示检测结论、将执行的动作与字段级变更。
                  </div>
                </div>
              )}

              {plan && (
                <div>
                  <Alert
                    type={
                      plan.inspection.conclusion === "compliant"
                        ? "success"
                        : plan.inspection.conclusion === "indeterminate"
                        ? "warning"
                        : "error"
                    }
                    showIcon
                    message={`检测结论：${plan.inspection.conclusion}${
                      plan.inspection.reason_codes.length
                        ? ` · ${plan.inspection.reason_codes
                            .map((c) => REASON_ZH[c] ?? c)
                            .join("；")}`
                        : ""
                    }`}
                    description={`检出记录 ${plan.inspection.record_count} 份 · 载体 ${plan.inspection.detected_format} · 修复可行性 ${
                      REPAIR_ZH[plan.repair_plan.repairability] ?? plan.repair_plan.repairability
                    }`}
                  />

                  {plan.inspection.conclusion === "not_found" && (
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginTop: 10 }}
                      message="该文件没有标识，不属于修复范围"
                      description={
                        <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                          修复工作台只修正「已有但已损坏」的标识。给无标识的文件首次写入一份标识
                          属于打标流程，即使勾选可信来源也不会在此执行。
                          {onNavigate ? (
                            <>
                              {" "}
                              <a onClick={() => onNavigate("labeling")}>前往媒体打标 →</a>
                            </>
                          ) : null}
                        </div>
                      }
                    />
                  )}

                  <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8 }}>
                    <Tag>计划状态: {plan.status}</Tag>
                    <Tag>原件: {plan.input.available ? "在库" : "已清理"}</Tag>
                    <Tag>
                      C2PA: {C2PA_ZH[plan.inspection.c2pa_presence.status] ?? plan.inspection.c2pa_presence.status}
                    </Tag>
                    <Tag>交叉核对: {plan.inspection.cross_reader.status}</Tag>
                  </div>

                  {plan.inspection.issues.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      {plan.inspection.issues.map((it, i) => (
                        <div
                          key={i}
                          style={{
                            display: "flex",
                            gap: 8,
                            alignItems: "baseline",
                            padding: "5px 0",
                            borderBottom: "1px dashed var(--hairline)",
                            fontSize: 13,
                          }}
                        >
                          <Tag
                            color={it.severity === "error" ? "error" : it.severity === "warn" ? "warning" : "default"}
                            style={{ marginInlineEnd: 0, flexShrink: 0 }}
                          >
                            {it.severity}
                          </Tag>
                          <div>
                            <code style={{ color: "var(--gold-700)" }}>{it.code}</code>
                            <span style={{ marginLeft: 8, color: "var(--text-600)" }}>{it.message}</span>
                            {it.field ? (
                              <div style={{ color: "var(--text-400)", fontSize: 12 }}>
                                字段: {it.field}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hairline)" }}>
                    <PlanDetail draft={plan.repair_plan} />
                  </div>

                  {/* ===== 确认执行 ===== */}
                  <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hairline)" }}>
                    <div style={{ fontWeight: 600, color: "var(--ink-900)", marginBottom: 8, fontSize: 14 }}>
                      3 · 确认执行
                    </div>

                    {!plan.input.available ? (
                      <Alert type="error" showIcon message="原件已过保留期被清理，无法执行。请重新上传。" />
                    ) : !plan.repair_plan.executable ? (
                      <Alert
                        type="info"
                        showIcon
                        message="该计划不可自动执行"
                        description="请按上方阻塞项处理：通常是缺少可信来源，或问题本身不允许自动修复。"
                      />
                    ) : (
                      <>
                        <Field label="操作人（写入审计记录）">
                          <Input
                            value={operatorLabel}
                            onChange={(e) => setOperatorLabel(e.target.value)}
                            placeholder="如：张三 / 学号 / 工号"
                          />
                        </Field>
                        <Checkbox checked={ack} onChange={(e) => setAck(e.target.checked)}>
                          我已核对上述动作与字段变更，确认按此计划写入
                        </Checkbox>
                        <button
                          className="btn-gold"
                          onClick={confirm}
                          disabled={!canConfirm || busy}
                          style={{ width: "100%", marginTop: 12, opacity: canConfirm && !busy ? 1 : 0.6 }}
                        >
                          {busy ? "提交中…" : "确认并执行修复"}
                        </button>
                        <div style={{ fontSize: 12, color: "var(--text-400)", marginTop: 8, lineHeight: 1.7 }}>
                          确认即代表授权写入，操作人与计划摘要一并记入审计；原件不会被覆盖。
                        </div>
                      </>
                    )}
                  </div>
                </div>
              )}

              {/* ===== 执行结果 ===== */}
              {job && (
                <div style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--hairline)" }}>
                  <div style={{ fontWeight: 600, color: "var(--ink-900)", marginBottom: 8, fontSize: 14 }}>
                    4 · 修复结果
                  </div>

                  <div style={{ fontSize: 13, color: "var(--text-600)", marginBottom: 8 }}>
                    阶段: {STAGE_ZH[job.stage] ?? job.stage} · 状态: {job.status}
                  </div>

                  {running && (
                    <Progress
                      percent={job.progress ?? 0}
                      status="active"
                      showInfo={job.progress !== null}
                    />
                  )}

                  {job.status === "succeeded" && (
                    <>
                      <Alert
                        type="success"
                        showIcon
                        message="修复完成"
                        description={
                          <div style={{ fontSize: 13, lineHeight: 1.9 }}>
                            复检结论: {job.validation?.post_repair_conclusion ?? "-"}
                            <br />
                            像素未改动: {job.validation?.pixel_sha256_unchanged ? "是" : "否"}
                            <br />
                            操作人: {job.confirmation.operator_label ?? "-"}（
                            {job.confirmation.method ?? "-"}）
                          </div>
                        }
                      />
                      {job.output?.download_url ? (
                        <button
                          className="btn-gold"
                          onClick={download}
                          disabled={downloading}
                          style={{ width: "100%", marginTop: 12, opacity: downloading ? 0.6 : 1 }}
                        >
                          {downloading ? "下载中…" : `下载修复结果（${job.output.file_name}）`}
                        </button>
                      ) : (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 12 }}
                          message="结果文件已过保留期被清理"
                          description="审计记录仍在，但文件本身不再可下载。"
                        />
                      )}
                    </>
                  )}

                  {job.status === "failed" && (
                    <Alert
                      type="error"
                      showIcon
                      message={`修复失败：${job.error?.code ?? "-"}`}
                      description={
                        <div style={{ fontSize: 13 }}>
                          {REPAIR_ERR_ZH[job.error?.code ?? ""] ?? job.error?.message ?? "未知原因"}
                          {job.error?.retryable ? "（可重试）" : "（不可重试）"}
                        </div>
                      }
                    />
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
