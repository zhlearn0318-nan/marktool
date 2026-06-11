# AIGC 标识合规平台（MVP）

图片模态的 AIGC 标识合规**检测 + 报告**原型。覆盖 GB 45438-2025 隐式元数据标识的真实检测；OCR/水印/AI 检测为可插拔占位。

## 后端
    cd backend
    .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000

## 前端
    cd frontend
    npm install && npm run dev

## 测试
    cd backend && .venv/Scripts/python -m pytest -v

> 说明：GB 45438-2025 附录 E 的 AIGC JSON 结构按公开信息近似定义，待对照标准原文校准。
