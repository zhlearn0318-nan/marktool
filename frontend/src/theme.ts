import type { ThemeConfig } from "antd";

/** 启明合规中枢 — Ant Design 主题：墨蓝主色 + 香槟金点缀 + 衬线标题 */
export const theme: ThemeConfig = {
  token: {
    colorPrimary: "#0e2e5a",
    colorInfo: "#0e2e5a",
    colorSuccess: "#2e9e6b",
    colorWarning: "#c9892a",
    colorError: "#c0392b",
    colorTextBase: "#0c1a2e",
    colorBgLayout: "#f5f6f8",
    borderRadius: 10,
    borderRadiusLG: 14,
    controlHeight: 40,
    fontFamily:
      "'Manrope', 'Noto Sans SC', system-ui, -apple-system, 'Segoe UI', sans-serif",
    fontSize: 14,
    colorBorder: "#e5e9f0",
  },
  components: {
    Button: {
      fontWeight: 600,
      primaryShadow: "none",
      defaultShadow: "none",
    },
    Card: {
      borderRadiusLG: 14,
      boxShadowTertiary:
        "0 1px 2px rgba(12,26,46,0.04), 0 8px 24px -12px rgba(12,26,46,0.16)",
    },
    Select: { controlHeight: 44 },
    Collapse: { headerBg: "transparent" },
    Tag: { borderRadiusSM: 999 },
  },
};
