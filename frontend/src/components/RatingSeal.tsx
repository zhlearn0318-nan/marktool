/** 评级印章：圆形官印式徽记，环绕文字 + 中心评级。signature 视觉元素。 */
const COLOR: Record<string, string> = {
  A: "#2e9e6b",
  B: "#16407a",
  C: "#c9892a",
  不合规: "#c0392b",
};

const DISPLAY: Record<string, string> = {
  A: "A",
  B: "B",
  C: "C",
  不合规: "✕",
};

export default function RatingSeal({ rating }: { rating: string }) {
  const c = COLOR[rating] ?? "#16407a";
  const grade = DISPLAY[rating] ?? rating;
  const size = 168;
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" aria-label={`合规评级 ${rating}`}>
      <defs>
        <path id="seal-top" d="M100,100 m-74,0 a74,74 0 1,1 148,0" fill="none" />
        <path id="seal-bot" d="M100,100 m74,0 a74,74 0 1,1 -148,0" fill="none" />
      </defs>

      {/* outer rings */}
      <circle cx="100" cy="100" r="92" fill="none" stroke={c} strokeWidth="1.4" opacity="0.45" />
      <circle cx="100" cy="100" r="84" fill="none" stroke={c} strokeWidth="3" />
      <circle cx="100" cy="100" r="58" fill="none" stroke={c} strokeWidth="1.2" opacity="0.55" />

      {/* curved text */}
      <text fill={c} fontSize="13" fontWeight="700" letterSpacing="3.5" fontFamily="'Manrope',sans-serif">
        <textPath href="#seal-top" startOffset="50%" textAnchor="middle">
          合 规 评 级 · COMPLIANCE
        </textPath>
      </text>
      <text fill={c} fontSize="11" fontWeight="600" letterSpacing="2.5" fontFamily="'Manrope',sans-serif" opacity="0.85">
        <textPath href="#seal-bot" startOffset="50%" textAnchor="middle">
          GB 45438-2025 · IMAGE
        </textPath>
      </text>

      {/* side stars */}
      <text x="22" y="105" fill={c} fontSize="14" textAnchor="middle">★</text>
      <text x="178" y="105" fill={c} fontSize="14" textAnchor="middle">★</text>

      {/* grade */}
      <text
        x="100"
        y="100"
        dominantBaseline="central"
        textAnchor="middle"
        fill={c}
        fontSize="62"
        fontWeight="900"
        fontFamily="'Fraunces','Noto Serif SC',serif"
      >
        {grade}
      </text>
    </svg>
  );
}
