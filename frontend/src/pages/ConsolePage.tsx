import { useEffect, useState } from "react";
import PageBanner from "../components/PageBanner";
import Icon, { type IconName } from "../components/Icon";
import { getHealth } from "../api";
import type { View } from "../nav";
const tasks: {
  title: string;
  description: string;
  icon: IconName;
  view: View;
  action: string;
}[] = [
  {
    title: "添加内容标识",
    description: "为图片或视频写入来源信息与 AIGC 元数据。",
    icon: "label",
    view: "labeling",
    action: "开始标注",
  },
  {
    title: "检查内容合规",
    description: "核查文件标识，查看字段问题与处理建议。",
    icon: "scan",
    view: "mediaInspect",
    action: "开始检测",
  },
  {
    title: "修复图片标识",
    description: "预览修复计划，确认后生成新的图片文件。",
    icon: "repair",
    view: "mediaRepair",
    action: "生成计划",
  },
];
export default function ConsolePage({
  onNavigate,
  hasReport,
}: {
  onNavigate: (view: View) => void;
  hasReport: boolean;
}) {
  const [status, setStatus] = useState<"loading" | "ok" | "error">("loading");
  async function refresh() {
    setStatus("loading");
    try {
      const h = await getHealth();
      setStatus(h.status === "ok" ? "ok" : "error");
    } catch {
      setStatus("error");
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  return (
    <>
      <PageBanner
        eyebrow=""
        title="工作台"
        sub="准备好下一份创作。从这里开始标注、检测或修复。"
      />
      <section className="section">
        <div className="dashboard-welcome">
          <div>
            <h2>让每一次交付，都有据可循。</h2>
            <p>
              上传一份文件，了解它的 AIGC 标识状态。
              <br />
              从一项清晰的检查，开始更有把握的内容管理。
            </p>
            <button
              className="tm-button light small"
              onClick={() => onNavigate("mediaInspect")}
            >
              检测一份文件 <Icon name="arrow" size={16} />
            </button>
          </div>
          <div className="dashboard-emblem" aria-hidden="true">
            <Icon name="shield" size={65} />
          </div>
        </div>
        <h2 className="dashboard-title">
          你想完成什么？
          <span className="service-status" role="status">
            {status === "loading"
              ? "正在检查服务…"
              : status === "ok"
                ? "标注与检测服务已连接"
                : "标注服务暂时未连接"}
            <button onClick={refresh} disabled={status === "loading"}>
              刷新 <Icon name="arrow" size={12} />
            </button>
          </span>
        </h2>
        <div className="dashboard-tools">
          {tasks.map((task) => (
            <button
              className="dashboard-tool"
              key={task.view}
              onClick={() => onNavigate(task.view)}
            >
              <Icon name={task.icon} size={25} />
              <h3>{task.title}</h3>
              <p>{task.description}</p>
              <span className="text-link">
                {task.action}
                <Icon name="arrow" size={16} />
              </span>
            </button>
          ))}
        </div>
        <div className="dashboard-next">
          <div className="ui-card">
            <div className="ui-card-head">
              <h3>从这里轻松上手</h3>
            </div>
            <div className="ui-card-body">
              {[
                {
                  title: "选择适合当前任务的工具",
                  text: "没有标识时可选择标注；不确定文件状态时，先进行检测。",
                },
                {
                  title: "上传文件，查看处理结果",
                  text: "标注与检测支持 JPEG、PNG、MP4；修复工作台支持 JPEG 和 PNG。",
                },
                {
                  title: "确认并下载",
                  text: "修复前先查看变更计划。标注或修复完成后，下载生成的新文件。",
                },
              ].map((step, i) => (
                <div className="dashboard-guide" key={step.title}>
                  <span>0{i + 1}</span>
                  <div>
                    <h4>{step.title}</h4>
                    <p>{step.text}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="ui-card">
            <div className="ui-card-head">
              <h3>本次会话的图片报告</h3>
            </div>
            <div className="ui-card-body">
              <div className="report-empty">
                <Icon name="image" size={36} />
                <strong>
                  {hasReport ? "你的检测报告已就绪" : "还没有生成图片报告"}
                </strong>
                <p>
                  {hasReport
                    ? "可继续查看本次会话最近生成的图片检测报告。"
                    : "使用图片检测报告工具，查看检测评级与明细。刷新页面后，会话记录将重置。"}
                </p>
                <button
                  className="tm-button secondary small"
                  onClick={() => onNavigate(hasReport ? "report" : "home")}
                >
                  {hasReport ? "查看报告" : "生成图片报告"}
                  <Icon name="arrow" size={14} />
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
