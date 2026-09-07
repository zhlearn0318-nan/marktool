export type View =
  | "home"
  | "videoInspect"
  | "overview"
  | "regulations"
  | "standards"
  | "api"
  | "help"
  | "console"
  | "report"
  | "labeling";

export const NAV: { label: string; view: View }[] = [
  { label: "平台概览", view: "overview" },
  { label: "合规检测", view: "home" },
  { label: "视频合规检测", view: "videoInspect" },
  { label: "视频打标", view: "labeling" },
  { label: "法规库", view: "regulations" },
  { label: "标准互转", view: "standards" },
  { label: "开发者 API", view: "api" },
  { label: "帮助中心", view: "help" },
];
