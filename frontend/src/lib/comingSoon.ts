import { message } from "antd";

/** 装饰性按钮的统一反馈：功能在后续版本开放，避免“死按钮”观感。 */
export function comingSoon(feature?: string) {
  message.info({
    content: feature ? `「${feature}」将在后续版本开放，敬请期待` : "该功能将在后续版本开放，敬请期待",
    duration: 2.2,
  });
}
