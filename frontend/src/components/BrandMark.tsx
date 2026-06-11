/** 启明 · 合规印章式徽标：盾形 + 金色对勾 + 中心“标”意象 */
export default function BrandMark({ size = 38 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="源迹 TraceMark"
    >
      <defs>
        <linearGradient id="bm-gold" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#EAD7AC" />
          <stop offset="1" stopColor="#B88E44" />
        </linearGradient>
      </defs>
      {/* shield */}
      <path
        d="M24 3.5 41 9.5v12c0 11.2-7.1 19.4-17 23-9.9-3.6-17-11.8-17-23v-12L24 3.5Z"
        fill="#0A2347"
        stroke="url(#bm-gold)"
        strokeWidth="1.6"
      />
      {/* inner ring */}
      <circle cx="24" cy="22" r="11.5" stroke="url(#bm-gold)" strokeWidth="1" opacity="0.55" />
      {/* checkmark */}
      <path
        d="M17.5 22.5 22 27l8.5-9"
        stroke="url(#bm-gold)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
