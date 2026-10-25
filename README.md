# 源迹 TraceMark（MarkTool）

AIGC 文件元数据标注、检测与修复平台。仓库包含 React 前端、图片检测/报告服务，以及统一的多格式元数据服务。当前实现围绕 GB 45438—2025 的七字段标识结构，支持在文件副本上写入标识、回读校验、检查内容完整性和下载结果。

## 当前能力

| 文件格式 | 元数据标注与回读 | 合规检测 | 元数据修复 |
|---|---|---|---|
| JPEG / PNG | 支持 | 支持 | 按修复计划执行 |
| MP4 | 支持 | 支持 | 按修复计划执行 |
| Markdown / PDF / HTML / DOCX | 支持 | 支持 | 按修复计划执行 |
| M4A / MP3 | 支持 | 暂未提供 | 暂未提供 |

修复需先生成计划、查看变更并确认执行；无法安全修复的文件会被拒绝或要求人工处理。原文件保留，结果另存为新文件。音视频写入不转码，通过 ffprobe 与短片段解码检查完整性；文档和图片使用各自的内容完整性校验。

本版本未集成多模态拆分、虚拟场景与交互场景标识模块。C2PA 互转、数字签名、AI 生成概率识别及信号层鲁棒水印也不属于当前已交付能力。

## 仓库结构

```text
backend/                         图片检测与历史报告服务（/api，端口 8000）
frontend/                        TraceMark 前端（Vite + React + TypeScript）
labeling-backend/                 多格式标注、检测与修复服务（/api/v1，端口 8002）
  app/adapters/                  视频、音频和文档适配器
  app/metadata/                  图片适配器、文档载体、编号登记和修复模块
  app/core/                      共享校验、任务流水线、存储和检测器
  config/                        默认配置与 ExifTool 命名空间配置
  tests/                         单元测试与真实文件集成测试
docs/                            开发手册和设计资料
start-local.ps1                  Windows 本地启动入口
start-local.config.example.psd1  可选的本机工具路径配置示例
启动最新版.bat                    Windows 双击启动入口
```

## 环境准备

需要 Python 3.10+、Node.js 18+、ExifTool、FFmpeg（含 ffprobe）。ExifTool 和 FFmpeg 应加入 PATH；ExifTool 也可通过 `EXIFTOOL_PATH` 指定。运行时、依赖包及外部工具不随仓库提交。

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
```

## 启动

### Windows 一键启动

安装依赖并准备好外部工具后，双击 `启动最新版.bat`，或运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1
# 只启动服务：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1 -NoBrowser
```

脚本优先使用 `.local-runtime\Scripts\python.exe`，在 PATH 中查找 Node.js 和 FFmpeg；ExifTool 还可放在 `.local-tools/exiftool/` 下。需要指定其他路径时，复制 `start-local.config.example.psd1` 为 `start-local.config.psd1` 并填写。该本机配置不会提交到 Git。

脚本在后台启动三个服务，检测到健康的现有服务时直接复用。日志位于根目录的 `backend-local.*.log`、`labeling-local.*.log` 和 `frontend-local.*.log`。修改后端代码后需先停止对应服务再重新启动。

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
| GET | `/api/v1/health` | 元数据服务健康状态和能力列表 |
| POST | `/api/detect` | 旧版图片报告入口 |
| GET | `/api/report/{id}` | 查询图片检测报告 |
| GET | `/api/health` | 图片报告服务健康检查 |

请求示例见 [元数据服务说明](labeling-backend/README.md)。运行后的交互文档分别为 http://127.0.0.1:8002/docs 和 http://127.0.0.1:8000/docs 。

## 验证

Windows，从仓库根目录分别执行：

```powershell
Set-Location backend
..\.local-runtime\Scripts\python.exe -m pytest -q
Set-Location ../labeling-backend
$env:AIGC_REQUIRE_TOOLS = '1'
..\.local-runtime\Scripts\python.exe -m pytest tests -q
Set-Location ../frontend
npm run build
```

macOS / Linux：

```bash
(cd backend && ../.local-runtime/bin/python -m pytest -q)
(cd labeling-backend && AIGC_REQUIRE_TOOLS=1 ../.local-runtime/bin/python -m pytest tests -q)
(cd frontend && npm run build)
```

完整验收需安装 ExifTool、ffmpeg 和 ffprobe。`AIGC_REQUIRE_TOOLS=1` 将测试跳过视为失败，避免缺失工具时把局部测试误记为完整通过。

## 配置与资料

- [元数据服务配置](labeling-backend/config/default_config.yaml)
- [GB 45438 元数据标注开发指南](docs/gb45438-metadata-labeling-development-guide.md)
- 载体验证：[MP4](labeling-backend/docs/mp4-carrier-validation.md)、[M4A](labeling-backend/docs/m4a-carrier-validation.md)、[MP3](labeling-backend/docs/mp3-carrier-validation.md)
- [前端静态素材来源](frontend/public/assets/README.md)

仓库只包含源码、必要文档和界面资源。上传文件、任务数据库、测试产物、日志、本机路径配置和虚拟环境均保留在本地。

## 许可证

项目代码采用 [MIT License](LICENSE)。第三方字体、图片及依赖遵循各自许可，来源见上述素材说明。
