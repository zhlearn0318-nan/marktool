import { useState } from "react";
import { Select, Upload, message } from "antd";
import Icon from "../components/Icon";
import PageBanner from "../components/PageBanner";
import { detectImage } from "../api";
import type { DetectResponse } from "../types";
import type { View } from "../nav";
export default function UploadPage({
  onResult,
  onNavigate,
}: {
  onResult: (result: DetectResponse) => void;
  onNavigate: (view: View) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  async function submit() {
    if (!file || loading) return;
    setLoading(true);
    try {
      onResult(await detectImage(file, "CN_GB45438"));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  return (
    <>
      <PageBanner
        eyebrow=""
        title="图片检测报告（旧版）"
        sub="保留原有图片报告流程。核查新版标注文件时，请优先使用「合规检测」。"
      />
      <section className="section">
        <div className="tool-stepbar">
          <span className="current">
            <b>1</b>上传图片
          </span>
          <Icon name="chevron" size={12} />
          <span>
            <b>2</b>开始检测
          </span>
          <Icon name="chevron" size={12} />
          <span>
            <b>3</b>查看报告
          </span>
        </div>
        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="ui-card">
            <div className="ui-card-head">
              <Icon name="upload" size={18} />
              <h3>选择待检测图片</h3>
            </div>
            <div className="ui-card-body">
              <Upload.Dragger
                accept=".png,.jpg,.jpeg,image/png,image/jpeg"
                disabled={loading}
                maxCount={1}
                showUploadList={false}
                beforeUpload={(f) => {
                  if (!/\.(png|jpe?g)$/i.test(f.name)) {
                    message.warning("请选择 PNG 或 JPEG 图片");
                    return Upload.LIST_IGNORE;
                  }
                  setFile(f);
                  return false;
                }}
              >
                <p className="ant-upload-drag-icon">
                  <Icon name="upload" size={32} />
                </p>
                <p className="ant-upload-text">拖入图片，或点击选择文件</p>
                <p className="ant-upload-hint">支持 PNG / JPEG · 单张检测</p>
              </Upload.Dragger>
              {file && (
                <div className="upload-file-preview">
                  <Icon name="image" />
                  <div>
                    <strong>{file.name}</strong>
                    <small>{(file.size / 1048576).toFixed(2)} MB</small>
                  </div>
                  <button
                    onClick={() => setFile(null)}
                    disabled={loading}
                    aria-label="移除已选图片"
                  >
                    <Icon name="close" size={16} />
                  </button>
                </div>
              )}
              <label className="upload-field" htmlFor="report-standard">
                检测标准
              </label>
              <Select
                id="report-standard"
                aria-label="检测标准"
                value="CN_GB45438"
                style={{ width: "100%" }}
                options={[
                  {
                    value: "CN_GB45438",
                    label: "GB 45438-2025 · AIGC 隐式标识",
                  },
                ]}
              />
              <button
                className="btn-gold"
                disabled={!file || loading}
                onClick={submit}
                style={{ width: "100%", marginTop: 24 }}
              >
                {loading ? "正在生成报告…" : "开始检测并生成报告"}
                <Icon name="arrow" size={16} />
              </button>
              <p className="tool-hint">
                检测不会修改原始图片。完成后可查看报告并打印保存。
              </p>
            </div>
          </div>
          <div className="ui-card">
            <div className="ui-card-head">
              <Icon name="scan" size={18} />
              <h3>你会在报告中看到</h3>
            </div>
            <div className="ui-card-body">
              {[
                {
                  title: "元数据检查",
                  text: "查看图片中是否含有 AIGC 隐式标识，以及字段结构检查结果。",
                },
                {
                  title: "合规评级与明细",
                  text: "了解已通过、缺失和待验证项目，结合检测依据判断下一步。",
                },
                {
                  title: "来源信息",
                  text: "读取已有标识中的生产者、传播者与内容编号。",
                },
              ].map((item) => (
                <div className="report-scope" key={item.title}>
                  <Icon name="check" size={17} />
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.text}</p>
                  </div>
                </div>
              ))}
              <p className="tool-hint">
                本工具保留原有图片报告流程。OCR、像素水印与 AI
                内容概率尚未启用。需要图片或视频的完整元数据核查，请使用「合规检测」。
              </p>
              <button
                className="text-link"
                style={{ marginTop: 22 }}
                onClick={() => onNavigate("mediaInspect")}
              >
                前往合规检测 <Icon name="arrow" size={16} />
              </button>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
