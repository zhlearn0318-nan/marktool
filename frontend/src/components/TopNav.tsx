import { useEffect, useState } from "react";
import BrandMark from "./BrandMark";
import Icon from "./Icon";
import type { View } from "../nav";
const links: { label: string; view: View }[] = [
  { label: "首页", view: "welcome" },
  { label: "产品与能力", view: "overview" },
  { label: "标准与法规", view: "regulations" },
  { label: "使用指南", view: "help" },
];
export default function TopNav({
  current,
  onNavigate,
}: {
  current: View;
  onNavigate: (view: View) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [current]);
  return (
    <header className="site-header">
      <div className="site-header-inner">
        <button
          className="site-brand"
          onClick={() => onNavigate("welcome")}
          aria-label="源迹 TraceMark 首页"
        >
          <BrandMark size={36} />
          <span>
            源迹 <b>TraceMark</b>
          </span>
        </button>
        <nav className="site-nav" aria-label="主导航">
          {links.map((link) => (
            <button
              className={current === link.view ? "active" : ""}
              onClick={() => onNavigate(link.view)}
              key={link.view}
              aria-current={current === link.view ? "page" : undefined}
            >
              {link.label}
            </button>
          ))}
        </nav>
        <div className="header-actions">
          <button className="header-docs" onClick={() => onNavigate("api")}>
            开发者文档
          </button>
          <button
            className="tm-button primary small"
            onClick={() => onNavigate("console")}
          >
            进入工作台 <Icon name="arrow" size={16} />
          </button>
          <button
            className="mobile-menu-button"
            onClick={() => setOpen(!open)}
            aria-label={open ? "关闭导航" : "展开导航"}
            aria-expanded={open}
            aria-controls="mobile-nav"
          >
            <Icon name={open ? "close" : "menu"} />
          </button>
        </div>
      </div>
      {open && (
        <nav
          id="mobile-nav"
          className="mobile-site-nav"
          aria-label="移动端导航"
        >
          {links.concat([{ label: "开发者文档", view: "api" }]).map((link) => (
            <button onClick={() => onNavigate(link.view)} key={link.view}>
              {link.label}
              <Icon name="chevron" size={16} />
            </button>
          ))}
        </nav>
      )}
    </header>
  );
}
