import { Progress, Table, Tag } from "antd";
import PageBanner from "../components/PageBanner";
import type { View } from "../nav";

const RATING_COLOR: Record<string, string> = { A: "green", B: "blue", C: "orange", 不合规: "red" };

const ROWS = [
  { key: 1, file: "poster_ai_v3.png", time: "2026-06-12 10:42", rating: "A", reg: "GB 45438" },
  { key: 2, file: "banner_campaign.jpg", time: "2026-06-12 10:31", rating: "B", reg: "GB 45438" },
  { key: 3, file: "product_render.png", time: "2026-06-12 09:58", rating: "不合规", reg: "GB 45438" },
  { key: 4, file: "social_card_07.png", time: "2026-06-12 09:33", rating: "C", reg: "GB 45438" },
  { key: 5, file: "hero_generated.jpg", time: "2026-06-12 08:50", rating: "A", reg: "GB 45438" },
  { key: 6, file: "thumbnail_ai.png", time: "2026-06-11 18:21", rating: "B", reg: "GB 45438" },
];

const DIST = [
  { label: "A · 完全合规", value: 38, color: "#2c8f60" },
  { label: "B · 基本合规", value: 31, color: "#1c4271" },
  { label: "C · 需整改", value: 17, color: "#b27d22" },
  { label: "不合规", value: 14, color: "#b6362a" },
];

export default function ConsolePage({ onNavigate }: { onNavigate: (v: View) => void }) {
  return (
    <>
      <PageBanner
        eyebrow="CONSOLE"
        title="控制台"
        sub="集中查看检测概况、合规分布与历史记录。以下为演示数据，便于预览企业级控制台形态。"
      />

      <section className="section">
        <div className="kpi-grid" style={{ marginBottom: 18 }}>
          <div className="kpi"><div className="kpi-num">128</div><div className="kpi-lbl">今日检测</div></div>
          <div className="kpi pass"><div className="kpi-num">76%</div><div className="kpi-lbl">今日合规率</div></div>
          <div className="kpi warn"><div className="kpi-num">23</div><div className="kpi-lbl">待整改</div></div>
          <div className="kpi"><div className="kpi-num">4,892</div><div className="kpi-lbl">累计检测</div></div>
        </div>

        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="ui-card" style={{ gridColumn: "span 1" }}>
            <div className="ui-card-head"><span className="ico">▤</span><h3>最近检测记录</h3></div>
            <div className="ui-card-body" style={{ padding: 8 }}>
              <Table
                size="middle"
                pagination={false}
                dataSource={ROWS}
                columns={[
                  { title: "文件", dataIndex: "file", key: "file" },
                  { title: "时间", dataIndex: "time", key: "time", responsive: ["md"] },
                  { title: "法规", dataIndex: "reg", key: "reg", responsive: ["lg"] },
                  {
                    title: "评级",
                    dataIndex: "rating",
                    key: "rating",
                    render: (r: string) => <Tag color={RATING_COLOR[r] ?? "default"}>{r}</Tag>,
                  },
                ]}
              />
            </div>
          </div>

          <div className="ui-card">
            <div className="ui-card-head"><span className="ico">◔</span><h3>合规评级分布</h3></div>
            <div className="ui-card-body">
              {DIST.map((d) => (
                <div key={d.label} style={{ marginBottom: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 4 }}>
                    <span style={{ color: "var(--text-700)", fontWeight: 600 }}>{d.label}</span>
                    <span style={{ color: "var(--text-400)" }}>{d.value}%</span>
                  </div>
                  <Progress percent={d.value} showInfo={false} strokeColor={d.color} trailColor="#eee7d8" />
                </div>
              ))}
              <div style={{ marginTop: 22, paddingTop: 18, borderTop: "1px solid var(--line)" }}>
                <div style={{ fontSize: 13, color: "var(--text-700)", fontWeight: 600, marginBottom: 6 }}>本月配额</div>
                <Progress percent={49} strokeColor="#b89446" trailColor="#eee7d8" />
                <div style={{ fontSize: 12.5, color: "var(--text-400)", marginTop: 4 }}>已用 4,892 / 10,000 次</div>
              </div>
            </div>
          </div>
        </div>

        <div style={{ textAlign: "center", marginTop: 36 }}>
          <button className="btn-gold" onClick={() => onNavigate("home")}>发起新的检测 →</button>
        </div>
      </section>
    </>
  );
}
