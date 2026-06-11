import { Empty } from "antd";
import type { ProvenanceNode } from "../types";

const ROLE_CN: Record<string, string> = {
  ContentProducer: "生成服务提供者",
  ContentPropagator: "传播服务提供者",
};

export default function ProvenanceChain({ nodes }: { nodes: ProvenanceNode[] }) {
  if (!nodes.length)
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未在元数据中检出可还原的标识链路" />;
  return (
    <div className="prov-flow">
      {nodes.map((n, i) => (
        <div key={i} style={{ display: "flex", alignItems: "stretch" }}>
          <div className="prov-node">
            <div className="role">{n.role}</div>
            <div className="nm">{n.name}</div>
            <div style={{ fontSize: 12, color: "var(--text-600)", marginTop: 2 }}>
              {ROLE_CN[n.role] ?? "标识参与方"}
            </div>
            {n.id ? <div className="id">编号 {n.id}</div> : null}
          </div>
          {i < nodes.length - 1 ? <div className="prov-arrow">→</div> : null}
        </div>
      ))}
    </div>
  );
}
