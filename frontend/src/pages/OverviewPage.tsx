import PageBanner from "../components/PageBanner";
import Icon from "../components/Icon";
import type { View } from "../nav";
export default function OverviewPage({
  onNavigate,
}: {
  onNavigate: (view: View) => void;
}) {
  return (
    <>
      <PageBanner
        eyebrow=""
        title="把内容标识，变成清晰的工作流"
        sub="源迹围绕 AIGC 文件元数据提供标注、检测与修复。以下能力状态对应当前本地版本。"
      />
      <section className="section">
        <div className="dashboard-welcome">
          <div>
            <h2>从标注到核查，一个工作空间。</h2>
            <p>
              面向图片、视频、音频与文档的文件元数据管理。
              <br />
              按任务选择工具，依据处理结果继续操作。
            </p>
            <button
              className="tm-button light small"
              onClick={() => onNavigate("console")}
            >
              进入工作台 <Icon name="arrow" size={16} />
            </button>
          </div>
          <div className="dashboard-emblem">
            <Icon name="layers" size={62} />
          </div>
        </div>
        <div className="grid-3">
          {[
            {
              title: "写入标识",
              text: "记录内容生产者、标识类别与内容编号，生成可下载的新文件。",
              view: "labeling" as View,
              icon: "label" as const,
            },
            {
              title: "检查元数据",
              text: "检查已有标识的载体、字段与结构，输出详细结论和问题清单。",
              view: "mediaInspect" as View,
              icon: "scan" as const,
            },
            {
              title: "按计划修复",
              text: "为图片、视频或文档生成修复计划，确认后执行，并保留操作记录。",
              view: "mediaRepair" as View,
              icon: "repair" as const,
            },
          ].map((item) => (
            <button
              className="dashboard-tool"
              key={item.view}
              onClick={() => onNavigate(item.view)}
            >
              <Icon name={item.icon} size={24} />
              <h3>{item.title}</h3>
              <p>{item.text}</p>
              <span className="text-link">
                开始使用 <Icon name="arrow" size={16} />
              </span>
            </button>
          ))}
        </div>
        <h2 className="dashboard-title">当前支持范围</h2>
        <div className="ui-card">
          <table className="spec-table">
            <thead>
              <tr>
                <th>内容格式</th>
                <th>元数据标注</th>
                <th>合规检测</th>
                <th>修复工作台</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>JPEG / PNG</td>
                <td>可用</td>
                <td>可用</td>
                <td>可用</td>
              </tr>
              <tr>
                <td>MP4 视频</td>
                <td>可用</td>
                <td>可用</td>
                <td>可用</td>
              </tr>
              <tr>
                <td>Markdown / PDF / HTML / DOCX</td>
                <td>可用</td>
                <td>可用</td>
                <td>可用</td>
              </tr>
              <tr>
                <td>M4A / MP3 音频</td>
                <td>可用（含回读校验）</td>
                <td>尚未开放</td>
                <td>尚未开放</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="tool-hint">
          当前能力聚焦 GB 45438-2025 AIGC 文件元数据。C2PA 标准互转、OCR
          显式标识、像素水印与 AI 内容概率不属于当前已启用能力。
        </p>
      </section>
    </>
  );
}
