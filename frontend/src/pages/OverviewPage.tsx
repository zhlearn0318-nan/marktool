import { Timeline } from "antd";
import PageBanner from "../components/PageBanner";
import type { View } from "../nav";

const CAPS = [
  { idx: "板块一", t: "打标 / 修复", d: "按目标法规自动写入显式文字、隐式元数据与抗压缩水印；对缺字段、格式错、水印丢失的内容自动修复。" },
  { idx: "板块二", t: "合规检测", d: "逐项核查文件是否依法标注 —— 找标识、验结构，而非猜测是否 AI 生成。覆盖显式与隐式两类标识。" },
  { idx: "板块三", t: "检测报告", d: "标识溯源链路、逐项合规评级与整改建议、篡改一致性判断、AI 内容概率参考，四位一体输出。" },
  { idx: "板块四", t: "标准互转", d: "C2PA manifest 与国标 AIGC JSON 双向映射，透明告知签名信任链限制，打通出海与入境。" },
];

const STACK = [
  ["前端", "React + Ant Design", "组件化控制台与可视化报告"],
  ["后端", "Python + FastAPI", "异步处理，REST API"],
  ["任务调度", "Celery + Redis", "批量与长任务编排（规划）"],
  ["检测引擎", "模块化插件架构", "按模态/能力热插拔扩展"],
  ["元数据", "Pillow / ExifTool", "XMP / EXIF / ID3 / MP4 读写"],
  ["OCR · ASR", "PaddleOCR · Whisper", "显式文字与语音声明识别（规划）"],
  ["水印", "TrustMark · AudioSeal", "图片与音频抗压缩水印（规划）"],
  ["标准与校验", "c2pa-rs · jsonschema", "C2PA 验签与附录E结构校验"],
];

const MATRIX = [
  ["图片", "✅ 已上线", "✅ 元数据(真实)", "⚙ 接口就绪"],
  ["文本", "⚙ 规划中", "⚙ 规划中", "—"],
  ["音频", "⚙ 规划中", "⚙ 规划中", "⚙ 规划中"],
  ["视频", "⚙ 规划中", "⚙ 规划中", "—"],
];

export default function OverviewPage({ onNavigate }: { onNavigate: (v: View) => void }) {
  return (
    <>
      <PageBanner
        eyebrow="PLATFORM OVERVIEW"
        title="一个开源、可私有化的 AI 内容标识合规中枢"
        sub="源迹 TraceMark 覆盖中国 GB 45438-2025 与欧盟 / C2PA 两套法规体系，提供打标修复、合规检测、报告生成、标准互转四大能力，支持文本、图片、音频、视频四种模态。"
      />

      <section className="section">
        <div className="section-head">
          <div className="eyebrow">CORE CAPABILITIES</div>
          <h2>四大核心能力</h2>
          <hr className="hairline-gold center-hairline" style={{ marginTop: 16 }} />
        </div>
        <div className="grid-2">
          {CAPS.map((c) => (
            <div className="info-card" key={c.idx}>
              <div className="board-idx">{c.idx}</div>
              <h3 style={{ marginTop: 8 }}>{c.t}</h3>
              <div className="muted">{c.d}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="ui-card">
            <div className="ui-card-head"><span className="ico">◫</span><h3>技术架构</h3></div>
            <div className="ui-card-body" style={{ padding: 0 }}>
              <table className="spec-table">
                <thead><tr><th>层级</th><th>选型</th><th>说明</th></tr></thead>
                <tbody>
                  {STACK.map((r) => (
                    <tr key={r[0]}><td style={{ fontWeight: 700 }}>{r[0]}</td><td>{r[1]}</td><td style={{ color: "var(--text-600)" }}>{r[2]}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="ui-card">
            <div className="ui-card-head"><span className="ico">▦</span><h3>模态 × 能力支持矩阵</h3></div>
            <div className="ui-card-body" style={{ padding: 0 }}>
              <table className="spec-table">
                <thead><tr><th>模态</th><th>状态</th><th>元数据检测</th><th>标准互转</th></tr></thead>
                <tbody>
                  {MATRIX.map((r) => (
                    <tr key={r[0]}><td style={{ fontWeight: 700 }}>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>
                  ))}
                </tbody>
              </table>
              <div style={{ padding: "14px 16px", fontSize: 12.5, color: "var(--text-400)" }}>
                ✅ 已上线 · ⚙ 规划中。当前 MVP 聚焦图片模态的真实元数据检测与报告。
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="section" style={{ paddingTop: 0 }}>
        <div className="ui-card">
          <div className="ui-card-head"><span className="ico">◷</span><h3>研发路线</h3></div>
          <div className="ui-card-body">
            <Timeline
              items={[
                { color: "#2c8f60", children: <RoadItem t="一期 · 第 1–3 月" d="图片模态板块二（显式 OCR + 元数据 + C2PA 验签 + 水印检测）与板块三基础报告，Web 控制台。" tag="可运行原型 · 当前阶段" /> },
                { color: "#b89446", children: <RoadItem t="二期 · 第 4–6 月" d="板块一图片打标；音频 + 视频模态（检测 + 打标）；开放 REST API。" tag="全模态检测 + 打标" /> },
                { color: "#b89446", children: <RoadItem t="三期 · 第 7–9 月" d="板块四 C2PA ↔ 国标互转；文本模态覆盖；报告增强（溯源链路图、AI 疑似度）；多法规打标。" tag="四板块完整功能" /> },
                { color: "#8d8675", children: <RoadItem t="四期 · 第 10–12 月" d="生成工具插件（ComfyUI 等）；批量处理；区块链存证。" tag="生态集成 · 商业化" /> },
              ]}
            />
          </div>
        </div>

        <div style={{ textAlign: "center", marginTop: 40 }}>
          <button className="btn-gold" onClick={() => onNavigate("home")}>立即体验图片检测 →</button>
        </div>
      </section>
    </>
  );
}

function RoadItem({ t, d, tag }: { t: string; d: string; tag: string }) {
  return (
    <div style={{ paddingBottom: 8 }}>
      <div style={{ fontFamily: "var(--font-serif)", fontWeight: 700, fontSize: 16, color: "var(--text-900)" }}>{t}</div>
      <div style={{ color: "var(--text-600)", fontSize: 13.5, lineHeight: 1.7, margin: "4px 0 8px" }}>{d}</div>
      <span className="tag-line">{tag}</span>
    </div>
  );
}
