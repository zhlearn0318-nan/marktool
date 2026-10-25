import BrandMark from "./BrandMark";
import type { View } from "../nav";
export default function Footer({
  onNavigate,
}: {
  onNavigate: (view: View) => void;
}) {
  return (
    <footer className="brand-footer">
      <div className="brand-footer-top">
        <div>
          <button className="site-brand" onClick={() => onNavigate("welcome")}>
            <BrandMark />
            <span>
              源迹 <b>TraceMark</b>
            </span>
          </button>
          <p>让每一份创作，都有迹可循。</p>
        </div>
        <div>
          <h3>产品</h3>
          <button onClick={() => onNavigate("labeling")}>元数据标注</button>
          <button onClick={() => onNavigate("mediaInspect")}>合规检测</button>
          <button onClick={() => onNavigate("mediaRepair")}>元数据修复</button>
        </div>
        <div>
          <h3>资源</h3>
          <button onClick={() => onNavigate("regulations")}>标准与法规</button>
          <button onClick={() => onNavigate("api")}>开发者文档</button>
          <button onClick={() => onNavigate("help")}>帮助中心</button>
        </div>
        <div>
          <h3>源迹</h3>
          <button onClick={() => onNavigate("overview")}>平台能力</button>
          <a
            href="https://github.com/zhlearn0318-nan/marktool"
            target="_blank"
            rel="noreferrer"
          >
            开源项目 ↗
          </a>
        </div>
      </div>
      <div className="brand-footer-bottom">
        <span>© {new Date().getFullYear()} TraceMark. 源迹</span>
        <span>内容有来源，信任有依据。</span>
      </div>
    </footer>
  );
}
