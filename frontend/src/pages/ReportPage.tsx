import { useState, type ReactNode } from "react";
import { Collapse, Modal, message } from "antd";
import type { DetectResponse } from "../types";
import CheckList from "../components/CheckList";
import ComplianceRadar from "../components/ComplianceRadar";
import ProvenanceChain from "../components/ProvenanceChain";
import RatingSeal from "../components/RatingSeal";

const RATING_DESC: Record<string, string> = {
  A: "完全合规 · 标识齐备且结构规范",
  B: "基本合规 · 元数据合规，部分检测项受插件限制未验证",
  C: "需整改 · 检出标识但结构不规范",
  不合规: "不合规 · 未检出有效 AIGC 标识",
};

/** 由报告内容派生一个稳定的伪指纹（前端演示用，非真实哈希）。 */
function pseudoHash(s: string): string {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  let out = "";
  let x = h;
  for (let i = 0; i < 8; i++) {
    x = Math.imul(x ^ (x >>> 13), 16777619) >>> 0;
    out += ("00000000" + x.toString(16)).slice(-8);
  }
  return out.slice(0, 64);
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: string;
  children: ReactNode;
}) {
  return (
    <div className="ui-card" style={{ marginBottom: 18 }}>
      <div className="ui-card-head">
        <span className="ico">{icon}</span>
        <h3>{title}</h3>
      </div>
      <div className="ui-card-body">{children}</div>
    </div>
  );
}

export default function ReportPage({
  data,
  onBack,
}: {
  data: DetectResponse;
  onBack: () => void;
}) {
  const { report, detection } = data;
  const items = report.compliance.items;
  const pass = items.filter((i) => i.status === "pass").length;
  const warn = items.filter((i) => i.status === "warn").length;
  const fail = items.filter((i) => i.status === "fail").length;

  const [attestOpen, setAttestOpen] = useState(false);
  const certHash = pseudoHash(detection.filename + "|" + report.compliance.rating + "|" + items.length);
  const stampedAt = new Date().toLocaleString("zh-CN");

  function share() {
    const url = window.location.href;
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(
        () => message.success("报告链接已复制到剪贴板"),
        () => message.info(url)
      );
    } else {
      message.info(url);
    }
  }

  const actions: { label: string; fn: () => void }[] = [
    { label: "导出 PDF 报告", fn: () => window.print() },
    { label: "打印", fn: () => window.print() },
    { label: "分享链接", fn: share },
    { label: "加入区块链存证", fn: () => setAttestOpen(true) },
  ];

  return (
    <div style={{ maxWidth: "var(--maxw)", margin: "0 auto", padding: "32px 28px 8px" }}>
      {/* ===== Report header ===== */}
      <div
        className="reveal"
        style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap", marginBottom: 22 }}
      >
        <button className="btn-ghost-light" onClick={onBack}>
          ← 重新检测
        </button>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div className="eyebrow">检测报告 · DETECTION REPORT</div>
          <div style={{ fontSize: 15, color: "var(--text-700)", marginTop: 4 }}>
            文件 <strong style={{ color: "var(--text-900)" }}>{detection.filename}</strong>
            <span style={{ color: "var(--text-400)", marginLeft: 12 }}>模态 · 图片</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {actions.map((a) => (
            <button
              key={a.label}
              className="btn-ghost-light"
              onClick={a.fn}
              style={{ height: 40, fontSize: 13 }}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>

      {/* ===== Summary band ===== */}
      <div
        className="ui-card reveal d1"
        style={{ display: "flex", gap: 32, padding: 28, marginBottom: 18, flexWrap: "wrap", alignItems: "center" }}
      >
        <div className="seal-wrap" style={{ flex: "none" }}>
          <RatingSeal rating={report.compliance.rating} />
        </div>
        <div style={{ flex: 1, minWidth: 280 }}>
          <div className="eyebrow">综合评定</div>
          <div
            className="serif"
            style={{ fontSize: 26, fontWeight: 700, color: "var(--text-900)", margin: "8px 0 4px" }}
          >
            合规评级 {report.compliance.rating}
          </div>
          <div style={{ color: "var(--text-600)", fontSize: 14.5, marginBottom: 20 }}>
            {RATING_DESC[report.compliance.rating] ?? "—"}
          </div>
          <div className="kpi-grid">
            <div className="kpi">
              <div className="kpi-num">{items.length}</div>
              <div className="kpi-lbl">检测项总数</div>
            </div>
            <div className="kpi pass">
              <div className="kpi-num">{pass}</div>
              <div className="kpi-lbl">合规通过</div>
            </div>
            <div className="kpi warn">
              <div className="kpi-num">{warn}</div>
              <div className="kpi-lbl">待验证 / 格式</div>
            </div>
            <div className="kpi fail">
              <div className="kpi-num">{fail}</div>
              <div className="kpi-lbl">缺失</div>
            </div>
          </div>
        </div>
      </div>

      {/* ===== Compliance detail ===== */}
      <div className="reveal d2">
        <Section title="合规评级与检测明细" icon="◎">
          <div style={{ display: "flex", gap: 36, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div style={{ flex: "none", display: "flex", flexDirection: "column", alignItems: "center" }}>
              <ComplianceRadar items={items} />
              <div style={{ fontSize: 12, color: "var(--text-400)", marginTop: 4 }}>
                合规雷达 · 通过 100% / 待验证 50% / 缺失 0%
              </div>
            </div>
            <div style={{ flex: 1, minWidth: 320 }}>
              <CheckList items={items} />
              {report.compliance.suggestions.length ? (
                <div
                  style={{
                    marginTop: 18,
                    background: "var(--gold-100)",
                    border: "1px solid var(--gold-300)",
                    borderRadius: 12,
                    padding: "16px 18px",
                  }}
                >
                  <div style={{ fontWeight: 700, color: "var(--gold-600)", marginBottom: 8 }}>整改建议</div>
                  <ul
                    style={{
                      margin: 0,
                      paddingLeft: 20,
                      color: "var(--text-700)",
                      fontSize: 13.5,
                      lineHeight: 1.9,
                    }}
                  >
                    {report.compliance.suggestions.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          </div>
        </Section>
      </div>

      {/* ===== Provenance ===== */}
      <div className="reveal d3">
        <Section title="标识溯源" icon="⛓">
          <ProvenanceChain nodes={report.provenance} />
        </Section>
      </div>

      {/* ===== Tamper + AI ===== */}
      <div className="reveal d4" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <Section title="篡改检测" icon="🛡">
          {report.tamper.findings.length ? (
            report.tamper.findings.map((f, i) => (
              <div
                key={i}
                style={{
                  background: "var(--amber-bg)",
                  color: "var(--amber)",
                  borderRadius: 10,
                  padding: "12px 14px",
                  fontSize: 13.5,
                  marginBottom: 8,
                }}
              >
                {f}
              </div>
            ))
          ) : (
            <div style={{ color: "var(--text-600)", fontSize: 14 }}>
              未发现明显篡改迹象（受未启用检测项限制）。
            </div>
          )}
        </Section>

        <Section title="AI 内容检测" icon="◬">
          <div
            style={{
              background: "var(--porcelain)",
              border: "1px dashed var(--line-strong)",
              borderRadius: 10,
              padding: "14px 16px",
              color: "var(--text-600)",
              fontSize: 14,
              lineHeight: 1.7,
            }}
          >
            {report.ai_detection.note}
          </div>
        </Section>
      </div>

      {/* ===== Raw metadata ===== */}
      <div className="reveal d5" style={{ marginBottom: 40 }}>
        <Collapse
          items={[
            {
              key: "raw",
              label: "原始 AIGC 元数据（XMP → JSON）",
              children: (
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    fontSize: 12.5,
                    background: "var(--ink-950)",
                    padding: 16,
                    borderRadius: 10,
                    margin: 0,
                  }}
                >
                  <code style={{ color: "#cfe0ff" }}>
                    {detection.aigc_metadata
                      ? JSON.stringify(detection.aigc_metadata, null, 2)
                      : "未检出元数据"}
                  </code>
                </pre>
              ),
            },
          ]}
        />
      </div>

      <Modal
        open={attestOpen}
        onCancel={() => setAttestOpen(false)}
        onOk={() => setAttestOpen(false)}
        okText="完成"
        cancelText="关闭"
        title="区块链存证凭证（演示）"
      >
        <p style={{ color: "var(--text-600)", marginTop: 0, fontSize: 13.5 }}>
          已将本次检测报告的指纹写入演示链，凭证如下（前端演示，不含真实上链）：
        </p>
        <div className="code-block" style={{ color: "#cfe0ff", lineHeight: 2 }}>
          <div>状态：✔ 已存证</div>
          <div style={{ wordBreak: "break-all" }}>报告指纹：{certHash}</div>
          <div>时间戳：{stampedAt}</div>
          <div>链：TraceMark Demo Chain</div>
          <div>区块高度：#{1280000 + items.length * 7}</div>
          <div>评级：{report.compliance.rating}</div>
        </div>
      </Modal>
    </div>
  );
}
