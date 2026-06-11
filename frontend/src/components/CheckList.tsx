import { List, Tag } from "antd";
import type { CheckItem } from "../types";

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  pass: { color: "success", label: "✅ 合规" },
  fail: { color: "error", label: "❌ 缺失" },
  warn: { color: "warning", label: "⚠️ 待验证/格式" },
};

export default function CheckList({ items }: { items: CheckItem[] }) {
  return (
    <List
      itemLayout="vertical"
      dataSource={items}
      renderItem={(it) => {
        const s = STATUS_MAP[it.status] ?? { color: "default", label: it.status };
        return (
          <List.Item key={it.id}>
            <List.Item.Meta
              title={
                <span>
                  {it.title} <Tag color={s.color}>{s.label}</Tag>
                </span>
              }
              description={it.detail}
            />
            {it.suggestion ? (
              <div style={{ color: "#8c8c8c" }}>整改建议：{it.suggestion}</div>
            ) : null}
          </List.Item>
        );
      }}
    />
  );
}
