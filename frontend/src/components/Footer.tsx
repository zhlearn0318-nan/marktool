import BrandMark from "./BrandMark";
import type { View } from "../nav";

const COLUMNS: { title: string; items: { label: string; view: View }[] }[] = [
  {
    title: "产品",
    items: [
      { label: "合规检测", view: "home" },
      { label: "检测报告", view: "console" },
      { label: "标准互转", view: "standards" },
      { label: "平台概览", view: "overview" },
    ],
  },
  {
    title: "法规",
    items: [
      { label: "GB 45438-2025", view: "regulations" },
      { label: "C2PA", view: "regulations" },
      { label: "EU AI Act", view: "regulations" },
      { label: "标准对照表", view: "standards" },
    ],
  },
  {
    title: "资源",
    items: [
      { label: "开发者文档", view: "api" },
      { label: "API 参考", view: "api" },
      { label: "帮助中心", view: "help" },
      { label: "平台概览", view: "overview" },
    ],
  },
];

/** 页脚：品牌信息 + 分栏导航（均可跳转内容页）+ 版权条。 */
export default function Footer({ onNavigate }: { onNavigate: (v: View) => void }) {
  return (
    <footer className="footer">
      <div className="footer-inner">
        <div>
          <div className="brand" style={{ cursor: "pointer" }} onClick={() => onNavigate("home")}>
            <BrandMark size={34} />
            <div className="brand-name">
              <span className="cn">源迹 TraceMark</span>
              <span className="en">AIGC COMPLIANCE HUB</span>
            </div>
          </div>
          <p
            style={{
              marginTop: 16,
              maxWidth: 280,
              fontSize: 13.5,
              lineHeight: 1.8,
              color: "var(--on-dark-dim)",
            }}
          >
            面向中国与全球 AI 内容标识法规的合规检测与报告平台，支持私有化部署。
          </p>
        </div>

        {COLUMNS.map((col) => (
          <div key={col.title}>
            <h4>{col.title}</h4>
            {col.items.map((it, i) => (
              <a key={`${it.label}-${i}`} onClick={() => onNavigate(it.view)}>
                {it.label}
              </a>
            ))}
          </div>
        ))}
      </div>

      <div className="footer-bar">
        <span>© 2026 源迹 TraceMark · AIGC Compliance Hub</span>
        <span>本平台为 MVP 演示版本 · 检测结果仅供参考</span>
      </div>
    </footer>
  );
}
