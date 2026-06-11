import { Empty, Tag } from "antd";
import type { ProvenanceNode } from "../types";

export default function ProvenanceChain({ nodes }: { nodes: ProvenanceNode[] }) {
  if (!nodes.length) return <Empty description="无可还原的标识链路" />;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      {nodes.map((n, i) => (
        <span key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              border: "1px solid #1890ff",
              borderRadius: 8,
              padding: "8px 12px",
              background: "#e6f4ff",
            }}
          >
            <Tag color="blue">{n.role}</Tag>
            <div style={{ fontWeight: 600 }}>{n.name}</div>
            {n.id ? <div style={{ fontSize: 12, color: "#8c8c8c" }}>编号：{n.id}</div> : null}
          </div>
          {i < nodes.length - 1 ? <span style={{ fontSize: 20 }}>→</span> : null}
        </span>
      ))}
    </div>
  );
}
