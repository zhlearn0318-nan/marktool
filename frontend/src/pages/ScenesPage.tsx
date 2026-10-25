import { useMemo, useState } from "react";
import { Alert, Input, Select, Switch, Tabs, Upload, message } from "antd";
import PageBanner from "../components/PageBanner";
import ToolSteps from "../components/ToolSteps";
import { downloadComposite, labelInteractiveScene, labelVirtualScene } from "../api";
import type { ClauseCheck, SceneReport } from "../types";

const FIELD: React.CSSProperties = { fontSize: 12.5, color: "var(--text-600)", marginBottom: 8 };
const POSITIONS = [
  { value: "top_left", label: "左上角" },
  { value: "top_right", label: "右上角" },
  { value: "bottom_left", label: "左下角" },
  { value: "bottom_right", label: "右下角" },
  { value: "top_edge", label: "上边" },
  { value: "bottom_edge", label: "下边" },
  { value: "left_edge", label: "左边" },
  { value: "right_edge", label: "右边" },
];
const PLACEMENTS = [
  { value: "near_content", label: "内容附近" },
  { value: "interface_top", label: "界面顶部" },
  { value: "interface_bottom", label: "界面底部" },
  { value: "interface_background", label: "界面背景" },
];

function Checks({ items }: { items?: ClauseCheck[] }) {
  if (!items?.length) return null;
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

function roleFor(file: File): string {
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  if (["txt", "vtt", "srt", "md"].includes(ext)) return "text";
  if (["png", "jpg", "jpeg"].includes(ext)) return "image";
  if (["m4a", "wav", "mp3", "aac"].includes(ext)) return "audio";
  return "video";
}

async function saveZip(report: SceneReport) {
  if (!report.download_url) return;
  const blob = await downloadComposite(report.download_url);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${report.bundle_id ?? "scene"}.zip`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function VirtualPane() {
  const [text, setText] = useState("人工智能生成合成");
  const [position, setPosition] = useState("bottom_right");
  const [producer, setProducer] = useState("ORG_1565201000000016");
  const [during, setDuring] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<SceneReport | null>(null);
  const place = {
    top_left: ["flex-start", "flex-start"],
    top_right: ["flex-end", "flex-start"],
    bottom_left: ["flex-start", "flex-end"],
    bottom_right: ["flex-end", "flex-end"],
    top_edge: ["center", "flex-start"],
    bottom_edge: ["center", "flex-end"],
    left_edge: ["flex-start", "center"],
    right_edge: ["flex-end", "center"],
  }[position] ?? ["flex-end", "flex-end"];
  const [horizontal, vertical] = place;

  async function submit() {
    setBusy(true);
    setReport(null);
    try {
      setReport(await labelVirtualScene({
        producer: producer.trim(),
        label: "1",
        text: text.trim(),
        position,
        duringService: during,
        file,
      }));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid-2" style={{ alignItems: "start" }}>
      <div className="ui-card">
        <div className="ui-card-head"><span className="ico">◇</span><h3>起始画面显式标识</h3></div>
        <div className="ui-card-body">
          <div style={FIELD}>提示文字（须含人工智能/AI，以及生成或合成）</div>
          <Input value={text} onChange={(event) => setText(event.target.value)} />
          <div style={{ marginTop: 14 }}><div style={FIELD}>位置（边或角）</div>
            <Select value={position} onChange={setPosition} style={{ width: "100%" }} options={POSITIONS} />
          </div>
          <div style={{ marginTop: 14 }}><div style={FIELD}>ContentProducer</div>
            <Input value={producer} onChange={(event) => setProducer(event.target.value)} />
          </div>
          <div style={{ marginTop: 14, display: "flex", justifyContent: "space-between" }}>
            <span>持续服务过程中也提示（第 5.5.c 条，可选）</span>
            <Switch checked={during} onChange={setDuring} />
          </div>
          <div style={{ marginTop: 14 }}>
            <Upload maxCount={1} beforeUpload={(item) => { setFile(item); return false; }} showUploadList={false}>
              <button className="btn-ghost-light" type="button">可选：上传起始画面或视频第 1 帧来源</button>
            </Upload>
            <div style={{ marginTop: 8, color: "var(--text-400)" }}>{file ? file.name : "不上传时生成 960×540 起始画面"}</div>
          </div>
          <button className="btn-gold" style={{ width: "100%", marginTop: 18 }} disabled={busy} onClick={submit}>
            {busy ? "写入中…" : "写入显式标识与隐式标识"}
          </button>
        </div>
      </div>
      <div className="ui-card">
        <div className="ui-card-head"><span className="ico">▣</span><h3>预览与校验</h3></div>
        <div className="ui-card-body">
          <div style={{
            height: 220, borderRadius: 12, background: "linear-gradient(#16324f, #2d5a4a)",
            display: "flex", justifyContent: horizontal, alignItems: vertical, padding: 12, position: "relative",
          }}>
            <span style={{ background: "#102033", color: "#fff", padding: "4px 8px", fontSize: 16 }}>{text || "提示"}</span>
          </div>
          <div style={{ marginTop: 8, color: "var(--text-400)", fontSize: 12 }}>
            正式结果以服务端按最短边 5% 绘制并回读的像素为准。这里只预览边角位置。
          </div>
          {report ? (
            <>
              <Alert style={{ marginTop: 14 }} type={report.passed ? "success" : "error"} showIcon
                message={report.passed ? "虚拟场景通过校验" : "虚拟场景未通过校验"} />
              <Checks items={report.checks} />
              <button className="btn-gold" style={{ marginTop: 12 }} onClick={() => saveZip(report).catch((error) => message.error(error.message))}>
                下载场景包
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function InteractivePane() {
  const [text, setText] = useState("AI生成");
  const [placements, setPlacements] = useState<string[]>(["near_content"]);
  const [color, setColor] = useState("#102033");
  const [background, setBackground] = useState("#F4EBD4");
  const [producer, setProducer] = useState("ORG_1565201000000016");
  const [content, setContent] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<SceneReport | null>(null);
  const preview = useMemo(() => ({ text, placements, color, background }), [text, placements, color, background]);

  async function submit() {
    setBusy(true);
    setReport(null);
    try {
      setReport(await labelInteractiveScene({
        producer: producer.trim(),
        label: "1",
        text: text.trim(),
        placements,
        color,
        background,
        content,
        contentRole: content ? roleFor(content) : undefined,
      }));
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid-2" style={{ alignItems: "start" }}>
      <div className="ui-card">
        <div className="ui-card-head"><span className="ico">✎</span><h3>界面显式标识</h3></div>
        <div className="ui-card-body">
          <div style={FIELD}>提示文字</div>
          <Input value={text} onChange={(event) => setText(event.target.value)} />
          <div style={{ marginTop: 14 }}><div style={FIELD}>放置方式（可多选，持续显示）</div>
            <Select mode="multiple" value={placements} onChange={setPlacements} style={{ width: "100%" }} options={PLACEMENTS} />
          </div>
          <div style={{ marginTop: 14, display: "flex", gap: 12 }}>
            <label style={{ flex: 1 }}>文字色 <Input value={color} onChange={(event) => setColor(event.target.value)} /></label>
            <label style={{ flex: 1 }}>背景色 <Input value={background} onChange={(event) => setBackground(event.target.value)} /></label>
          </div>
          <div style={{ marginTop: 14 }}><div style={FIELD}>ContentProducer（仅在附带内容文件时写入隐式标识）</div>
            <Input value={producer} onChange={(event) => setProducer(event.target.value)} />
          </div>
          <div style={{ marginTop: 14 }}>
            <Upload maxCount={1} beforeUpload={(item) => { setContent(item); return false; }} showUploadList={false}>
              <button className="btn-ghost-light" type="button">可选：附带文本 / 图片 / 音频 / 视频</button>
            </Upload>
            <div style={{ marginTop: 8, color: "var(--text-400)" }}>{content ? `${content.name} · ${roleFor(content)}` : "不附带内容文件时只校验界面显式标识"}</div>
          </div>
          <button className="btn-gold" style={{ width: "100%", marginTop: 18 }} disabled={busy} onClick={submit}>
            {busy ? "写入中…" : "生成界面标识并校验"}
          </button>
        </div>
      </div>
      <div className="ui-card">
        <div className="ui-card-head"><span className="ico">▣</span><h3>界面预览</h3></div>
        <div className="ui-card-body">
          <div style={{ position: "relative", minHeight: 260, borderRadius: 12, background: preview.background, color: preview.color, overflow: "hidden", border: "1px solid var(--line)" }}>
            {preview.placements.includes("interface_background") ? (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 28, opacity: 0.35 }}>{preview.text}</div>
            ) : null}
            {preview.placements.includes("interface_top") ? (
              <div style={{ position: "relative", textAlign: "center", padding: 10, fontWeight: 700 }}>{preview.text}</div>
            ) : null}
            <div style={{ margin: 28, padding: 16, background: "rgba(255,255,255,0.72)", borderRadius: 10, position: "relative" }}>
              <div style={{ marginBottom: 8 }}>对话内容显示在这里。</div>
              {preview.placements.includes("near_content") ? <strong>{preview.text}</strong> : null}
            </div>
            {preview.placements.includes("interface_bottom") ? (
              <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, textAlign: "center", padding: 10, fontWeight: 700 }}>{preview.text}</div>
            ) : null}
          </div>
          {report ? (
            <>
              <Alert style={{ marginTop: 14 }} type={report.passed ? "success" : "error"} showIcon
                message={report.passed ? "交互场景通过校验" : "交互场景未通过校验"} />
              <Checks items={report.checks} />
              <button className="btn-gold" style={{ marginTop: 12 }} onClick={() => saveZip(report).catch((error) => message.error(error.message))}>
                下载场景包
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function ScenesPage() {
  return (
    <>
      <PageBanner
        eyebrow="SCENES"
        title="虚拟场景与交互场景"
        sub="按第 5.5、5.6 条添加可感知的文字提示，并在内容文件中写入恰好一份附录 E 隐式标识。已有场景包的复检和修复在合规检测中完成。"
      />
      <section className="section">
        <ToolSteps items={["填写显式提示", "写入并校验", "下载场景包"]} active={0} />
        <p style={{ color: "var(--text-600)" }}>
          要检查已有场景包是否重复、缺字段或显式提示不合规，请到
          <button className="btn-ghost-light" type="button" style={{ marginLeft: 8 }}
            onClick={() => { window.location.hash = "mediaInspect"; }}>
            合规检测
          </button>
        </p>
        <Tabs
          items={[
            { key: "virtual", label: "虚拟场景", children: <VirtualPane /> },
            { key: "interactive", label: "交互场景", children: <InteractivePane /> },
          ]}
        />
      </section>
    </>
  );
}
