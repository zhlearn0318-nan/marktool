import { useState } from "react";
import { Button, Card, Select, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { detectImage } from "../api";
import type { DetectResponse } from "../types";

export default function UploadPage({ onResult }: { onResult: (r: DetectResponse) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [reg, setReg] = useState("CN_GB45438");
  const [loading, setLoading] = useState(false);

  async function submit() {
    if (!file) {
      message.warning("请先选择图片");
      return;
    }
    setLoading(true);
    try {
      onResult(await detectImage(file, reg));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card title="上传图片进行 AIGC 标识合规检测" style={{ maxWidth: 640, margin: "40px auto" }}>
      <Upload.Dragger
        beforeUpload={(f) => {
          setFile(f);
          return false;
        }}
        maxCount={1}
        accept="image/*"
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p>点击或拖拽图片到此处</p>
      </Upload.Dragger>
      <div style={{ marginTop: 16 }}>
        目标法规：
        <Select
          value={reg}
          onChange={setReg}
          style={{ width: 220, marginLeft: 8 }}
          options={[{ value: "CN_GB45438", label: "中国国标 GB 45438-2025" }]}
        />
      </div>
      <Button type="primary" onClick={submit} loading={loading} style={{ marginTop: 16 }}>
        开始检测
      </Button>
    </Card>
  );
}
