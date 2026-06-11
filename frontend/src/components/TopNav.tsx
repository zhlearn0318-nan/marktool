import BrandMark from "./BrandMark";
import { comingSoon } from "../lib/comingSoon";
import { NAV, type View } from "../nav";

/** 顶部导航：品牌徽标 + 导航项（均可进入内容页）+ 语言/控制台。 */
export default function TopNav({
  current,
  onNavigate,
}: {
  current: View;
  onNavigate: (v: View) => void;
}) {
  return (
    <header className="topnav">
      <div className="topnav-inner">
        <div className="brand" onClick={() => onNavigate("home")}>
          <BrandMark />
          <div className="brand-name">
            <span className="cn">源迹 TraceMark</span>
            <span className="en">AIGC COMPLIANCE HUB</span>
          </div>
        </div>

        <nav className="nav-links">
          {NAV.map((n) => (
            <button
              key={n.view}
              className={`nav-link${current === n.view ? " active" : ""}`}
              onClick={() => onNavigate(n.view)}
            >
              {n.label}
            </button>
          ))}
        </nav>

        <div className="nav-right">
          <button className="lang-pill" onClick={() => comingSoon("English 版本")}>
            中 / EN
          </button>
          <button className="btn-ghost-dark" onClick={() => onNavigate("console")}>
            控制台
          </button>
        </div>
      </div>
    </header>
  );
}
