import type { CheckItem } from "../types";

const STATUS: Record<string, { icon: string; pill: string; cls: string }> = {
  pass: { icon: "✓", pill: "合规", cls: "pass" },
  fail: { icon: "✕", pill: "缺失", cls: "fail" },
  warn: { icon: "!", pill: "待验证 / 格式", cls: "warn" },
};

export default function CheckList({ items }: { items: CheckItem[] }) {
  return (
    <div>
      {items.map((it) => {
        const s = STATUS[it.status] ?? { icon: "·", pill: it.status, cls: "warn" };
        return (
          <div className="check-item" key={it.id}>
            <div className={`check-badge ${s.cls}`}>{s.icon}</div>
            <div className="check-main" style={{ flex: 1 }}>
              <div className="ct">
                {it.title}
                <span className={`status-pill ${s.cls}`}>{s.pill}</span>
              </div>
              <div className="cd">{it.detail}</div>
              {it.suggestion ? <div className="cs">整改建议 · {it.suggestion}</div> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
