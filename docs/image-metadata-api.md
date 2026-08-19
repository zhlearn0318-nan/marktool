# JPEG/PNG 元数据标注异步 API 与前后端联调说明

## 1. 当前交付边界

本模块在现有仓库中增加 JPEG/JPG、PNG 文件元数据隐式标识的异步任务 API。它复用已有检测器、七字段 Schema、XMP 读取器和图片适配器，不修改现有 `/api/detect` 检测流程，也不包含前端页面、MP4、显式标识、内容水印或 C2PA。

当前能力可以形成以下后端闭环：

```text
前端申请 ProduceID
  → 上传图片和完整七字段
  → 后端持久化任务并返回 202
  → 后台写入、回读和校验
  → 前端轮询任务状态
  → 成功后下载独立结果文件
```

## 2. 与现有仓库和开发手册的对齐

### 2.1 直接复用的现有内容

| 现有内容 | 本模块的处理方式 |
|---|---|
| FastAPI 应用 `app.main` | 只新增 `/api/v1` 路由，不重写应用 |
| `/api/detect`、`/api/report/{id}` | 完整保留，避免影响现有前端 |
| `gb45438_appendix_e.json` | 作为文件中 AIGC 七字段的共享 Schema |
| `validation.py` | 复用七字段、严格字符和首次写入关系校验 |
| `xmp_reader.py` | 复用已有标识检查和写后回读 |
| `ImageMetadataService` | 作为异步任务真正调用的 JPEG/PNG 写入器 |
| `SQLiteIdentifierRegistry` | 继续检查提供者范围内编号与图片内容的对应关系 |

### 2.2 直接采用的开发手册约定

- API 前缀为 `/api/v1`；国标七字段保持规定的大小写，其余字段使用 `snake_case`。
- 创建任务使用 `multipart/form-data`，包含 `file` 和 `request`。
- 最终创建请求必须包含确定的 `ProduceID` 和完整七字段。
- 已有标识默认使用 `reject`；只有用户明确确认后才使用 `replace`。
- 创建任务返回 `202 Accepted`，前端通过 `job_id` 轮询。
- 状态使用 `queued`、`running`、`succeeded`、`failed`。
- 只有 `succeeded` 状态可以下载结果。
- 结果文件独立保存，不覆盖原文件。
- 成功响应返回实际回读元数据及四项强制校验结果。
- 任务响应不暴露服务器绝对路径。

## 3. 本项目新增或具体规定的内容

以下内容不是 GB 45438—2025 规定的固定技术实现，而是开发手册未锁定、当前项目为方便单机开发和多人协作所作的选择。以后可以替换实现，但不应随意改变公共 API。

1. **ProduceID 编号接口**：新增 `POST /api/v1/metadata-label-identifiers`。后端生成大写 UUID；前端将返回的 `produce_id` 同时填入最终请求的 `ProduceID` 和首次写入时的 `PropagateID`。
2. **任务数据库**：当前使用 SQLite；默认位置为 `backend/data/metadata_label_jobs.sqlite3`。
3. **编号登记库**：未显式配置时默认使用 `backend/data/aigc_identifiers.sqlite3`。
4. **文件存储**：原文件和结果文件使用随机物理名称，默认放在 `backend/data/metadata_label_files/` 的隔离子目录；数据库只保存引用。
5. **异步执行器**：当前使用单进程线程池，默认同时运行 2 个图片任务；进程重启后，数据库中的 `running` 任务会重新回到队列。
6. **文件大小上限**：JPEG/PNG 默认 25 MiB，可通过环境变量修改。
7. **保留时间**：成功或失败任务默认保留 168 小时，即 7 天；查询时会清理已过期记录和对应文件。
8. **工具超时**：单次 ExifTool 调用默认最多 30 秒。
9. **适配器版本**：审计记录使用 `jpeg-png-xmp-exiftool-v1`，载体使用 `xmp-aigc-v1`。
10. **幂等重试**：支持可选 `Idempotency-Key`。相同键、相同文件和参数返回同一个任务；相同键用于不同请求时返回冲突。
11. **浏览器兼容**：严格支持 `request` 为 `application/json` 文件部分；同时兼容浏览器 `FormData` 直接追加 JSON 字符串的常见写法。
12. **当前能力声明**：`/api/v1/health` 对 JPEG/PNG 按 ExifTool 是否可用返回能力状态；MP4 当前固定为 `false`，由后续视频模块接入。

新增的接口层错误码：

| 错误码 | 使用场景 |
|---|---|
| `AIGC_INITIAL_RELATION_INVALID` | 首次写入时传播字段没有与生产字段保持一致 |
| `AIGC_IDENTIFIER_DUPLICATE` | 同一提供者的编号已经对应另一份内容 |
| `IDEMPOTENCY_KEY_REUSED` | 同一幂等键被用于不同文件或参数 |
| `OUTPUT_NOT_READY` | 任务尚未成功时请求下载 |

## 4. 接口清单

### 4.1 生成 ProduceID

```http
POST /api/v1/metadata-label-identifiers
```

成功返回 `201`：

```json
{
  "request_id": "req_...",
  "produce_id": "5B7BE04A-19F1-4BA1-9A5E-00D438E4FB67",
  "generated_at": "2026-08-19T08:00:00Z"
}
```

编号接口只负责生成候选编号。创建任务和真正写入时，后端仍会结合提供者和图片内容执行唯一性检查。

### 4.2 创建异步标注任务

```http
POST /api/v1/metadata-label-jobs
Content-Type: multipart/form-data
Idempotency-Key: <可选>
```

multipart 内容：

- `file`：JPEG/JPG 或 PNG 二进制文件；
- `request`：以下 JSON，推荐把该部分设置为 `application/json`。

```json
{
  "standard": "GB45438-2025",
  "modality": "image",
  "existing_metadata_policy": "reject",
  "AIGC": {
    "Label": "1",
    "ContentProducer": "ORG_1565201000000016",
    "ProduceID": "5B7BE04A-19F1-4BA1-9A5E-00D438E4FB67",
    "ReservedCode1": "",
    "ContentPropagator": "ORG_1565201000000016",
    "PropagateID": "5B7BE04A-19F1-4BA1-9A5E-00D438E4FB67",
    "ReservedCode2": ""
  }
}
```

首次写入时，前端只需要让用户选择 `Label`、填写 `ContentProducer`，其余值按以下规则生成：

```text
ProduceID = 后端编号接口返回值
ContentPropagator = ContentProducer
PropagateID = ProduceID
ReservedCode1 = ""
ReservedCode2 = ""
```

成功接收返回 `202`，其中 `job_id` 用于后续查询。

### 4.3 查询状态

```http
GET /api/v1/metadata-label-jobs/{job_id}
```

前端收到 `202` 后开始查询。建议初期每 1 秒一次，持续一段时间后放宽到每 2 至 3 秒；进入 `succeeded` 或 `failed` 后停止。

成功状态包含：

- 输入文件真实 MIME、大小和 SHA-256；
- 结果文件信息和过期时间；
- 实际从结果文件回读的 `embedded_metadata`；
- 回读成功、Schema 正确、仅一份标识、媒体完整性四项结果；
- 下载地址。

### 4.4 下载结果

```http
GET /api/v1/metadata-label-jobs/{job_id}/output
```

只有任务状态为 `succeeded` 时返回 JPEG/PNG 文件。下载文件名经过安全化处理，服务器真实路径不会出现在响应中。

### 4.5 能力检查

```http
GET /api/v1/health
```

当前图片模块的典型响应：

```json
{
  "status": "ok",
  "capabilities": {
    "image/jpeg": true,
    "image/png": true,
    "video/mp4": false
  }
}
```

## 5. 本地配置

所有个人电脑路径都通过环境变量设置，代码中不写死开发者盘符。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `EXIFTOOL_PATH` | 从系统 PATH 查找 | ExifTool 可执行文件位置 |
| `AIGC_JOB_DB_PATH` | `backend/data/metadata_label_jobs.sqlite3` | 任务 SQLite 数据库 |
| `AIGC_ID_REGISTRY_PATH` | `backend/data/aigc_identifiers.sqlite3` | 编号唯一性登记库 |
| `AIGC_JOB_STORAGE_DIR` | `backend/data/metadata_label_files` | 原文件和结果文件目录 |
| `AIGC_MAX_UPLOAD_BYTES` | `26214400` | 单文件最大字节数 |
| `AIGC_JOB_RETENTION_HOURS` | `168` | 任务和文件保留小时数 |
| `AIGC_JOB_WORKERS` | `2` | 单机并发任务数 |
| `AIGC_EXIFTOOL_TIMEOUT_SECONDS` | `30` | 单次 ExifTool 超时秒数 |

开发机至少需要设置：

```powershell
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
```

## 6. 前端调用要点

前端成员不需要调用 `ImageMetadataService`，只调用上述 HTTP 接口。建议按以下顺序实现：

1. 调用编号接口；
2. 组装并预览完整 `AIGC` 七字段；
3. 通过 multipart 创建任务；
4. 保存返回的 `job_id`；
5. 轮询状态；
6. 遇到 `AIGC_METADATA_EXISTS` 时询问用户，不得自动改成 `replace`；
7. 成功后展示实际回读 JSON、四项校验结果和下载按钮。

后端启动后可访问 `http://localhost:8000/docs` 查看 OpenAPI 页面。接口字段发生变化时，应先更新本说明和 OpenAPI 模型，再通知前端成员。

## 7. 测试入口

自动化测试文件：

- `backend/tests/test_metadata_label_api.py`：前后端契约、异步流程、JPEG/PNG 写入和下载；
- `backend/tests/test_metadata_job_store.py`：任务持久化、重启恢复、审计字段和幂等性；
- `backend/tests/test_image_adapter.py`：底层写入、回读、替换、唯一性和图片完整性。

完整自动化测试命令：

```powershell
cd backend
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
.\.venv\Scripts\python.exe -m pytest -q -rs
```

人工联调时，先启动后端：

```powershell
cd backend
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

然后打开 `http://localhost:8000/docs`，依次测试编号接口、创建任务、查询状态和下载接口。测试完成后，再用现有 `/api/detect` 上传下载得到的结果图片，应识别到一份完整 AIGC 元数据。
