# AIGC 标识合规平台 —— MVP 设计文档

- 日期：2026-06-10
- 范围：第三章文档定义的「一期 MVP」——**图片模态的检测 + 报告**
- 明确不做：打标/修复、标准互转、音频/视频模态、Celery/Redis 异步队列、欧盟法规规则集（接口预留，规则后续接入）

## 1. 目标与非目标

### 目标
把文档第 3.2 节板块二（合规检测）与板块三（检测报告）在**图片模态**上端到端跑通，作为可演示原型：上传一张图片 → 检测它是否按 GB 45438-2025 标注了 AIGC 标识 → 输出四部分结构的可视化报告。

核心命题（文档原话）：**"检测一张图有没有标"**。

### 非目标（YAGNI）
- 不做板块一（打标/修复）
- 不做板块四（C2PA ↔ 国标互转）
- 不做音频、视频、文本模态
- 不引入 Celery + Redis（单图检测为秒级，同步处理即可）
- 不实现真实的 AI 生成概率模型（文档明确这不是核心，MVP 占位）

## 2. 保真度策略（关键决策）

| 检测能力 | MVP 实现方式 | 真实/占位 |
|---|---|---|
| 隐式标识 - 元数据（XMP 中的 AIGC JSON） | Pillow `Image.getxmp()` 读取 → 提取 AIGC JSON → `jsonschema` 校验附录 E 结构 | **真实可跑** |
| 隐式标识 - 水印（TrustMark） | 定义 `WatermarkDetector` 插件接口，占位实现返回"未启用" | 占位（接口真实） |
| 显式标识 - 边角文字（OCR） | 定义 `ExplicitMarkDetector` 插件接口 + 字高≥最短边 5% 的校验骨架，占位 OCR 返回"需插件" | 占位（接口 + 几何校验真实） |
| AI 内容检测 | 报告中占位，标注"辅助参考、未启用" | 占位 |

设计原则：**核心元数据检测链路 100% 真实、在 Windows 上仅依赖 Pillow + jsonschema 即可运行，零 ExifTool / 零重型 ML 依赖**。重型能力（OCR、水印、AI 检测）全部走统一的插件接口，占位实现可被真实实现平滑替换，不改动调用方。

## 3. 架构

```
┌─────────────┐     POST /api/detect      ┌──────────────────────────┐
│  React 前端  │ ─────────(图片)─────────→ │      FastAPI 后端          │
│  (Ant Design)│ ←────(检测结果 + 报告)──── │                            │
└─────────────┘                            │  ┌──────────────────────┐ │
                                           │  │   检测引擎 (registry)  │ │
                                           │  │  ┌────────────────┐  │ │
                                           │  │  │ MetadataDetector│ 真实
                                           │  │  │ WatermarkDetector│占位
                                           │  │  │ ExplicitMarkDet. │占位
                                           │  │  └────────────────┘  │ │
                                           │  └──────────┬───────────┘ │
                                           │             ▼             │
                                           │      ReportBuilder         │
                                           │   (四部分报告 + 评级)        │
                                           └──────────────────────────┘
```

- **后端**：Python 3.11 + FastAPI，同步处理。
- **前端**：Vite + React + Ant Design。
- **存储**：上传文件落临时目录；检测结果以 `result_id` 存内存字典（MVP 不引数据库）。

### API
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/detect` | multipart 上传图片，可选 `target_regulation`（默认 `CN_GB45438`）。返回 `{result_id, detection, report}` |
| GET | `/api/report/{result_id}` | 取回已生成的报告 JSON |
| GET | `/api/health` | 健康检查 |

## 4. 检测引擎设计

### 4.1 插件接口
```python
class Detector(ABC):
    name: str
    modality: str          # "image"
    mark_type: str         # "implicit_metadata" | "implicit_watermark" | "explicit_text"
    def applicable(self, ctx: DetectionContext) -> bool: ...
    def detect(self, ctx: DetectionContext) -> list[CheckItem]: ...
```
- `DetectionContext`：封装文件路径、已解析的图片对象、目标法规、共享缓存（如已读出的 XMP，供多个 detector 复用）。
- `registry`：按 modality 收集 detector，`run_all(ctx)` 汇总所有 `CheckItem`。
- 新增模态/能力 = 新增一个 Detector 并注册，调用方不变。

### 4.2 图片模态 detector（对应文档板块二）
- **MetadataDetector（真实）**：`Image.getxmp()` 取 XMP dict → 定位 AIGC JSON（命名空间/键见 §5）→ 不存在则产出 ❌"未检出隐式元数据标识"；存在则 `jsonschema` 校验，缺字段 → ⚠️，格式错 → ⚠️，完整 → ✅。
- **ExplicitMarkDetector（占位）**：接口接收 OCR 结果（文字 + 包围盒），实现"是否含『AI/人工智能』+『生成/合成』关键词 + 位置在边角 + 字高 ≥ 最短边 5%"的判定逻辑（这部分逻辑真实）；OCR 后端占位，产出 ⚠️"显式标识检测需 OCR 插件，未启用"。
- **WatermarkDetector（占位）**：TrustMark 接口占位，产出 ⚠️"水印检测未启用"。

## 5. 数据模型

### 5.1 GB 45438 附录 E AIGC JSON schema
以 `backend/app/schemas/gb45438_appendix_e.json`（JSON Schema draft-07）落地。基于文档出现的字段与该标准公开结构**近似定义**，README 与文件头注明"待对照标准原文校准"。字段（首版）：
- `Label`（标识，如生成/合成类型）— 必填
- `ContentProducer`（生成服务提供者）— 必填
- `ProduceID`（生成内容编号）— 必填
- `ContentPropagator`（传播服务提供者）— 可选
- `PropagateID`（传播编号）— 可选
- `ReservedCode1` / `ReservedCode2`（预留）— 可选

### 5.2 Pydantic 模型
```python
class CheckStatus(str, Enum): PASS="pass"; FAIL="fail"; WARN="warn"
class CheckItem: id; title; status; detail; suggestion; mark_type
class DetectionResult: result_id; filename; modality; items: list[CheckItem]
class Report:
    provenance      # 标识溯源：从元数据还原的生产/传播链路节点
    compliance      # 合规报告：items + rating(A/B/C/不合规) + 整改建议汇总
    tamper          # 篡改检测：元数据vs水印一致性、显式vs隐式矛盾
    ai_detection    # AI内容检测：占位 {enabled: false, note}
```

### 5.3 合规评级规则（中国国标，图片）
- 必需项：隐式元数据标识存在且 schema 合规。
- 评级：全部 ✅ → A；元数据合规但显式/水印未验 → B（受插件限制）；元数据缺字段/格式错 → C；无任何标识 → 不合规。
- 每个 ❌/⚠️ 附带具体整改建议文案。

## 6. 前端

- **上传页**：拖拽/选择图片，选目标法规（默认中国国标），提交。
- **报告页**：
  - 合规评级徽章 + **合规雷达图**（各检测维度得分，AntD Charts / 轻量 SVG）
  - 逐项检测清单（✅/❌/⚠️ + 详情 + 整改建议，AntD Table/List）
  - **溯源链路图**（ContentProducer → ContentPropagator 节点流）
  - 原始元数据查看器（折叠 JSON）
  - 篡改检测、AI 检测区块（占位区块如实标注未启用）

## 7. 错误处理
- 非图片/损坏文件 → 400 + 友好提示，不抛栈。
- 图片无任何元数据 → 不是错误，正常产出"未检出标识"的检测结果（这正是核心场景之一）。
- 单个 detector 抛异常 → 捕获，该项记为 ⚠️"检测器异常"，不中断整体检测。

## 8. 测试
- **测试夹具脚本**：用 Pillow 生成图片并写入 XMP，覆盖：含完整 AIGC JSON、缺必填字段、JSON 格式错误、无任何元数据 四种。
- **pytest**：
  - MetadataDetector 对四类夹具的判定（pass/warn/fail/未检出）。
  - jsonschema 校验单测。
  - 合规评级规则单测。
  - API `/api/detect` 集成测试（上传夹具图片，断言报告结构与评级）。
- 前端：MVP 仅手动验证 + 一个冒烟渲染测试可选。

## 9. 目录结构
```
backend/
  app/
    main.py                      # FastAPI 实例 + 路由挂载
    api/routes.py                # /api/detect, /api/report, /api/health
    engine/
      base.py                    # Detector ABC, DetectionContext, CheckItem
      registry.py                # detector 注册与 run_all
      detectors/
        metadata_detector.py     # 真实
        explicit_mark_detector.py# 占位 + 几何校验逻辑
        watermark_detector.py    # 占位
    schemas/
      gb45438_appendix_e.json    # JSON Schema
      models.py                  # Pydantic
    report/
      builder.py                 # 组装四部分报告
      compliance_rules.py        # 中国国标图片规则 + 评级
    metadata/
      xmp_reader.py              # Pillow getxmp 封装 + AIGC JSON 提取
    storage.py                   # 临时文件 + 内存结果存储
  tests/
    fixtures.py                  # 生成测试图片
    test_metadata_detector.py
    test_schema.py
    test_compliance_rules.py
    test_api.py
  requirements.txt
frontend/
  (Vite + React + Ant Design)
  src/
    api.ts
    pages/UploadPage.tsx
    pages/ReportPage.tsx
    components/ComplianceRadar.tsx
    components/ProvenanceChain.tsx
    components/CheckList.tsx
README.md                        # 运行方式、依赖、标准近似说明
```

## 10. 验收标准
1. `pip install -r requirements.txt` 后 `uvicorn` 启动后端，仅依赖 Pillow + jsonschema + FastAPI 等纯 Python 包即可运行（无需 ExifTool / ML）。
2. 前端 `npm install && npm run dev` 启动，可上传图片并看到四部分报告与可视化。
3. 上传一张内嵌合规 AIGC JSON 的图片 → 报告评级 A/B、溯源链路有节点。
4. 上传一张无标识的普通图片 → 报告判"未检出标识 / 不合规"，列出整改建议。
5. `pytest` 全绿。
