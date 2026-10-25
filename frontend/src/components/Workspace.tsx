import type { ReactNode } from "react";
import Icon, { type IconName } from "./Icon";
import type { View } from "../nav";
const groups: {
  title: string;
  items: { view: View; label: string; icon: IconName; soon?: boolean }[];
}[] = [
  {
    title: "工作空间",
    items: [
      { view: "console", label: "工作台概览", icon: "grid" },
      { view: "labeling", label: "元数据标注", icon: "label" },
      { view: "multimodal", label: "多模态", icon: "layers" },
      { view: "scenes", label: "场景标识", icon: "video" },
      { view: "mediaInspect", label: "合规检测", icon: "scan" },
      { view: "mediaRepair", label: "元数据修复", icon: "repair" },
    ],
  },
  {
    title: "更多工具",
    items: [
      { view: "home", label: "图片报告（旧版）", icon: "image" },
      { view: "standards", label: "标准互转", icon: "layers", soon: true },
    ],
  },
  {
    title: "参考与支持",
    items: [
      { view: "overview", label: "平台能力", icon: "shield" },
      { view: "regulations", label: "标准与法规", icon: "book" },
      { view: "api", label: "开发者文档", icon: "code" },
      { view: "help", label: "帮助中心", icon: "help" },
    ],
  },
];
export default function Workspace({
  view,
  onNavigate,
  children,
}: {
  view: View;
  onNavigate: (view: View) => void;
  children: ReactNode;
}) {
  const label =
    groups.flatMap((g) => g.items).find((item) => item.view === view)?.label ??
    "检测报告";
  return (
    <div className="workspace">
      <aside className="workspace-sidebar">
        <div className="workspace-heading">
          <span className="workspace-monogram">T</span>
          <div>
            <strong>源迹工作空间</strong>
            <span>内容标识管理</span>
          </div>
        </div>
        <nav aria-label="工作台导航">
          {groups.map((group) => (
            <div className="sidebar-group" key={group.title}>
              <p>{group.title}</p>
              {group.items.map((item) => (
                <button
                  key={item.view}
                  className={
                    view === item.view ||
                    (view === "report" && item.view === "home")
                      ? "active"
                      : ""
                  }
                  aria-current={view === item.view ? "page" : undefined}
                  onClick={() => onNavigate(item.view)}
                >
                  <Icon name={item.icon} size={18} />
                  <span>{item.label}</span>
                  {item.soon && <small>规划</small>}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-help">
          <Icon name="help" />
          <strong>第一次使用源迹？</strong>
          <p>跟随指南，完成你的第一次内容核查。</p>
          <button onClick={() => onNavigate("help")}>
            查看操作指南 <Icon name="arrow" size={16} />
          </button>
        </div>
      </aside>
      <div className="workspace-main">
        <div className="workspace-breadcrumb">
          <span>工作空间</span>
          <Icon name="chevron" size={13} />
          <span>{label}</span>
          <span className="workspace-local">
            <i />
            本地工作空间
          </span>
        </div>
        <nav className="mobile-tool-nav" aria-label="快捷工具">
          {groups[0].items.map((item) => (
            <button
              key={item.view}
              className={view === item.view ? "active" : ""}
              onClick={() => onNavigate(item.view)}
            >
              <Icon name={item.icon} size={16} />
              {item.label}
            </button>
          ))}
          <select
            aria-label="更多工具与帮助"
            value={
              groups[0].items.some((item) => item.view === view)
                ? ""
                : view === "report"
                  ? "home"
                  : view
            }
            onChange={(e) => onNavigate(e.target.value as View)}
          >
            <option value="" disabled>
              更多
            </option>
            {groups
              .slice(1)
              .flatMap((group) => group.items)
              .map((item) => (
                <option value={item.view} key={item.view}>
                  {item.label}
                </option>
              ))}
          </select>
        </nav>
        <div className={`workspace-page view-${view}`} key={view}>
          {children}
        </div>
        <div className="workspace-end">
          <span>源迹 TraceMark · 让可信，随内容同行</span>
          <button onClick={() => onNavigate("help")}>
            需要帮助 <Icon name="arrow" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
