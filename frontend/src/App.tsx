import { useState } from "react";
import { ConfigProvider, Layout, Typography } from "antd";
import zhCN from "antd/locale/zh_CN";
import UploadPage from "./pages/UploadPage";
import ReportPage from "./pages/ReportPage";
import type { DetectResponse } from "./types";

export default function App() {
  const [result, setResult] = useState<DetectResponse | null>(null);
  return (
    <ConfigProvider locale={zhCN}>
      <Layout style={{ minHeight: "100vh" }}>
        <Layout.Header style={{ color: "#fff" }}>
          <Typography.Title level={4} style={{ color: "#fff", lineHeight: "64px", margin: 0 }}>
            AIGC 标识合规平台 · MVP（图片检测）
          </Typography.Title>
        </Layout.Header>
        <Layout.Content style={{ padding: 24, background: "#f5f5f5" }}>
          {result ? (
            <ReportPage data={result} onBack={() => setResult(null)} />
          ) : (
            <UploadPage onResult={setResult} />
          )}
        </Layout.Content>
      </Layout>
    </ConfigProvider>
  );
}
