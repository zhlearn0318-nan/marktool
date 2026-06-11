import PageBanner from "../components/PageBanner";
import type { View } from "../nav";

const REGS = [
  {
    code: "GB 45438-2025",
    name: "人工智能生成合成内容标识方法",
    region: "中国",
    status: "已支持",
    live: true,
    effective: "2025 年发布",
    points: [
      "显式标识：图片边角“AIGC 生成”文字，字高 ≥ 最短边 5%",
      "隐式标识：元数据中写入符合附录 E 的 AIGC JSON",
      "字段含 Label / ContentProducer / ProduceID 等",
      "覆盖文本、图片、音频、视频四类生成内容",
    ],
  },
  {
    code: "C2PA",
    name: "Coalition for Content Provenance and Authenticity",
    region: "国际联盟",
    status: "互转支持",
    live: true,
    effective: "持续演进",
    points: [
      "基于密码学签名的内容来源与真实性凭证",
      "manifest 记录生成者、编辑链与 digitalSourceType",
      "签名信任链需由原始签发方建立，不可凭空生成",
      "源迹支持与国标 AIGC JSON 的字段双向映射",
    ],
  },
  {
    code: "EU AI Act",
    name: "欧盟人工智能法案 · 透明度义务",
    region: "欧盟",
    status: "规划中",
    live: false,
    effective: "分阶段生效",
    points: [
      "要求对 AI 生成或操纵内容进行清晰披露",
      "深度伪造内容须明确标注为人工生成或篡改",
      "鼓励采用可机读的来源标识技术（如 C2PA）",
      "源迹将于后续版本接入对应规则集",
    ],
  },
];

export default function RegulationsPage({ onNavigate }: { onNavigate: (v: View) => void }) {
  return (
    <>
      <PageBanner
        eyebrow="REGULATION LIBRARY"
        title="法规库"
        sub="集中收录与 AI 内容标识相关的中国与全球法规标准，标注源迹 TraceMark 的支持情况，帮助你快速定位合规要点。"
      />

      <section className="section">
        <div className="grid-3">
          {REGS.map((r) => (
            <div className="info-card" key={r.code} style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="board-idx">{r.region}</span>
                <span className={`board-status ${r.live ? "live" : "soon"}`}>
                  <span className="dot" />
                  {r.status}
                </span>
              </div>
              <h3 style={{ marginTop: 12 }}>{r.code}</h3>
              <div style={{ fontSize: 13, color: "var(--text-600)", marginBottom: 4 }}>{r.name}</div>
              <div style={{ fontSize: 12, color: "var(--text-400)", marginBottom: 14 }}>{r.effective}</div>
              <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-700)", fontSize: 13, lineHeight: 1.85, flex: 1 }}>
                {r.points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="ui-card" style={{ marginTop: 28 }}>
          <div className="ui-card-head"><span className="ico">⇄</span><h3>标识要求速览</h3></div>
          <div className="ui-card-body" style={{ padding: 0 }}>
            <table className="spec-table">
              <thead><tr><th>维度</th><th>中国 GB 45438-2025</th><th>C2PA</th></tr></thead>
              <tbody>
                <tr><td style={{ fontWeight: 700 }}>显式标识</td><td>可见文字 / 语音声明，位置与字高有要求</td><td>不强制，侧重机读凭证</td></tr>
                <tr><td style={{ fontWeight: 700 }}>隐式标识</td><td>元数据 AIGC JSON（附录 E）</td><td>manifest + 数字签名</td></tr>
                <tr><td style={{ fontWeight: 700 }}>溯源</td><td>ContentProducer / Propagator</td><td>claim generator / ingredients</td></tr>
                <tr><td style={{ fontWeight: 700 }}>信任根</td><td>结构合规即可</td><td>需 CA 签名信任链</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <div style={{ textAlign: "center", marginTop: 36 }}>
          <button className="btn-gold" onClick={() => onNavigate("standards")}>查看标准互转演示 →</button>
        </div>
      </section>
    </>
  );
}
