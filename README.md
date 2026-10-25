# 源迹 TraceMark（MarkTool）

AIGC 文件元数据标注、检测与修复平台。仓库包含 React 前端、图片检测/报告服务，以及统一的多格式元数据服务。当前实现围绕 GB 45438—2025 的七字段标识结构，支持在文件副本上写入标识、回读校验、检查内容完整性和下载结果。在此之上，多模态内容可以拆成成分文件分别标识后再重组；虚拟场景和交互场景按第 5.5、5.6 条写入显式文字提示。实现说明见 `docs/技术交接文档.md`。

## 当前能力

| 文件格式 | 元数据标注与回读 | 合规检测 | 元数据修复 |
|---|---|---|---|
| JPEG / PNG | 支持 | 支持 | 按修复计划执行 |
| MP4 | 支持 | 支持 | 按修复计划执行 |
| Markdown / PDF / HTML / DOCX | 支持 | 支持 | 按修复计划执行 |
| M4A / MP3 | 支持 | 暂未提供 | 暂未提供 |
| 多模态组合 | 拆分、分别打标、重组后再校验 | 按每个内容文件校验 | 不走修复工作台 |
| 虚拟场景 / 交互场景 | 显式提示 + 内容文件隐式标识 | 按第 5.5 / 5.6 条校验 | 不走修复工作台 |

修复需先生成计划、查看变更并确认执行；无法安全修复的文件会被拒绝或要求人工处理。原文件保留，结果另存为新文件。音视频写入不转码，通过 ffprobe 与短片段解码检查完整性；文档和图片使用各自的内容完整性校验。

单文件接口 `metadata-label-jobs`、`compliance-inspect`、`metadata-repair-*` 的行为与合并前的 main 一致。多模态和场景是这些适配器的调用方，不另写一套 M4A、MP3 或文档载体。字幕和纯文本没有对应容器，仍写在文件尾段。C2PA 互转、数字签名、AI 生成概率识别及信号层鲁棒水印不属于当前已交付能力。

## 仓库结构

```text
backend/                         图片检测与历史报告服务（/api，端口 8000）
frontend/                        TraceMark 前端（Vite + React + TypeScript）
  src/pages/BrandHome.tsx        品牌首页
  src/components/Workspace.tsx   工作空间侧栏
  src/pages/MultimodalPage.tsx   多模态标识
  src/pages/ScenesPage.tsx       虚拟场景与交互场景
labeling-backend/                多格式标注、检测、修复、多模态与场景（/api/v1，端口 8002）
  app/adapters/                  视频、音频和文档适配器（多模态复用）
  app/metadata/                  图片适配器、文档载体、编号登记和修复模块
  app/modalities/                按角色分发到上述适配器；字幕/纯文本尾段
  app/multimodal/                拆分、重组、多模态打标/解析/校验
  app/scenes/                    虚拟场景与交互场景
  app/core/                      共享校验、任务流水线、存储和检测器
  config/                        默认配置与 ExifTool 命名空间配置
  tests/                         单元测试与真实文件集成测试
docs/                            开发手册、设计资料和技术交接文档
start-local.ps1                  Windows 本地启动入口
start-local.config.example.psd1  可选的本机工具路径配置示例
启动最新版.bat                    Windows 双击启动入口
```

## 环境准备

需要 Python 3.10+、Node.js 18+、ExifTool、FFmpeg（含 ffprobe）。ExifTool 和 FFmpeg 应加入 PATH；ExifTool 也可通过 `EXIFTOOL_PATH` 指定。虚拟场景绘制中文提示时，Windows 使用微软雅黑（`C:/Windows/Fonts/msyh.ttc`）或黑体；macOS 使用苹方或华文黑体；Linux 需安装文泉驿微米黑或 Noto Sans CJK。也可以在请求里用 `font_path` 指定字体。运行时、依赖包及外部工具不随仓库提交。

Windows PowerShell，在仓库根目录安装依赖：

```powershell
python -m venv .local-runtime
.\.local-runtime\Scripts\python.exe -m pip install -r backend/requirements.txt -r labeling-backend/requirements.txt
Set-Location frontend
npm ci
Set-Location ..
```

macOS / Linux：

```bash
python3 -m venv .local-runtime
.local-runtime/bin/python -m pip install -r backend/requirements.txt -r labeling-backend/requirements.txt
(cd frontend && npm ci)
# Debian / Ubuntu 额外安装拆分和中文绘制所需工具
sudo apt-get install -y ffmpeg libimage-exiftool-perl fonts-wqy-microhei
```

`labeling-backend/requirements.txt` 已包含 Mutagen（MP3 ID3）、Pillow（图片与场景绘制）等依赖，随上面的 pip 安装，不需要再单独加包。

## 启动

### Windows 一键启动

安装依赖并准备好外部工具后，双击 `启动最新版.bat`，或运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
# 只启动服务：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -NoBrowser
```

脚本优先使用 `.local-runtime\Scripts\python.exe`，在 PATH 中查找 Node.js 和 FFmpeg；ExifTool 还可放在 `.local-tools/exiftool/` 下。需要指定其他路径时，复制 `start-local.config.example.psd1` 为 `start-local.config.psd1` 并填写。该本机配置不会提交到 Git。

脚本在后台启动三个服务，检测到健康的现有服务时直接复用。日志位于根目录的 `backend-local.*.log`、`labeling-local.*.log` 和 `frontend-local.*.log`。修改后端代码后需先停止对应服务再重新启动。多模态和场景没有单独进程，随 labeling-backend 一起在 8002 提供。

### 手动启动

在三个终端分别运行；以下为 macOS / Linux 写法，Windows 将 Python 路径换为 `..\.local-runtime\Scripts\python.exe`：

```bash
# 终端一
cd backend
../.local-runtime/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
# 终端二
cd labeling-backend
../.local-runtime/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

```bash
# 终端三
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

访问 http://127.0.0.1:5173/ 。开发前端将 `/api/v1` 转发至 8002，将 `/api` 转发至 8000。生产静态部署需配置同样的反向代理；`npm run build` 仅生成前端文件，不启动后端。

前端入口在新版工作空间：顶栏和侧栏都有「多模态」「场景」。品牌首页仍是 `#welcome`。

## API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/metadata-label-jobs` | 上传文件与标注请求，创建异步任务 |
| GET | `/api/v1/metadata-label-jobs/{job_id}` | 查询状态与校验结果 |
| GET | `/api/v1/metadata-label-jobs/{job_id}/output` | 下载标注文件 |
| POST | `/api/v1/compliance-inspect` | 只读检查图片、视频或文档 |
| POST | `/api/v1/metadata-repair-plans` | 生成修复计划 |
| GET | `/api/v1/metadata-repair-plans/{plan_id}` | 查询修复计划 |
| POST | `/api/v1/metadata-repair-jobs` | 确认计划，创建修复任务 |
| GET | `/api/v1/metadata-repair-jobs/{job_id}` | 查询修复结果 |
| GET | `/api/v1/metadata-repair-jobs/{job_id}/output` | 下载修复文件 |
| POST | `/api/v1/multimodal/label` | 拆分或接收多文件，写入并校验成分与重组结果 |
| POST | `/api/v1/multimodal/parse` | 读出成分和重组文件中的隐式标识 |
| POST | `/api/v1/multimodal/verify` | 校验份数、附录 E 和重组形态 |
| POST | `/api/v1/scenes/virtual/label` | 虚拟场景起始画面显式标识和隐式标识 |
| POST | `/api/v1/scenes/virtual/parse` | 解析虚拟场景包 |
| POST | `/api/v1/scenes/virtual/verify` | 校验虚拟场景 |
| POST | `/api/v1/scenes/interactive/label` | 交互界面显式标识，可选内容文件隐式标识 |
| POST | `/api/v1/scenes/interactive/parse` | 解析交互场景包 |
| POST | `/api/v1/scenes/interactive/verify` | 校验交互场景 |
| GET | `/api/v1/composite-bundles/{bundle_id}` | 下载多模态或场景结果包 |
| GET | `/api/v1/health` | 元数据服务健康状态和能力列表 |
| POST | `/api/detect` | 旧版图片报告入口 |
| GET | `/api/report/{id}` | 查询图片检测报告 |
| GET | `/api/health` | 图片报告服务健康检查 |

请求示例见 [元数据服务说明](labeling-backend/README.md)。运行后的交互文档分别为 http://127.0.0.1:8002/docs 和 http://127.0.0.1:8000/docs 。

## 多模态与场景用法

在 `labeling-backend` 目录：

```bash
# 把带音频和字幕的 MP4 拆开打标，并在重组视频上只保留一份标识
python -m app.cli multimodal label \
  --video sample.mp4 \
  --produce-id PID-MM-1 \
  --producer ORG_1565201000000016 \
  --output out/multimodal

# 文本 + 图片，重组为只含一个 AIGC.json 的包
python -m app.cli multimodal label \
  --text note.txt --image cover.png \
  --produce-id PID-TI-1 \
  --producer ORG_1565201000000016 \
  --output out/text-image

# 虚拟场景：生成起始画面，写入显式文字和隐式标识
python -m app.cli scene virtual label \
  --text 人工智能生成合成 --position bottom_right \
  --produce-id PID-VS-1 \
  --producer ORG_1565201000000016 \
  --output out/virtual

# 交互场景：界面提示；可选地把一份文本内容文件一起打上隐式标识
python -m app.cli scene interactive label \
  --text AI生成 --placement near_content \
  --content reply.txt --content-role text \
  --produce-id PID-IS-1 \
  --producer ORG_1565201000000016 \
  --output out/interactive
```

MP3 成分使用与单文件打标相同的 ID3 `TXXX:AIGC`，不再先转成 M4A。Markdown、PDF、HTML、DOCX 同样走各自已有载体。只有 `.txt`、`.vtt`、`.srt` 使用尾段。

## 验证

Windows，从仓库根目录分别执行：

```powershell
Set-Location backend
..\.local-runtime\Scripts\python.exe -m pytest -q
Set-Location ../labeling-backend
$env:AIGC_REQUIRE_TOOLS = '1'
..\.local-runtime\Scripts\python.exe -m pytest tests -q
Set-Location ../frontend
npx tsc --noEmit
npm run build
```

macOS / Linux：

```bash
(cd backend && ../.local-runtime/bin/python -m pytest -q)
(cd labeling-backend && AIGC_REQUIRE_TOOLS=1 ../.local-runtime/bin/python -m pytest tests -q)
(cd frontend && npx tsc --noEmit && npm run build)
```

完整验收需安装 ExifTool、ffmpeg 和 ffprobe。`AIGC_REQUIRE_TOOLS=1` 将测试跳过视为失败，避免缺失工具时把局部测试误记为完整通过。

## 配置与资料

- [元数据服务配置](labeling-backend/config/default_config.yaml)
- [GB 45438 元数据标注开发指南](docs/gb45438-metadata-labeling-development-guide.md)
- [技术交接文档](docs/技术交接文档.md)
- 载体验证：[MP4](labeling-backend/docs/mp4-carrier-validation.md)、[M4A](labeling-backend/docs/m4a-carrier-validation.md)、[MP3](labeling-backend/docs/mp3-carrier-validation.md)
- [前端静态素材来源](frontend/public/assets/README.md)

仓库只包含源码、必要文档和界面资源。上传文件、任务数据库、测试产物、日志、本机路径配置和虚拟环境均保留在本地。

## 许可证

项目代码采用 [MIT License](LICENSE)。第三方字体、图片及依赖遵循各自许可，来源见上述素材说明。
