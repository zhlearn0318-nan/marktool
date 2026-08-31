import { useState } from "react";
import { Select, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { detectImage } from "../api";
import type { DetectResponse } from "../types";
import type { View } from "../nav";

const HERO_META = [
  { num: "2", lbl: "法规体系  GB / C2PA" },
  { num: "4", lbl: "内容模态  文图音视" },
  { num: "3", lbl: "图片检测维度" },
  { num: "100%", lbl: "可私有化部署" },
];

const BOARDS: { idx: string; title: string; desc: string; live: boolean; view: View }[] = [
  {
    idx: "板块一",
    title: "打标 / 修复",
    desc: "完成 JPEG/PNG 文件元数据隐式标识的提取、首次打标、合规检测、修复计划与安全执行。",
    live: true,
    view: "metadata",
  },
  {
    idx: "板块二",
    title: "合规检测",
    desc: "逐项核查文件是否按要求标注 —— 找标识、验结构，而非猜测是否 AI 生成。",
    live: true,
    view: "home",
  },
  {
    idx: "板块三",
    title: "检测报告",
    desc: "标识溯源、合规评级、篡改检测、AI 内容参考，四位一体可视化呈现。",
    live: true,
    view: "console",
  },
  {
    idx: "板块四",
    title: "标准互转",
    desc: "C2PA manifest 与国标 AIGC JSON 双向转换，打通出海与入境合规链路。",
    live: false,
    view: "standards",
  },
];

const WHITEPAPER = `源迹 TraceMark · AIGC 标识合规白皮书（摘要）
================================================

一、背景
随着生成式人工智能的普及，AI 生成内容的标识与溯源已成为全球监管焦点。
中国《GB 45438-2025 人工智能生成合成内容标识方法》与国际 C2PA / 欧盟 AI Act
分别提出了显式与隐式标识要求。源迹 TraceMark 旨在提供一站式合规检测、
标识溯源与权威报告能力。

二、四大核心板块
1. 打标 / 修复：发布前按法规自动打标，缺陷自动修复。
2. 合规检测：逐项核查显式与隐式标识，给出合规评级。
3. 检测报告：标识溯源、合规报告、篡改检测、AI 内容参考。
4. 标准互转：GB AIGC JSON 与 C2PA manifest 双向映射。

三、技术架构
前端 React + Ant Design；后端 Python + FastAPI；检测引擎采用插件化架构，
可平滑扩展模态与能力。隐式元数据检测仅依赖 Pillow + jsonschema，开箱即跑。

四、MVP 范围
当前版本聚焦图片模态的「检测 + 报告」，真实实现 XMP → AIGC JSON → 结构校验；
OCR、像素水印、AI 内容概率为可插拔占位，接口已就绪。

（本白皮书为演示版本摘要，最终内容以正式发布为准。）
`;

function downloadWhitepaper() {
  const blob = new Blob([WHITEPAPER], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "源迹TraceMark-合规白皮书.md";
  a.click();
  URL.revokeObjectURL(url);
  message.success("白皮书已开始下载");
}

export default function UploadPage({
  onResult,
  onNavigate,
}: {
  onResult: (r: DetectResponse) => void;
  onNavigate: (v: View) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [reg, setReg] = useState("CN_GB45438");
  const [loading, setLoading] = useState(false);

  async function submit() {
    if (!file) {
      message.warning("请先选择待检测的图片");
      return;
    }
    setLoading(true);
    try {
      onResult(await detectImage(file, reg));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {/* ===== Hero ===== */}
      <section className="hero">
        <div className="hero-inner">
          <div>
            <div className="eyebrow on-dark reveal">源迹 TraceMark · AIGC 标识合规</div>
            <h1 className="hero-title reveal d1">
              让每一份 AI 内容
              <br />
              都<span className="accent"> 有据可循</span>
            </h1>
            <p className="hero-sub reveal d2">
              覆盖中国 GB 45438-2025 与 C2PA 两套法规体系，提供合规检测、标识溯源与权威报告。
              上传一张图片，即刻验证它是否依法标注 AIGC 标识。
            </p>
            <div className="hero-cta reveal d3">
              <button
                className="btn-gold"
                onClick={() =>
                  document.getElementById("boards")?.scrollIntoView({ behavior: "smooth" })
                }
              >
                了解四大核心能力
              </button>
              <button className="btn-ghost-dark" onClick={downloadWhitepaper}>
                下载合规白皮书
              </button>
            </div>
            <div className="hero-meta reveal d4">
              {HERO_META.map((m) => (
                <div key={m.lbl}>
                  <div className="num serif">{m.num}</div>
                  <div className="lbl">{m.lbl}</div>
                </div>
              ))}
            </div>
          </div>

          {/* detection console */}
          <div className="hero-panel reveal d2">
            <div className="eyebrow">合规检测控制台</div>
            <h3
              className="serif"
              style={{ color: "var(--ink-900)", fontSize: 22, margin: "8px 0 18px", fontWeight: 700 }}
            >
              即刻检测一张图片
            </h3>

            <Upload.Dragger
              beforeUpload={(f) => {
                setFile(f);
                return false;
              }}
              maxCount={1}
              accept="image/*"
              showUploadList={false}
            >
              <p className="ant-upload-drag-icon" style={{ color: "var(--gold-600)" }}>
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽图片到此处</p>
              <p className="ant-upload-hint">支持 PNG / JPEG · 单张检测</p>
            </Upload.Dragger>

            {file ? (
              <div style={{ marginTop: 12, fontSize: 13, color: "var(--gold-700)", display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: "var(--text-400)" }}>已选择</span>
                <strong style={{ color: "var(--ink-900)" }}>{file.name}</strong>
              </div>
            ) : null}

            <div style={{ marginTop: 18 }}>
              <div style={{ fontSize: 12.5, color: "var(--text-600)", marginBottom: 8, letterSpacing: "0.04em" }}>
                目标法规
              </div>
              <Select
                value={reg}
                onChange={setReg}
                style={{ width: "100%" }}
                options={[
                  { value: "CN_GB45438", label: "中国国标 GB 45438-2025" },
                  { value: "EU_C2PA", label: "欧盟 EU AI Act / C2PA（规划中）", disabled: true },
                ]}
              />
            </div>

            <button
              className="btn-gold"
              onClick={submit}
              disabled={loading}
              style={{ width: "100%", marginTop: 18, opacity: loading ? 0.7 : 1 }}
            >
              {loading ? "检测中…" : "开始合规检测"}
            </button>
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-400)", textAlign: "center" }}>
              检测在服务端完成，图片不对外公开
            </div>
          </div>
        </div>
      </section>

      {/* ===== 四板块 ===== */}
      <section className="section" id="boards">
        <div className="section-head">
          <div className="eyebrow">PRODUCT ARCHITECTURE</div>
          <h2>四大核心板块</h2>
          <hr className="hairline-gold center-hairline" style={{ marginTop: 16 }} />
          <p>
            打标修复、合规检测、检测报告、标准互转 —— 覆盖 AI 内容标识合规全链路。
            当前 MVP 已上线<strong>板块二（检测）</strong>与<strong>板块三（报告）</strong>的图片模态。
          </p>
        </div>

        <div className="board-grid">
          {BOARDS.map((b, i) => (
            <div
              key={b.idx}
              className={`board-card reveal d${i + 1}`}
              style={{ cursor: "pointer" }}
              onClick={() => onNavigate(b.view)}
            >
              <div className="board-idx">{b.idx}</div>
              <h3>{b.title}</h3>
              <p>{b.desc}</p>
              <span className={`board-status ${b.live ? "live" : "soon"}`}>
                <span className="dot" />
                {b.live ? "已上线" : "规划中"}
              </span>
              <div style={{ marginTop: 14, fontSize: 13, fontWeight: 700, color: "var(--gold-700)" }}>
                了解更多 →
              </div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
