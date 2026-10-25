import type { ThemeConfig } from "antd";

/** 启明合规中枢 — Ant Design 主题：墨蓝主色 + 香槟金点缀 + 衬线标题 */
export const theme: ThemeConfig = {
  token: {
    colorPrimary: "#20384f",
    colorInfo: "#20384f",
    colorSuccess: "#2e9e6b",
    colorWarning: "#c9892a",
    colorError: "#c0392b",
    colorTextBase: "#263d45",
    colorBgLayout: "#f8f8f5",
    borderRadius: 6,
    borderRadiusLG: 10,
    controlHeight: 40,
    fontFamily:
      "'Satoshi', 'Microsoft YaHei', 'PingFang SC', system-ui, sans-serif",
    fontSize: 13,
    colorBorder: "#dce2d6",
  },
  components: {
    Button: {
      fontWeight: 600,
      primaryShadow: "none",
      defaultShadow: "none",
    },
    Card: {
      borderRadiusLG: 10,
      boxShadowTertiary:
        "0 1px 2px rgba(12,26,46,0.04), 0 8px 24px -12px rgba(12,26,46,0.16)",
    },
    Select: { controlHeight: 44 },
    Collapse: { headerBg: "transparent" },
    Tag: { borderRadiusSM: 999 },
  },
};
