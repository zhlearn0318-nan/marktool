import { Alert, Button, Card, Collapse, Space, Tag } from "antd";
import type { DetectResponse } from "../types";
import CheckList from "../components/CheckList";
import ComplianceRadar from "../components/ComplianceRadar";
import ProvenanceChain from "../components/ProvenanceChain";

const RATING_COLOR: Record<string, string> = {
  A: "green",
  B: "blue",
  C: "orange",
  不合规: "red",
};

export default function ReportPage({
  data,
  onBack,
}: {
  data: DetectResponse;
  onBack: () => void;
}) {
  const { report, detection } = data;
  return (
    <div style={{ maxWidth: 900, margin: "24px auto" }}>
      <Space style={{ marginBottom: 16 }}>
        <Button onClick={onBack}>← 重新检测</Button>
        <span>文件：{detection.filename}</span>
      </Space>

      <Card
        title={
          <span>
            合规评级{" "}
            <Tag color={RATING_COLOR[report.compliance.rating] ?? "default"}>
              {report.compliance.rating}
            </Tag>
          </span>
        }
        style={{ marginBottom: 16 }}
      >
        <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
          <ComplianceRadar items={report.compliance.items} />
          <div style={{ flex: 1, minWidth: 300 }}>
            <CheckList items={report.compliance.items} />
            {report.compliance.suggestions.length ? (
              <Alert
                type="info"
                showIcon
                message="整改建议"
                description={
                  <ul>
                    {report.compliance.suggestions.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                }
              />
            ) : null}
          </div>
        </div>
      </Card>

      <Card title="标识溯源" style={{ marginBottom: 16 }}>
        <ProvenanceChain nodes={report.provenance} />
      </Card>

      <Card title="篡改检测" style={{ marginBottom: 16 }}>
        {report.tamper.findings.length ? (
          report.tamper.findings.map((f, i) => (
            <Alert key={i} type="warning" showIcon message={f} style={{ marginBottom: 8 }} />
          ))
        ) : (
          <span>未发现明显篡改迹象（受未启用检测项限制）。</span>
        )}
      </Card>

      <Card title="AI 内容检测" style={{ marginBottom: 16 }}>
        <Alert type="info" showIcon message={report.ai_detection.note} />
      </Card>

      <Collapse
        items={[
          {
            key: "raw",
            label: "原始 AIGC 元数据",
            children: (
              <pre style={{ whiteSpace: "pre-wrap" }}>
                {JSON.stringify(detection.aigc_metadata, null, 2) || "无"}
              </pre>
            ),
          },
        ]}
      />
    </div>
  );
}
