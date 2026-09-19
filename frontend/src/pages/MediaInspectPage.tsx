import { useEffect, useState } from "react";
import { Alert, Collapse, Tag, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import PageBanner from "../components/PageBanner";
import { getHealth, inspectMedia } from "../api";
import type {
  ComplianceCandidate,
  ComplianceConclusion,
  ComplianceIssue,
  ComplianceReport,
} from "../types";

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

const REASON_ZH: Record<string, string> = {
  DUPLICATE_RECORDS: "检测到多份 AIGC 标识（国标要求同一文件仅保留一份）",
  MISSING_FIELD: "缺失必填字段，或身份/编号字段为空",
  BAD_JSON: "元数据值无法解析为国标结构（JSON 非法或缺外层 AIGC 对象）",
  BAD_LABEL: "Label 取值非法（必须是字符串 1 / 2 / 3）",
  UNKNOWN_FIELD: "国标对象外存在字段",
  LEGACY_CARRIER: "旧载体 QuickTime:Comment（存在正式迁移规则）",
  CHARSET: "字段含国标规定字符范围外的字符，需人工复核",
  FIRST_WRITE_MISMATCH: "传播方/传播编号与制作者不一致（可能是合法二次传播）",
  // 图片与视频共用这个码，文案保持格式中立（后端会在问题清单里给具体原因）
  UNREADABLE_CARRIER: "元数据载体不可完整读取（结构损坏 / 截断 / 编码异常）",
  REGISTRY_MISMATCH: "编号登记库核对不一致",
  TOOL_DIVERGENCE: "读取工具结果分歧",
};

const REPAIR_ZH: Record<string, string> = {
  auto_fixable: "可确定修复（去重 / Label 转字符串 / 补空值保留字段）",
  needs_human: "需人工确认（来源不明 / 冲突 / 身份缺失，不猜测不乱修）",
  forbidden: "禁止修复（需猜测来源或伪造编号）",
};

const MEDIA_STATUS_ZH: Record<string, string> = {
  ok: "正常可读",
  degraded: "降级（视频流不可解）",
  unreadable: "不可读",
};

const C2PA_ZH: Record<string, string> = {
  absent: "未发现 C2PA",
  present_unverified: "存在 C2PA（未验签）",
  indeterminate: "无法判断",
};

const SEVERITY_TAG: Record<string, "error" | "warning" | "default"> = {
  error: "error",
  warn: "warning",
  info: "default",
};

/** 与后端能力表一致（§4.5 图片/视频共用同一套检测接口） */
const SUPPORTED_MIMES = ["video/mp4", "image/jpeg", "image/png"];
const ACCEPT = ".mp4,.jpg,.jpeg,.png,video/mp4,image/jpeg,image/png";

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

  useEffect(() => {
    getHealth()
      .then((h) => setServiceUp(SUPPORTED_MIMES.some((m) => h.capabilities?.[m])))
      .catch(() => setServiceUp(false));
  }, []);

  async function submit() {
    if (!file) {
      message.warning("请先选择待检测的文件（MP4 / JPEG / PNG）");
      return;
    }
    setLoading(true);
    setReport(null);
    try {
      setReport(await inspectMedia(file));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  const meta = CONCLUSION_META[report?.conclusion ?? "indeterminate"];
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
        eyebrow="COMPLIANCE · MP4 / JPEG / PNG"
        title="媒体合规检测"
        sub="上传 MP4 / JPEG / PNG，按 GB 45438—2025 只读检测已有 AIGC 元数据隐式标识是否合规，全程不改动文件。"
      />

      <section className="section">
        {serviceUp === false && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 18 }}
            message="合规检测服务不可用"
            description="无法连接 /api/v1（labeling-backend，端口 8002）。请先启动后端服务（含 /api/v1/compliance-inspect 接口的版本）后再试。"
          />
        )}

        <div className="grid-2" style={{ alignItems: "start" }}>
          {/* ===== 左侧：文件上传 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">↑</span>
              <h3>1 · 上传待检测文件</h3>
            </div>
            <div className="ui-card-body">
              <Upload.Dragger
                accept={ACCEPT}
                maxCount={1}
                beforeUpload={(f) => {
                  setFile(f);
                  return false;
                }}
                showUploadList={false}
              >
                <p className="ant-upload-drag-icon" style={{ color: "var(--gold-600)" }}>
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽 MP4 / JPEG / PNG 到此处</p>
                <p className="ant-upload-hint">
                  单文件 · 按文件内容判定真实格式 · 只读检测不修改文件
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
              <h3>2 · 合规检测报告</h3>
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
                    message={`${meta.label}${!isIndet && report.reason_code ? ` · ${REASON_ZH[report.reason_code] ?? report.reason_code}` : ""}`}
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
                              {/* BMFF box 结构是 MP4 专有；图片报告 applicable=false，整行不显示 */}
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
