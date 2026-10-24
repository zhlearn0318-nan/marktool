# AIGC 标识合规平台（MarkTool）

本仓库面向 GB 45438—2025，包含图片检测/报告原型，以及打标服务。打标服务已覆盖图片、视频、音频、文本的文件元数据隐式标识，并能把复合内容拆开标识后再重组。虚拟场景和交互场景还会按标准写入显式文字提示。实现说明见 `docs/技术交接文档.md`。

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
| 图片（JPEG/PNG）打标适配器 | 已实现 | 与视频共用 `/api/v1`，载体为 XMP-aigc |
| 文本隐式标识 | 已实现 | 正文后的 `GB45438-AIGC` 元数据段，解析时与正文分离 |
| 音频隐式标识 | 已实现 | M4A 的 `XMP-aigc:AIGC`。WAV/MP3 会先转为 AAC |
| 多模态标识 | 已实现 | 拆分画面、音频、字幕（及其他模态组合），分别打标、解析、校验，再重组为只含一份标识的文件或包 |
| 虚拟场景 | 已实现 | 起始画面显式文字（第 5.5 条）+ 画面文件中的一份隐式标识 |
| 交互场景 | 已实现 | 界面持续文字提示（第 5.6 条）；若附带内容文件，再写一份隐式标识 |
| C2PA 互转、概率检测、数字签名、内容水印 | 不在本阶段 | 第 6.2 条的内容隐式标识标准不做要求 |

## 关键目录

```text
.
├── backend/             # 检测/报告 MVP：图片 XMP 读取、探针引擎、合规评级（FastAPI）
├── frontend/            # 源迹 TraceMark：Vite + React + Antd，上传检测/报告 + 视频打标页
├── labeling-backend/    # 元数据打标服务：图片、视频、音频、文本、多模态与场景
│   ├── app/
│   │   ├── adapters/video.py   # 视频适配器：MP4 写入/回读/replace/媒体完整性
│   │   ├── modalities/         # 文本尾段、音频 M4A，以及按模态分发到既有适配器
│   │   ├── multimodal/         # 拆分、重组、多模态打标/解析/校验
│   │   ├── scenes/             # 虚拟场景与交互场景的显式标识和隐式标识
│   │   ├── core/               # 七字段模型、全标签 XMP 读取器、BMFF 探针、合规检查器、流水线
│   │   ├── api/                # /api/v1 标注、合规检测、多模态与场景
│   │   └── cli.py              # python -m app.cli
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

> 打标服务需系统已装 **ExifTool**、**ffprobe**、**ffmpeg**。多模态拆分和虚拟场景截取视频帧都调用 ffmpeg，且不把参数拼进 shell。ExifTool 若不在 PATH 中，可设置环境变量 `EXIFTOOL_PATH`。虚拟场景要绘制中文提示，系统需有中文字体（例如文泉驿微米黑、Noto Sans CJK 或微软雅黑）。开发环境默认数据目录为各服务下的 `storage/`，已被 Git 忽略。

安装示例：

```bash
# Debian / Ubuntu
sudo apt-get install -y ffmpeg libimage-exiftool-perl fonts-wqy-microhei
```

```powershell
# Windows：安装 ffmpeg、exiftool，并确认微软雅黑（msyh.ttc）可用
```

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
| `POST` | `/api/v1/multimodal/label` | 多模态打标。`file` 为待拆分视频，或用 `file_text` / `file_image` / `file_audio` / `file_video` 分别提交。`request` 为 JSON |
| `POST` | `/api/v1/multimodal/parse` | 解析成分文件与重组文件中的隐式标识 |
| `POST` | `/api/v1/multimodal/verify` | 校验份数、附录 E、重组结果 |
| `POST` | `/api/v1/scenes/virtual/label` | 虚拟场景：起始画面显式标识 + 隐式标识 |
| `POST` | `/api/v1/scenes/virtual/parse` | 解析虚拟场景包 |
| `POST` | `/api/v1/scenes/virtual/verify` | 校验第 5.5 条与第 6.1 条 |
| `POST` | `/api/v1/scenes/interactive/label` | 交互场景界面显式标识，可选内容文件隐式标识 |
| `POST` | `/api/v1/scenes/interactive/parse` | 解析交互场景描述或场景包 |
| `POST` | `/api/v1/scenes/interactive/verify` | 校验第 5.6 条；有内容文件时再校验第 6.1 条 |
| `GET` | `/api/v1/composite-bundles/{bundle_id}` | 下载上述操作的 zip 结果包 |
| `POST` | `/api/detect` | 上传图片，全量检测，返回合规报告（MVP） |
| `GET` | `/api/report/{id}` | 查询历史检测报告（MVP） |
| `GET` | `/api/health` | 健康检查（MVP） |

> **联调状态**：前端 TraceMark 已接通视频打标与合规检测——顶部导航「视频打标」页上传 MP4 → `/api/v1/metadata-label-jobs` 创建任务 → 自动轮询进度 → 下载打标结果；「视频合规检测」页上传 MP4 → `/api/v1/compliance-inspect` 同步返回详细诊断报告。`/api/v1` 经 vite 代理转发至 `:8002`（labeling-backend），`/api` 转发至 `:8000`（检测 MVP），两个服务可同时运行。

## 多模态与场景用法

在 `labeling-backend` 目录下。HTTP 与命令行调用同一套函数。

```bash
# 把带音频和字幕的 MP4 拆开打标，并在重组视频上只保留一份标识
python -m app.cli multimodal label \
  --video sample.mp4 \
  --producer ORG_1565201000000016 \
  --produce-id PID-MM-001 \
  --output ./out-mm

python -m app.cli multimodal parse --input ./out-mm/bundle.zip
python -m app.cli multimodal verify --input ./out-mm/recombined/composite.mp4

# 文本 + 图片，重组为只含一个 AIGC.json 的包
python -m app.cli multimodal label \
  --text caption.txt --image cover.png \
  --producer ORG_1565201000000016 \
  --output ./out-ti

# 虚拟场景：生成起始画面，写入显式文字和隐式标识
python -m app.cli scene virtual label \
  --producer ORG_1565201000000016 \
  --text "人工智能生成合成" \
  --position bottom_right \
  --output ./out-virtual

python -m app.cli scene virtual verify --input ./out-virtual/virtual-scene.zip

# 交互场景：界面提示；可选地把一份文本内容文件一起打上隐式标识
python -m app.cli scene interactive label \
  --text "AI生成" \
  --placement near_content \
  --content reply.txt --content-role text \
  --producer ORG_1565201000000016 \
  --output ./out-interactive
```

```bash
curl -X POST http://127.0.0.1:8002/api/v1/multimodal/label \
  -F "file=@sample.mp4;type=video/mp4" \
  -F 'request={"standard":"GB45438-2025","existing_metadata_policy":"reject","AIGC":{"Label":"1","ContentProducer":"ORG_1565201000000016","ProduceID":"PID-MM-001","ReservedCode1":"","ContentPropagator":"ORG_1565201000000016","PropagateID":"PID-MM-001","ReservedCode2":""}}'
```

前端「多模态」「场景」页面对应上述接口。代理规则不变：`/api/v1` → `:8002`。

## 测试

```bash
# macOS / Linux
cd backend && .venv/bin/python -m pytest -q
cd labeling-backend && .venv/bin/python -m pytest tests/ -q
```

```powershell
# Windows PowerShell
cd backend; .\.venv\Scripts\python.exe -m pytest -q
cd labeling-backend; .\.venv\Scripts\python.exe -m pytest tests/ -q
```

> labeling-backend 的视频打标集成测试依赖 ExifTool / ffprobe / ffmpeg，需先安装。

## 文档

- 技术交接：`docs/技术交接文档.md`（多模态、虚拟场景、交互场景的实现说明与条款对照）
- 开发手册：`docs/gb45438-metadata-labeling-development-guide.md`
- 视频模块要点：`labeling-backend/docs/guide-keypoints.md`、`labeling-backend/docs/mp4-carrier-validation.md`
- 完整接口契约与 `request` 示例：`labeling-backend/README.md`

## 许可证

本项目采用 [MIT License](LICENSE)。
