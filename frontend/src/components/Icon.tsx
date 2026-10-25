export type IconName =
  | "arrow"
  | "scan"
  | "label"
  | "repair"
  | "grid"
  | "book"
  | "code"
  | "help"
  | "layers"
  | "image"
  | "video"
  | "check"
  | "chevron"
  | "menu"
  | "close"
  | "upload"
  | "shield";
const paths: Record<IconName, string> = {
  arrow: "M5 12h14m-6-6 6 6-6 6",
  scan: "M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3M7 12h10M7 8h6m-6 8h8",
  label:
    "m20 13-7 7a2 2 0 0 1-3 0l-7-7V4a1 1 0 0 1 1-1h9l7 7a2 2 0 0 1 0 3ZM7.5 7.5h.01",
  repair:
    "m14 6 4 4m-7-5a6 6 0 0 0-7 7l-1 8 8-1a6 6 0 0 0 7-7l3-3-6-6-4 2ZM4 20l7-7",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  book: "M12 5v16M3 3c3-1 6 0 9 2 3-2 6-3 9-2v16c-3-1-6 0-9 2-3-2-6-3-9-2V3Z",
  code: "m7 7-5 5 5 5m10-10 5 5-5 5m-3-14-4 18",
  help: "M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3m0 3h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  layers: "m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5",
  image:
    "M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm0 14 5-5 4 4 3-3 5 5M8 7h.01",
  video: "M3 4h18v16H3zM3 8h18M7 4v4m5-4v4m5-4v4m-7 4 5 3-5 3v-6Z",
  check: "m5 12 4 4L19 6",
  chevron: "m9 5 7 7-7 7",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "m6 6 12 12M6 18 18 6",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5",
  shield: "m12 3 9 4v5c0 5-4 8-9 10-5-2-9-5-9-10V7l9-4Zm-5 9 3 3 7-7",
};
export default function Icon({
  name,
  size = 20,
  className = "",
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
