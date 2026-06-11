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
  const size = 260;
  const c = size / 2;
  const r = 92;
  const rings = [0.34, 0.67, 1];

  const pts = AXES.map((ax, i) => {
    const angle = (Math.PI * 2 * i) / AXES.length - Math.PI / 2;
    const score = scoreFor(items, ax.key);
    return {
      angle,
      score,
      label: ax.label,
      ax: { x: c + r * Math.cos(angle), y: c + r * Math.sin(angle) },
      val: { x: c + r * score * Math.cos(angle), y: c + r * score * Math.sin(angle) },
    };
  });
  const valuePoly = pts.map((p) => `${p.val.x},${p.val.y}`).join(" ");
  const ringPoly = (f: number) =>
    pts.map((p) => `${c + r * f * Math.cos(p.angle)},${c + r * f * Math.sin(p.angle)}`).join(" ");

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <defs>
        <radialGradient id="radar-fill" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#16407a" stopOpacity="0.42" />
          <stop offset="1" stopColor="#16407a" stopOpacity="0.16" />
        </radialGradient>
      </defs>

      {/* grid rings */}
      {rings.map((f, i) => (
        <polygon key={i} points={ringPoly(f)} fill="none" stroke="#dde3ec" strokeWidth="1" />
      ))}
      {/* spokes */}
      {pts.map((p, i) => (
        <line key={i} x1={c} y1={c} x2={p.ax.x} y2={p.ax.y} stroke="#e9edf3" strokeWidth="1" />
      ))}

      {/* value polygon */}
      <polygon points={valuePoly} fill="url(#radar-fill)" stroke="#16407a" strokeWidth="2" />
      {pts.map((p, i) => (
        <circle key={i} cx={p.val.x} cy={p.val.y} r="4" fill="#c8a45c" stroke="#fff" strokeWidth="1.5" />
      ))}

      {/* labels */}
      {pts.map((p, i) => {
        const lx = c + (r + 22) * Math.cos(p.angle);
        const ly = c + (r + 22) * Math.sin(p.angle);
        return (
          <g key={i}>
            <text
              x={lx}
              y={ly - 5}
              fontSize="12.5"
              fontWeight="700"
              textAnchor="middle"
              fill="#2b3950"
              fontFamily="'Manrope','Noto Sans SC',sans-serif"
            >
              {p.label}
            </text>
            <text
              x={lx}
              y={ly + 11}
              fontSize="11"
              textAnchor="middle"
              fill="#b88e44"
              fontWeight="700"
            >
              {Math.round(p.score * 100)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}
