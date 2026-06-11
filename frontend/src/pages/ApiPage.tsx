import type { ReactNode } from "react";
import { Tabs } from "antd";
import PageBanner from "../components/PageBanner";

function Method({ m }: { m: string }) {
  const color = m === "GET" ? "#2c8f60" : "#1c4271";
  return (
    <span
      style={{
        fontFamily: "Consolas, monospace",
        fontWeight: 700,
        fontSize: 12,
        color: "#fff",
        background: color,
        padding: "3px 10px",
        borderRadius: 6,
        letterSpacing: "0.04em",
      }}
    >
      {m}
    </span>
  );
}

function Endpoint({
  method,
  path,
  desc,
  children,
}: {
  method: string;
  path: string;
  desc: string;
  children?: ReactNode;
}) {
  return (
    <div className="ui-card" style={{ marginBottom: 18 }}>
      <div className="ui-card-head" style={{ gap: 12 }}>
        <Method m={method} />
        <h3 style={{ fontFamily: "Consolas, monospace", fontSize: 15 }}>{path}</h3>
      </div>
      <div className="ui-card-body">
        <div style={{ color: "var(--text-600)", fontSize: 14, marginBottom: children ? 16 : 0 }}>{desc}</div>
        {children}
      </div>
    </div>
  );
}

const CURL = `curl -X POST http://localhost:8000/api/detect \\
  -F "file=@sample.png" \\
  -F "target_regulation=CN_GB45438"`;

const PY = `import requests

with open("sample.png", "rb") as f:
    r = requests.post(
        "http://localhost:8000/api/detect",
        files={"file": f},
        data={"target_regulation": "CN_GB45438"},
    )
data = r.json()
print(data["report"]["compliance"]["rating"])   # A / B / C / 不合规`;

const JS = `const fd = new FormData();
fd.append("file", file);                       // File 对象
fd.append("target_regulation", "CN_GB45438");

const res = await fetch("/api/detect", { method: "POST", body: fd });
const data = await res.json();
console.log(data.report.compliance.rating);`;

const RESP = `{
  "result_id": "9a1e4bfb…",
  "detection": {
    "filename": "sample.png",
    "modality": "image",
    "items": [
      { "id": "metadata.schema", "status": "pass",
        "title": "隐式标识(元数据)", "mark_type": "implicit_metadata" }
    ],
    "aigc_metadata": { "Label": "1", "ContentProducer": "…", "ProduceID": "…" }
  },
  "report": {
    "compliance": { "rating": "B", "items": [ … ], "suggestions": [ … ] },
    "provenance": [ { "role": "ContentProducer", "name": "…", "id": "…" } ],
    "tamper": { "consistent": null, "findings": [ … ] },
    "ai_detection": { "enabled": false, "note": "…" }
  }
}`;

export default function ApiPage() {
  return (
    <>
      <PageBanner
        eyebrow="DEVELOPER API"
        title="开发者 API"
        sub="源迹 TraceMark 以 REST API 暴露检测能力，可直接集成到你的发布流水线。以下为当前 MVP 已实现的接口。"
      />

      <section className="section">
        <div className="grid-2" style={{ alignItems: "start", marginBottom: 28 }}>
          <div>
            <Endpoint method="GET" path="/api/health" desc="健康检查，返回服务状态。" >
              <pre className="code-block" style={{ margin: 0 }}>{`200 OK
{ "status": "ok" }`}</pre>
            </Endpoint>

            <Endpoint
              method="POST"
              path="/api/detect"
              desc="上传图片执行合规检测，返回检测明细与四部分报告。"
            >
              <table className="spec-table" style={{ marginBottom: 14 }}>
                <thead><tr><th>参数</th><th>类型</th><th>说明</th></tr></thead>
                <tbody>
                  <tr><td style={{ fontWeight: 700 }}>file</td><td>file</td><td>待检测图片（PNG / JPEG）</td></tr>
                  <tr><td style={{ fontWeight: 700 }}>target_regulation</td><td>string</td><td>目标法规，默认 CN_GB45438</td></tr>
                </tbody>
              </table>
            </Endpoint>

            <Endpoint
              method="GET"
              path="/api/report/{result_id}"
              desc="按检测 ID 取回此前生成的完整报告。"
            />
          </div>

          <div className="ui-card">
            <div className="ui-card-head"><span className="ico">{"{ }"}</span><h3>请求示例</h3></div>
            <div className="ui-card-body">
              <Tabs
                items={[
                  { key: "curl", label: "cURL", children: <pre className="code-block" style={{ margin: 0 }}>{CURL}</pre> },
                  { key: "py", label: "Python", children: <pre className="code-block" style={{ margin: 0 }}>{PY}</pre> },
                  { key: "js", label: "JavaScript", children: <pre className="code-block" style={{ margin: 0 }}>{JS}</pre> },
                ]}
              />
            </div>
          </div>
        </div>

        <div className="ui-card">
          <div className="ui-card-head"><span className="ico">↳</span><h3>响应结构 · /api/detect</h3></div>
          <div className="ui-card-body">
            <pre className="code-block" style={{ margin: 0 }}>{RESP}</pre>
          </div>
        </div>

        <div style={{ marginTop: 18, fontSize: 13, color: "var(--text-400)", textAlign: "center" }}>
          交互式 Swagger 文档：服务运行时访问 <code>http://localhost:8000/docs</code>
        </div>
      </section>
    </>
  );
}
