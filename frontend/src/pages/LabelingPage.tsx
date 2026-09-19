import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Input, Progress, Select, Upload, message } from "antd";
import { DownloadOutlined, InboxOutlined, ThunderboltOutlined } from "@ant-design/icons";
import PageBanner from "../components/PageBanner";
import { createLabelJob, downloadLabelJob, getHealth, getLabelJob, modalityForFile } from "../api";
import type { LabelJobResponse } from "../types";

const TERMINAL = new Set(["succeeded", "failed"]);

/** 与后端能力表一致的受支持格式（§4.5 图片/视频共用同一套接口） */
const SUPPORTED_MIMES = ["video/mp4", "image/jpeg", "image/png"];
const ACCEPT = ".mp4,.jpg,.jpeg,.png,video/mp4,image/jpeg,image/png";

const STAGE_ZH: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  writing: "写入元数据",
  verifying: "回读校验",
  checking_media: "媒体完整性校验",
};

const STATUS_CLASS: Record<string, string> = {
  queued: "warn",
  running: "warn",
  succeeded: "pass",
  failed: "fail",
};

const FIELD_LABEL: React.CSSProperties = {
  fontSize: 12.5,
  color: "var(--text-600)",
  marginBottom: 8,
  letterSpacing: "0.04em",
};

export default function LabelingPage() {
  const [serviceUp, setServiceUp] = useState<boolean | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [policy, setPolicy] = useState("reject");
  const [producer, setProducer] = useState("ORG_1565201000000016");
  const [label, setLabel] = useState("1");
  const [creating, setCreating] = useState(false);
  const [job, setJob] = useState<LabelJobResponse | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setServiceUp(SUPPORTED_MIMES.some((m) => h.capabilities?.[m])))
      .catch(() => setServiceUp(false));
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, []);

  const poll = useCallback(async (jobId: string) => {
    try {
      const j = await getLabelJob(jobId);
      setJob(j);
      if (!TERMINAL.has(j.status)) {
        timerRef.current = window.setTimeout(() => poll(jobId), 1500);
      }
    } catch (e) {
      message.error((e as Error).message);
    }
  }, []);

  async function submit() {
    if (!file) {
      message.warning("请先选择待打标的文件（MP4 / JPEG / PNG）");
      return;
    }
    const produceId = crypto.randomUUID().toUpperCase();
    setCreating(true);
    setJob(null);
    try {
      const j = await createLabelJob(file, {
        standard: "GB45438-2025",
        modality: modalityForFile(file),
        existing_metadata_policy: policy as "reject" | "replace",
        AIGC: {
          Label: label,
          ContentProducer: producer,
          ProduceID: produceId,
          ReservedCode1: "",
          ContentPropagator: producer,
          PropagateID: produceId,
          ReservedCode2: "",
        },
      });
      setJob(j);
      if (!TERMINAL.has(j.status)) poll(j.job_id);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setCreating(false);
    }
  }

  async function download() {
    if (!job?.job_id) return;
    try {
      const blob = await downloadLabelJob(job.job_id);
      const name =
        job.output?.file_name ??
        (file
          ? file.name.replace(/(\.[^.]+)$/, "-labeled$1")
          : "labeled.bin");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      message.error((e as Error).message);
    }
  }

  const running = Boolean(job && !TERMINAL.has(job.status));
  const succeeded = job?.status === "succeeded";
  const failed = job?.status === "failed";

  return (
    <>
      <PageBanner
        eyebrow="LABELING"
        title="媒体打标"
        sub="上传 MP4 / JPEG / PNG，按 GB 45438-2025 写入 XMP-aigc:AIGC 隐式标识，全程不转码、不重压像素。"
      />

      <section className="section">
        {serviceUp === false && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 18 }}
            message="打标服务不可用"
            description="无法连接 /api/v1（labeling-backend，端口 8002）。请先启动该服务后再试。"
          />
        )}

        <div className="grid-2" style={{ alignItems: "start" }}>
          {/* ===== 左侧：文件 + 参数 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">✎</span>
              <h3>1 · 上传与打标参数</h3>
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
                  单文件 · 视频不转码、图片不重压 · 写入 XMP-aigc 隐式标识
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

              <div style={{ marginTop: 18 }}>
                <div style={FIELD_LABEL}>已有标识策略</div>
                <Select
                  value={policy}
                  onChange={setPolicy}
                  style={{ width: "100%" }}
                  options={[
                    { value: "reject", label: "reject · 已有标识则拒绝（返回 409）" },
                    { value: "replace", label: "replace · 整体移除后重写" },
                  ]}
                />
              </div>

              <div style={{ marginTop: 14 }}>
                <div style={FIELD_LABEL}>内容生产者编号（ContentProducer）</div>
                <Input
                  value={producer}
                  onChange={(e) => setProducer(e.target.value)}
                  placeholder="ORG_..."
                />
              </div>

              <div style={{ marginTop: 14 }}>
                <div style={FIELD_LABEL}>Label 标识类别</div>
                <Select
                  value={label}
                  onChange={setLabel}
                  style={{ width: "100%" }}
                  options={[
                    { value: "1", label: "1 · 人工智能生成内容" },
                    { value: "2", label: "2 · 人工智能合成内容" },
                    { value: "3", label: "3 · 其他" },
                  ]}
                />
              </div>

              <div
                style={{
                  marginTop: 14,
                  fontSize: 12,
                  color: "var(--text-400)",
                  lineHeight: 1.6,
                }}
              >
                ProduceID / PropagateID 将自动生成全局唯一编号；首次写入强制传播者=生产者、传播编号=生产编号。
              </div>

              <button
                className="btn-gold"
                onClick={submit}
                disabled={creating || !file}
                style={{ width: "100%", marginTop: 18, opacity: creating || !file ? 0.6 : 1 }}
              >
                {creating ? "正在创建任务…" : "开始打标"}
              </button>
            </div>
          </div>

          {/* ===== 右侧：任务进度 + 结果 ===== */}
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">↳</span>
              <h3>2 · 任务进度与结果</h3>
            </div>
            <div className="ui-card-body">
              {!job && (
                <div
                  style={{
                    padding: "28px 0",
                    textAlign: "center",
                    color: "var(--text-400)",
                  }}
                >
                  <ThunderboltOutlined
                    style={{ fontSize: 26, color: "var(--gold-600)", marginBottom: 8 }}
                  />
                  <div style={{ fontSize: 13 }}>提交后这里会显示任务状态、进度与打标结果。</div>
                </div>
              )}

              {job && (
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: 8,
                    }}
                  >
                    <div style={{ fontSize: 13, color: "var(--text-400)" }}>任务号</div>
                    <code style={{ fontSize: 12 }}>{job.job_id}</code>
                  </div>

                  <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                    <span className={`status-pill ${STATUS_CLASS[job.status] ?? ""}`}>
                      {job.status}
                    </span>
                    <span className="status-pill warn">{STAGE_ZH[job.stage] ?? job.stage}</span>
                  </div>

                  {running && (
                    <div style={{ marginTop: 16 }}>
                      <Progress percent={job.progress ?? undefined} status="active" />
                      <div style={{ fontSize: 12, color: "var(--text-400)", marginTop: 4 }}>
                        打标进行中，每 1.5s 自动刷新…
                      </div>
                    </div>
                  )}

                  {succeeded && (
                    <div style={{ marginTop: 16 }}>
                      <Alert
                        type="success"
                        showIcon
                        message="打标完成"
                        description={`载体：${job.output?.carrier ?? "-"} · 结果文件 ${(
                          ((job.output?.size_bytes ?? 0) / 1048576).toFixed(2)
                        )} MB`}
                      />
                      <button className="btn-gold" onClick={download} style={{ width: "100%", marginTop: 14 }}>
                        <DownloadOutlined /> 下载打标结果
                      </button>
                      <div className="code-block" style={{ marginTop: 14 }}>
                        <pre style={{ margin: 0 }}>
                          {JSON.stringify(job.embedded_metadata ?? {}, null, 2)}
                        </pre>
                      </div>
                    </div>
                  )}

                  {failed && job.error && (
                    <Alert
                      type="error"
                      showIcon
                      style={{ marginTop: 16 }}
                      message={`打标失败（${job.error.code}）`}
                      description={job.error.message}
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
