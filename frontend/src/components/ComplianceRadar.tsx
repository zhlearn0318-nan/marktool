import type { CheckItem } from "../types";

const AXES = [
  { key: "implicit_metadata", label: "隐式元数据" },
  { key: "explicit_text", label: "显式文字" },
  { key: "implicit_watermark", label: "隐式水印" },
];

function scoreFor(items: CheckItem[], markType: string): number {
  const it = items.find((i) => i.mark_type === markType);
  if (!it) return 0;
  if (it.status === "pass") return 1;
  if (it.status === "warn") return 0.5;
  return 0;
}

export default function ComplianceRadar({ items }: { items: CheckItem[] }) {
  const size = 240;
  const c = size / 2;
  const r = 90;
  const pts = AXES.map((ax, i) => {
    const angle = (Math.PI * 2 * i) / AXES.length - Math.PI / 2;
    const score = scoreFor(items, ax.key);
    return {
      ax: { x: c + r * Math.cos(angle), y: c + r * Math.sin(angle) },
      val: { x: c + r * score * Math.cos(angle), y: c + r * score * Math.sin(angle) },
      label: ax.label,
      angle,
    };
  });
  const polygon = pts.map((p) => `${p.val.x},${p.val.y}`).join(" ");
  return (
    <svg width={size} height={size}>
      <polygon
        points={pts.map((p) => `${p.ax.x},${p.ax.y}`).join(" ")}
        fill="none"
        stroke="#d9d9d9"
      />
      {pts.map((p, i) => (
        <line key={i} x1={c} y1={c} x2={p.ax.x} y2={p.ax.y} stroke="#f0f0f0" />
      ))}
      <polygon points={polygon} fill="rgba(24,144,255,0.3)" stroke="#1890ff" />
      {pts.map((p, i) => (
        <text
          key={i}
          x={c + (r + 18) * Math.cos(p.angle)}
          y={c + (r + 18) * Math.sin(p.angle)}
          fontSize="12"
          textAnchor="middle"
          dominantBaseline="middle"
          fill="#595959"
        >
          {p.label}
        </text>
      ))}
    </svg>
  );
}
