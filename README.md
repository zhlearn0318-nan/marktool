# AIGC 标识合规平台（MarkTool）

本仓库面向 GB 45438—2025 文件元数据隐式标识，包含图片检测/报告原型（后端 + 前端），以及视频（MP4）后端元数据打标服务。**视频（MP4）打标模块为 feature/video-metadata 分支交付物**：后端接口与前端打标页均已实现并已打通；图片适配器由队友负责。

## 当前实现范围

| 能力 | 状态 | 说明 |
|---|---|---|
| 视频（MP4）AIGC 元数据打标 | 已实现 | ExifTool 写入 `XMP-aigc:AIGC`，不转码，输出 `carrier = mp4-aigc-v1` |
| 已有标识 `reject` / `replace` | 已实现 | 全标签扫描（含旧载体 `QuickTime:Comment`）；`reject` → 409，`replace` → 整体移除 |
| 七字段严格 Schema | 已实现 | 外层 `AIGC`、七字段必填（含 `ReservedCode1/2`）、`Label` 枚举、未知字段拒绝 |
| 媒体完整性校验 | 已实现 | ffprobe 对比写入前后的时长/分辨率/轨道/编解码器，ffmpeg 解码前 2 秒确认可播放 |
| 异步任务与持久化 | 已实现 | `/api/v1` 异步创建/查询/下载，SQLite 登记 + 原子发布 + 保留策略 |
| MP4 合规检测（详细诊断） | 已实现 | `/api/v1/compliance-inspect`：只读检测 → 合规/不合规/未检出/无法判定四档；多份标识逐字段对比（`FIELDS_DISAGREE`）、损坏定位（`METADATA_REGION_WIPED` 点名原子偏移、`BAD_JSON` 截断到字段值段）、每条候选记录带 `location` 载体物理位置 |
| 图片 XMP 检测与合规评级（MVP） | 已实现 | `/api/detect` 全量检测 → 报告（A / B / C / 不合规） |
| 前端（源迹 TraceMark）· 检测/报告 | 已实现 | Vite + React + Antd，接入 `/api/detect` 上传检测与报告页 |
| 视频打标前端页面 | 已实现 | 「视频打标」页：上传 MP4 → 创建任务 → 轮询进度 → 下载结果（接入 `/api/v1`，ProduceID 自动生成） |
| 视频合规检测前端页面 | 已实现 | 「视频合规检测」页：上传 MP4 → 结论四档 + 问题清单 + AIGC 候选（含 `location`）+ 原始 JSON（接入 `/api/v1/compliance-inspect`） |
| 图片（JPEG/PNG）打标适配器 | 未实现 | 队友交付；能力开关 `false`，前端据此禁用图片入口 |
| C2PA 互转、概率检测、数字签名 | 不在本阶段 | 首期只做文件元数据隐式标识 |

## 关键目录

```text
.
├── backend/             # 检测/报告 MVP：图片 XMP 读取、探针引擎、合规评级（FastAPI）
├── frontend/            # 源迹 TraceMark：Vite + React + Antd，上传检测/报告 + 视频打标页
├── labeling-backend/    # 视频（MP4）元数据打标服务（本分支交付）
│   ├── app/
│   │   ├── adapters/video.py   # 视频适配器：MP4 写入/回读/replace/媒体完整性
│   │   ├── core/               # 七字段模型、全标签 XMP 读取器、BMFF 探针、合规检查器（bmff/inspector）、流水线、任务存储
│   │   ├── api/                # /api/v1 异步标注接口 + compliance-inspect 合规检测
│   │   └── config/             # ExifTool XMP-aigc 自定义命名空间配置
│   └── docs/                   # 视频载体验证记录、手册要点提炼
├── docs/                 # 开发手册与方案设计
└── LICENSE
```

## 环境准备

两个后端服务各自独立安装依赖。以下命令同时给出 macOS/Linux 与 Windows（PowerShell）两种写法。

### 检测/报告 MVP（backend）

```bash
# macOS / Linux
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```powershell
# Windows PowerShell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 视频打标服务（labeling-backend）

```bash
# macOS / Linux
cd labeling-backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```powershell
# Windows PowerShell
cd labeling-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 视频打标服务需系统已装 ExifTool、ffprobe、ffmpeg。ExifTool 若不在 PATH 中，可设置环境变量 `EXIFTOOL_PATH`；开发环境默认数据/任务目录为各服务下的 `storage/`，已被 Git 忽略。

## 运行

### 视频打标服务（`/api/v1`）

```bash
# macOS / Linux
cd labeling-backend
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
```

```powershell
# Windows PowerShell
cd labeling-backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

### 检测/报告 MVP（`/api`）

```bash
# macOS / Linux
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000
```

```powershell
# Windows PowerShell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

### 前端（源迹 TraceMark）

```bash
cd frontend
npm install
npm run dev
```

> npm 命令在 macOS / Windows 下通用。前端监听 `http://localhost:5173`，代理规则：`/api` → 后端 `:8000`（检测 MVP），`/api/v1` → 后端 `:8002`（视频打标）。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/metadata-label-jobs` | 创建视频打标任务（`multipart`: `file` + `request`），返回 `202` |
| `GET` | `/api/v1/metadata-label-jobs/{job_id}` | 查询任务状态/阶段/校验结果 |
| `GET` | `/api/v1/metadata-label-jobs/{job_id}/output` | 下载打标结果文件（仅 `succeeded`） |
| `GET` | `/api/v1/health` | 健康检查 + 能力列表（`video/mp4: true`） |
| `POST` | `/api/v1/compliance-inspect` | MP4 合规检测（`multipart`: `file`，只读）：同步返回 `conclusion`/`reason_code`/`issues`/`candidates`（候选带 `location`）/`bmff` 等 |
| `POST` | `/api/detect` | 上传图片，全量检测，返回合规报告（MVP） |
| `GET` | `/api/report/{id}` | 查询历史检测报告（MVP） |
| `GET` | `/api/health` | 健康检查（MVP） |

> **联调状态**：前端 TraceMark 已接通视频打标与合规检测——顶部导航「视频打标」页上传 MP4 → `/api/v1/metadata-label-jobs` 创建任务 → 自动轮询进度 → 下载打标结果；「视频合规检测」页上传 MP4 → `/api/v1/compliance-inspect` 同步返回详细诊断报告。`/api/v1` 经 vite 代理转发至 `:8002`（labeling-backend），`/api` 转发至 `:8000`（检测 MVP），两个服务可同时运行。

## 测试

```bash
# macOS / Linux
cd backend && .venv/bin/python -m pytest -q                      # 32 个用例
cd labeling-backend && .venv/bin/python -m pytest tests/ -q      # 89 个用例
```

```powershell
# Windows PowerShell
cd backend; .\.venv\Scripts\python.exe -m pytest -q
cd labeling-backend; .\.venv\Scripts\python.exe -m pytest tests/ -q
```

> labeling-backend 的视频打标集成测试依赖 ExifTool / ffprobe / ffmpeg，需先安装。

## 文档

- 开发手册：`docs/gb45438-metadata-labeling-development-guide.md`
- 视频模块要点：`labeling-backend/docs/guide-keypoints.md`、`labeling-backend/docs/mp4-carrier-validation.md`
- 完整接口契约与 `request` 示例：`labeling-backend/README.md`

## 许可证

本项目采用 [MIT License](LICENSE)。
