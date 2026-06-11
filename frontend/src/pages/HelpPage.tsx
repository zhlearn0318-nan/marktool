import { Collapse, Steps } from "antd";
import PageBanner from "../components/PageBanner";
import type { View } from "../nav";

const FAQ = [
  {
    q: "源迹 TraceMark 是“检测内容是否由 AI 生成”吗？",
    a: "不是。核心能力是“检测内容是否依法标注了 AIGC 标识” —— 在文件里找标识、验证其结构是否合规，而非推测内容本身是否由 AI 生成。AI 内容概率仅作兜底参考，且默认未启用。",
  },
  {
    q: "目前支持哪些文件与法规？",
    a: "MVP 阶段支持图片模态（PNG / JPEG）的合规检测与报告，法规聚焦中国国标 GB 45438-2025 的隐式元数据标识。音视频、文本以及打标、互转能力将按研发路线陆续上线。",
  },
  {
    q: "隐式标识是怎么检测的？",
    a: "读取图片 XMP 元数据中的 AIGC JSON，并对照 GB 45438 附录 E 的结构进行 jsonschema 校验：缺失记为缺失，结构不规范记为待整改，完整合规则通过。该链路真实可跑，仅依赖 Pillow 与 jsonschema。",
  },
  {
    q: "显式文字与水印检测为什么显示“未启用”？",
    a: "显式文字（OCR）与像素水印（TrustMark）属于重型能力，已在引擎中预留统一插件接口，MVP 以占位形式呈现，可在不改动调用方的前提下平滑接入。",
  },
  {
    q: "可以私有化部署吗？",
    a: "可以。后端为 Python + FastAPI，前端为静态资源，检测在服务端完成、图片不对外公开，适合私有化与离线环境部署。",
  },
];

export default function HelpPage({ onNavigate }: { onNavigate: (v: View) => void }) {
  return (
    <>
      <PageBanner
        eyebrow="HELP CENTER"
        title="帮助中心"
        sub="快速上手源迹 TraceMark，了解检测原理、支持范围与常见问题。"
      />

      <section className="section">
        <div className="ui-card" style={{ marginBottom: 24 }}>
          <div className="ui-card-head"><span className="ico">◐</span><h3>三步完成一次检测</h3></div>
          <div className="ui-card-body">
            <Steps
              direction="horizontal"
              responsive
              current={-1}
              items={[
                { title: "上传图片", description: "在合规检测控制台拖入或选择一张 PNG / JPEG。" },
                { title: "选择法规", description: "默认中国国标 GB 45438-2025，点击开始检测。" },
                { title: "查看报告", description: "获得评级、溯源、整改建议与原始元数据。" },
              ]}
            />
          </div>
        </div>

        <div className="section-head" style={{ marginBottom: 24 }}>
          <div className="eyebrow">FAQ</div>
          <h2>常见问题</h2>
          <hr className="hairline-gold center-hairline" style={{ marginTop: 16 }} />
        </div>

        <Collapse
          accordion
          items={FAQ.map((f, i) => ({
            key: String(i),
            label: <span style={{ fontWeight: 700, color: "var(--text-900)" }}>{f.q}</span>,
            children: <div style={{ color: "var(--text-600)", fontSize: 14, lineHeight: 1.8 }}>{f.a}</div>,
          }))}
        />

        <div
          className="ui-card"
          style={{ marginTop: 28, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 16, padding: 28 }}
        >
          <div>
            <h3 style={{ fontFamily: "var(--font-serif)", margin: 0, fontSize: 20, color: "var(--text-900)" }}>
              准备好开始了吗？
            </h3>
            <div style={{ color: "var(--text-600)", marginTop: 6 }}>上传一张图片，30 秒看到完整合规报告。</div>
          </div>
          <button className="btn-gold" onClick={() => onNavigate("home")}>前往合规检测 →</button>
        </div>
      </section>
    </>
  );
}
