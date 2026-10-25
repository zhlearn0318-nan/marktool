import { useState } from "react";
import { Alert, Input, Select, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import PageBanner from "../components/PageBanner";
import ToolSteps from "../components/ToolSteps";
import { downloadComposite, labelMultimodal, verifyMultimodal } from "../api";
import type { ClauseCheck, SceneReport } from "../types";

const FIELD: React.CSSProperties = {
  fontSize: 12.5,
  color: "var(--text-600)",
  marginBottom: 8,
};

function Checks({ items }: { items: ClauseCheck[] }) {
  return (
    <ul style={{ margin: "8px 0 0", paddingLeft: 18, lineHeight: 1.7 }}>
      {items.map((item) => (
        <li key={item.id} style={{ color: item.passed ? "var(--text-700)" : "var(--vermilion)" }}>
          <strong>{item.clause}</strong> · {item.message}
        </li>
      ))}
    </ul>
  );
}

export default function MultimodalPage() {
  const [file, setFile] = useState<File | null>(null);
  const [verifyFile, setVerifyFile] = useState<File | null>(null);
  const [producer, setProducer] = useState("ORG_1565201000000016");
  const [label, setLabel] = useState("1");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<SceneReport | null>(null);
  const [verifyReport, setVerifyReport] = useState<SceneReport | null>(null);

  async function submit() {
    if (!file) {
      message.warning("请先选择含多种模态的视频");
      return;
    }
    setBusy(true);
    setReport(null);
    try {
      setReport(await labelMultimodal(file, producer.trim(), label));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    if (!report?.download_url) return;
    try {
      const blob = await downloadComposite(report.download_url);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${report.bundle_id ?? "multimodal"}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      message.error((error as Error).message);
    }
  }

  async function verify() {
    if (!verifyFile) return;
    setBusy(true);
    setVerifyReport(null);
    try {
      setVerifyReport(await verifyMultimodal(verifyFile));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageBanner
        eyebrow="MULTIMODAL"
        title="多模态标识"
        sub="把视频拆成画面、音频和字幕，分别写入附录 E 隐式标识，再在重组文件上只保留一份。"
      />
      <section className="section">
        <ToolSteps
          items={["上传复合内容", "拆分打标并校验", "下载或复核"]}
          active={report?.passed ? 2 : busy ? 1 : 0}
        />
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">▣</span>
              <h3>1 · 拆分并打标</h3>
            </div>
            <div className="ui-card-body">
              <Upload.Dragger
                accept=".mp4,video/mp4"
                maxCount={1}
                beforeUpload={(item) => {
                  setFile(item);
                  return false;
                }}
                showUploadList={false}
              >
                <p className="ant-upload-drag-icon" style={{ color: "var(--gold-600)" }}>
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">点击或拖拽含音频、字幕的 MP4</p>
                <p className="ant-upload-hint">至少两种模态。只有画面的视频会返回 NOT_MULTIMODAL。</p>
              </Upload.Dragger>
              {file ? <div style={{ marginTop: 12 }}><strong>{file.name}</strong></div> : null}
              <div style={{ marginTop: 16 }}>
                <div style={FIELD}>ContentProducer</div>
                <Input value={producer} onChange={(event) => setProducer(event.target.value)} />
              </div>
              <div style={{ marginTop: 14 }}>
                <div style={FIELD}>Label</div>
                <Select
                  value={label}
                  onChange={setLabel}
                  style={{ width: "100%" }}
                  options={[
                    { value: "1", label: "1 · 属于人工智能生成合成内容" },
                    { value: "2", label: "2 · 可能为人工智能生成合成内容" },
                    { value: "3", label: "3 · 疑似为人工智能生成合成内容" },
                  ]}
                />
              </div>
              <button className="btn-gold" style={{ width: "100%", marginTop: 18 }} disabled={busy} onClick={submit}>
                {busy ? "处理中…" : "拆分、打标并校验"}
              </button>
            </div>
          </div>

          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">✓</span>
              <h3>2 · 成分与重组结果</h3>
            </div>
            <div className="ui-card-body">
              {!report ? (
                <div style={{ color: "var(--text-400)" }}>提交后在这里查看每个成分和重组文件的校验。</div>
              ) : (
                <>
                  <Alert
                    type={report.passed ? "success" : "error"}
                    showIcon
                    message={report.passed ? "多模态标识通过校验" : "多模态标识未通过校验"}
                    description={`组合 ${report.combination ?? "-"} · 重组形态 ${report.recombined_kind ?? "-"}`}
                  />
                  {(report.notes ?? []).map((note) => (
                    <div key={note} style={{ marginTop: 8, color: "var(--text-600)" }}>{note}</div>
                  ))}
                  {(report.components ?? []).map((component) => (
                    <div key={component.role} style={{ marginTop: 14 }}>
                      <strong>{component.role}</strong>
                      <span style={{ color: "var(--text-400)" }}> · {component.carrier} · {component.produce_id}</span>
                      <Checks items={component.checks ?? []} />
                    </div>
                  ))}
                  {report.recombined ? (
                    <div style={{ marginTop: 14 }}>
                      <strong>重组文件</strong>
                      <span style={{ color: "var(--text-400)" }}> · {report.recombined.file_name}</span>
                      <Checks items={report.recombined.checks ?? []} />
                    </div>
                  ) : null}
                  {report.download_url ? (
                    <button className="btn-gold" style={{ width: "100%", marginTop: 16 }} onClick={download}>
                      下载结果包
                    </button>
                  ) : null}
                </>
              )}
            </div>
          </div>
        </div>

        <div className="ui-card" style={{ marginTop: 22 }}>
          <div className="ui-card-head">
            <span className="ico">⌕</span>
            <h3>解析与校验已有结果</h3>
          </div>
          <div className="ui-card-body">
            <Upload
              maxCount={1}
              beforeUpload={(item) => {
                setVerifyFile(item);
                return false;
              }}
              showUploadList={false}
            >
              <button className="btn-ghost-light" type="button">选择结果包或媒体文件</button>
            </Upload>
            {verifyFile ? <span style={{ marginLeft: 12 }}>{verifyFile.name}</span> : null}
            <button className="btn-gold" style={{ marginLeft: 12 }} disabled={!verifyFile || busy} onClick={verify}>
              校验
            </button>
            {verifyReport ? (
              <Alert
                style={{ marginTop: 14 }}
                type={verifyReport.passed ? "success" : "warning"}
                showIcon
                message={verifyReport.passed ? "校验通过" : "校验未通过"}
              />
            ) : null}
          </div>
        </div>
      </section>
    </>
  );
}
