# AIGC 标识合规平台（MVP）

本仓库面向 GB 45438—2025 文件元数据隐式标识，当前包含图片检测/报告原型，以及 JPEG/PNG 后端元数据写入适配器。

## 当前实现范围

| 能力 | 状态 | 说明 |
|---|---|---|
| JPEG/PNG AIGC 元数据写入 | 已实现 | ExifTool 写入 XMP，不覆盖原文件 |
| 已有标识 `reject` / `replace` | 已实现 | 默认拒绝；明确替换时先删除旧记录再整体写入 |
| 写后回读与唯一性校验 | 已实现 | 公共 XMP 读取器与 ExifTool 双路径验证 |
| 写前交叉检查 | 已实现 | 两种读取结果不一致或含 Extended XMP 时安全拒绝 |
| 七字段严格 Schema | 已实现 | 外层 `AIGC`、七字段、`Label` 枚举、未知字段拒绝 |
| 附录 E 严格字符规则 | 已实现 | 拒绝空格、双引号、反斜杠、换行及严格范围外字符 |
| 首次写入关系 | 已实现 | 强制传播者等于生产者、传播编号等于生产编号 |
| 编号唯一性 | 已实现 | SQLite 登记“提供者 + 编号”与内容指纹的对应关系 |
| 图片完整性校验 | 已实现 | 真实格式识别、解码、尺寸、模式与像素一致性 |
| 图片元数据检测与报告 API | 已实现 | 保留现有 `/api/detect` 和报告接口 |
| JPEG/PNG 标注任务 API | 已实现 | `/api/v1` 异步创建、查询、下载和 ProduceID 生成 |
| MP4、前端标注页面 | 未实现 | 由其他任务负责 |
| 显式标识、内容水印、C2PA | 不在本阶段 | 当前只研究文件元数据隐式标识 |

## 关键目录

```text
backend/
├── app/
│   ├── api/                  # 检测接口与新增异步标注接口
│   ├── engine/detectors/     # 元数据唯一性和七字段检测
│   ├── metadata/             # JPEG/PNG 适配器、ExifTool 封装、XMP 读取器
│   ├── report/               # 合规报告
│   └── schemas/              # 共享 JSON Schema 与校验器
└── tests/                    # 单元测试与 ExifTool 集成测试

docs/
├── gb45438-metadata-labeling-development-guide.md
├── image-metadata-adapter.md
├── image-metadata-api.md
└── image-metadata-work-summary.md
```

## 环境准备

后端依赖：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装 ExifTool 后设置路径，例如：

```powershell
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
```

未配置数据库和任务文件目录时，开发环境默认使用 `backend/data/`；该目录已被 Git 忽略。详细说明见 [JPEG/PNG 文件元数据适配器说明](docs/image-metadata-adapter.md) 和 [异步 API 与前后端联调说明](docs/image-metadata-api.md)。

## 运行现有检测 API

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

API 文档：`http://localhost:8000/docs`。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/detect` | 上传图片并生成检测报告 |
| `GET` | `/api/report/{id}` | 查询检测报告 |
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/v1/metadata-label-identifiers` | 后端生成 ProduceID |
| `POST` | `/api/v1/metadata-label-jobs` | 上传图片并创建异步标注任务 |
| `GET` | `/api/v1/metadata-label-jobs/{job_id}` | 查询任务状态和校验结果 |
| `GET` | `/api/v1/metadata-label-jobs/{job_id}/output` | 下载成功结果文件 |
| `GET` | `/api/v1/health` | 查询 JPEG/PNG/MP4 能力 |

## 测试

```powershell
cd backend
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
.\.venv\Scripts\python.exe -m pytest -q
```

当前共 122 项测试，覆盖现有检测流程、JPEG/PNG 适配器、任务持久化、异步 API、安全边界和中断恢复。本地安装 ExifTool 后全部测试应实际执行并通过；GitHub Actions 固定使用 ExifTool 13.59，并要求真实 JPEG/PNG 集成测试不得跳过。

## 许可证

本项目采用 [MIT License](LICENSE)。
