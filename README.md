# AIGC 标识合规平台（MVP）

面向图片模态的 AIGC 标识合规**检测 + 报告**原型，基于 GB 45438-2025 标准框架实现隐式元数据的真实检测，其余探针为可插拔占位。

---

## 产品板块概览

| 板块 | 名称 | 说明 |
|------|------|------|
| 板块一 | 打标 / 修复 | 为 AIGC 内容写入合规标识（元数据、水印等） |
| 板块二 | **合规检测** ← **本 MVP** | 读取并验证已有标识的完整性与合规性 |
| 板块三 | **检测报告** ← **本 MVP** | 生成结构化合规评级报告（A / B / C / 不合规） |
| 板块四 | 标准互转 | 不同标准格式之间的 AIGC 元数据互转 |

> **MVP 范围**：本版本仅实现 **板块二（检测）+ 板块三（报告）**，仅支持 **图片（IMAGE）** 模态。

---

## MVP 范围与保真度

| 能力 | 状态 | 说明 |
|------|------|------|
| 元数据检测（XMP → AIGC JSON → JSON Schema 校验） | **真实实现** | 读取 PNG XMP 块，提取 AIGC JSON，按附录 E 结构校验必填字段 |
| OCR 显式文字水印检测 | **占位符** ⚠️ 未启用 | 接口预留，当前返回"未检测"警告项 |
| TrustMark 隐式水印检测 | **占位符** ⚠️ 未启用 | 接口预留，当前返回"未检测"警告项 |
| AI 内容溯源检测 | **占位符** ⚠️ 未启用 | 接口预留，当前返回"未检测"警告项 |
| 评级规则（A / B / C / 不合规） | **真实实现** | 依据各探针结果按规则引擎计算最终评级 |
| 报告存储与检索 | **真实实现** | 内存存储，支持 `GET /api/report/{id}` 查询 |

---

## 目录结构

```
.
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI 路由（detect、report、health）
│   │   ├── engine/
│   │   │   ├── base.py     # 探针基类
│   │   │   ├── registry.py # 探针注册表
│   │   │   └── detectors/  # 各探针实现（元数据、显式标记、水印）
│   │   ├── metadata/       # XMP 读取与 AIGC JSON 提取
│   │   ├── report/         # 合规规则引擎与报告构建
│   │   ├── schemas/        # Pydantic 模型、JSON Schema、校验逻辑
│   │   ├── storage.py      # 报告内存存储
│   │   └── main.py         # FastAPI 应用入口
│   └── tests/              # pytest 测试套件（32 个用例）
└── frontend/
    └── src/
        ├── pages/          # UploadPage（上传检测）、ReportPage（报告详情）
        ├── components/     # CheckList、ComplianceRadar、ProvenanceChain
        ├── api.ts          # 后端 API 调用封装
        └── types.ts        # TypeScript 类型定义
```

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/detect` | 上传图片，触发全量检测，返回报告（含评级） |
| `GET` | `/api/report/{id}` | 按报告 ID 查询历史报告 |
| `GET` | `/api/health` | 健康检查，返回 `{"status": "ok"}` |

**请求示例（detect）**

```bash
curl -X POST http://localhost:8000/api/detect \
  -F "file=@sample_marked.png"
```

**响应结构（摘要）**

```json
{
  "report_id": "...",
  "report": {
    "compliance": { "rating": "B", "summary": "..." },
    "checks": [...],
    "provenance": [...]
  }
}
```

---

## 运行

### 后端

```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

服务监听 `http://localhost:8000`，API 文档见 `http://localhost:8000/docs`。

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端监听 `http://localhost:5173`，已配置 `/api` 代理至后端 `:8000`。

---

## 测试

```bash
cd backend
.venv/Scripts/python -m pytest -v
```

当前共 32 个测试用例，全部通过。

---

## 快速体验（端到端数据路径验证）

在 `backend/` 目录下生成测试样本：

```bash
cd backend
.venv/Scripts/python -c "from tests.fixtures import make_png, VALID_AIGC; make_png('sample_marked.png', aigc_dict=VALID_AIGC); make_png('sample_unmarked.png')"
```

在进程内通过 TestClient 验证检测结果：

```bash
.venv/Scripts/python -c "
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
for name in ['sample_marked.png', 'sample_unmarked.png']:
    r = c.post('/api/detect', files={'file': (name, open(name,'rb').read(), 'image/png')}).json()
    print(name, '->', r['report']['compliance']['rating'])
"
```

预期输出：

```
sample_marked.png -> A   # （或 B，取决于占位探针权重）
sample_unmarked.png -> 不合规
```

> `sample_marked.png` / `sample_unmarked.png` 为临时产物，已加入 `.gitignore`，请勿提交。

---

## 注意事项

> GB 45438-2025 附录 E 的 AIGC JSON 结构按公开信息近似定义，待对照标准原文校准。
