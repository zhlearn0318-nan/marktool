import { Collapse } from "antd";
import PageBanner from "../components/PageBanner";
import Icon from "../components/Icon";
import type { View } from "../nav";
const questions = [
  {
    key: "scope",
    label: "目前可以处理哪些文件？",
    children:
      "元数据标注支持 JPEG、PNG、MP4、M4A、MP3、Markdown、PDF、HTML 和 DOCX。图片、视频和文档还支持合规检测及按计划修复；M4A / MP3 当前支持打标与回读校验，合规检测及修复尚未开放。",
  },
  {
    key: "detect",
    label: "合规检测会判断内容是不是 AI 生成的吗？",
    children:
      "本平台检查文件中已有的 AIGC 标识及其字段结构。它不根据画面推测内容是否由 AI 生成，也不会把未检出标识直接当作内容由人类创作的证明。",
  },
  {
    key: "multimodal",
    label: "多模态和场景标识在哪里？",
    children:
      "工作空间里的「多模态」会把视频拆成画面、音频和字幕，分别写入隐式标识后再重组。「场景标识」按第 5.5、5.6 条处理虚拟场景起始画面和交互界面提示。单文件的标注、检测和修复仍走原来的入口。",
  },
  {
    key: "original",
    label: "标注和修复会改动我的原件吗？",
    children:
      "上传后服务会生成新的结果文件供下载，原件保留。处理聚焦元数据，图片不重新压缩像素，视频不转码。修复工作台会先展示变更计划，再由你确认执行。",
  },
  {
    key: "producer",
    label: "生产者编号应该填写什么？",
    children:
      "填写你实际使用的机构或内容生产者编号，请勿直接填写中文名称。使用可打印的 ASCII 字符，不要包含空格、双引号或反斜杠。平台会为首次标注自动生成内容编号。",
  },
  {
    key: "result",
    label: "看到“未检出标识”后，该怎么做？",
    children:
      "先确认内容来源及标注需要。如果应当添加 AIGC 标识，可到元数据标注页面写入；如果图片、视频或文档中已有标识但字段存在问题，可查看修复计划后再操作。",
  },
  {
    key: "disabled",
    label: "为什么部分报告项目显示尚未启用？",
    children:
      "原有图片报告保留了 OCR、像素水印与 AI 内容概率模块的展示位置，这些模块尚未启用。当前真实可用的能力集中在 AIGC 文件元数据标注、检查与修复。",
  },
];
export default function HelpPage({
  onNavigate,
}: {
  onNavigate: (view: View) => void;
}) {
  return (
    <>
      <PageBanner
        eyebrow=""
        title="帮助中心"
        sub="从选择工具到下载结果，让每一步都有清晰的指引。"
      />
      <section className="section">
        <div className="grid-3">
          {[
            {
              title: "我想给内容添加标识",
              text: "选择元数据标注，上传文件并填写生产者编号。",
              view: "labeling" as View,
              icon: "label" as const,
            },
            {
              title: "我想检查文件是否有问题",
              text: "选择合规检测，上传文件后查看检测结论。",
              view: "mediaInspect" as View,
              icon: "scan" as const,
            },
            {
              title: "我想修复文件中的标识",
              text: "选择元数据修复，先生成计划，再确认执行。",
              view: "mediaRepair" as View,
              icon: "repair" as const,
            },
          ].map((item) => (
            <button
              className="dashboard-tool"
              onClick={() => onNavigate(item.view)}
              key={item.view}
            >
              <Icon name={item.icon} size={24} />
              <h3>{item.title}</h3>
              <p>{item.text}</p>
              <span className="text-link">
                前往工具 <Icon name="arrow" size={16} />
              </span>
            </button>
          ))}
        </div>
        <h2 className="dashboard-title">常见问题</h2>
        <div className="ui-card">
          <Collapse
            ghost
            accordion
            items={questions}
            defaultActiveKey={["scope"]}
          />
        </div>
      </section>
    </>
  );
}
