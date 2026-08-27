# 开发手册重点提炼（视频打标模块视角）

> 依据《gb45438-metadata-labeling-development-guide.md》。本文只提炼与"视频元数据打标识"
> 后端模块直接相关、且实现时不能绕过的硬要求。完整契约以手册原文为准。

## 1. 数据模型：文件元数据隐式标识（附录 E 规范性）

标准对象外层固定 `AIGC`，内层 **7 个字段全部必填**（§14 校准要点）：

| 字段 | 说明 | 首次写入规则 |
|---|---|---|
| `Label` | 字符串 `"1"`/`"2"`/`"3"`（1=属于 AI 生成） | 必填 |
| `ContentProducer` | 内容生产者 | 必填 |
| `ProduceID` | 内容制作编号 | 必填，且一个编号只能被一个文件使用一次（§5.6） |
| `ReservedCode1` | 预留 | **必填**，首期写空串 |
| `ContentPropagator` | 内容传播者 | 首次写入 = 生成方 |
| `PropagateID` | 传播编号 | 首次写入 = 制作编号 |
| `ReservedCode2` | 预留 | **必填**，首期写空串 |

- 字符约束（§5.5）：值只能是 ASCII 单字节可打印字符，**禁 `"` `\`、空格、换行**；中文/emoji 会命中检查。
- JSON Schema 共享、前后端同源（§5.7）；`additionalProperties: false`，未知字段直接拒绝。
- 一个文件内**只允许一份**该标识（§5.4）。

## 2. 载体位置（§6.4）

- 视频（MP4）统一写 **`XMP-aigc:AIGC`**（ExifTool 自定义命名空间，`config/exiftool_aigc.config`），
  落在 MP4 的 moov 区，**不转码**。输出必须携带载体标识：`carrier = "mp4-aigc-v1"`。
- 检测已有标识必须**全标签扫描**（`-a -G -s`），旧载体里的 AIGC（如 QuickTime:Comment）也要认——
  否则"仅一份"约束会被旧标签破坏。
- §17.3：正式定稿前须完成载体兼容性验证并**留档**，见 `docs/mp4-carrier-validation.md`。

## 3. API（§7，前后端契约）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/metadata-label-jobs` | `multipart`: `file` + `request`（JSON），`Idempotency-Key` 可选。成功 `202` |
| GET | `/api/v1/metadata-label-jobs/{id}` | 查询状态/阶段/校验结果，前端**轮询** |
| GET | `/api/v1/metadata-label-jobs/{id}/output` | 下载结果文件，仅 `succeeded`，带 `expires_at` |
| GET | `/api/v1/health` | 健康 + 能力列表（图片 `false` 时前端应禁用入口） |

- `request` 里 `standard` 固定 `GB45438-2025`；`modality` 声明模态必须与文件真实类型一致；`existing_metadata_policy` 仅 `reject`/`replace`（首期不支持 merge）。
- 响应与错误都带 `request_id`（body + `X-Request-Id` 头）。
- 交互式文档：服务启动后 `http://127.0.0.1:8000/docs`。

## 4. 状态机（§8）

- `status`（面向用户，有限集合）：`queued → running → succeeded | failed`。
- `stage`（内部细化，前端可忽略）：`validating_request → inspecting_file → checking_existing_metadata → writing_metadata → verifying_metadata → verifying_media → publishing_output → completed/failed`。
- `progress`：可空（空=前端显示不确定进度条）；成功=100。
- 前端轮询规则：未到终态就间隔查询，到了 `succeeded`/`failed` 停止。

## 5. 后端处理流水线（§9，视频打标后端要做的）

1. **预检**（同步，422/409 直接回）：multipart 完整性 → request Schema → 声明模态 vs 文件真实类型（按内容识别）→ 大小上限 → 已有标识检查。
2. **已有标识策略**：`reject` → 409 `AIGC_METADATA_EXISTS`；`replace` → 先整体移除全部旧标签、回读确认归零，再写一份新的（§9.3）。
3. **写入**：在临时副本上写，**绝不修改原文件**（§4.4）。
4. **回读校验**（§9.4）：恰好一份 → 可解析 → 过 Schema → 与提交对象逐字段一致。
5. **媒体完整性**（§9.4）：ffprobe 对比写入前后时长/分辨率/轨道/编解码器 + ffmpeg 解码冒烟（可播放）。
6. **原子发布**（§9.2）：全部校验通过后 `os.replace` 进输出目录。
7. **审计**（§9.5）：原文件哈希、已有标签、回读结果、媒体签名都要落库，作为合规依据。

## 6. 错误码（§11）

- 同步：`400 INVALID_MULTIPART`、`413 FILE_TOO_LARGE`、`415 UNSUPPORTED_MEDIA_TYPE`、
  `422 AIGC_SCHEMA_INVALID / AIGC_CHARACTER_INVALID / MODALITY_MISMATCH`、`409 AIGC_METADATA_EXISTS`、`404 JOB_NOT_FOUND`。
- 异步（任务失败时返回）：`METADATA_WRITE_FAILED / METADATA_READBACK_FAILED / AIGC_DUPLICATE_RECORDS / MEDIA_INTEGRITY_FAILED`，带 `retryable`。
- 错误响应不回传内部路径/堆栈（§13）。

## 7. 存储与安全（§12/§13）

- 原文件、结果文件、任务记录保留期可配置，到期一起清理（§12.3）。
- 物理存储名用服务端随机 ID，不用用户文件名；操作参数数组调用，禁止拼 shell。
- 限制文件大小、并发任务数、单任务超时。

## 8. 验收标准（§15，视频列）

- 干净 MP4 打标成功；已有旧标识 reject 正确拒绝、replace 正确替换；
  结果文件可探测、可播放、未转码；32+ 条契约/矩阵测试通过。
- 写模块上线前，检测器 `aigc_check.py` 必须按 §14 校准到 **7 字段全必填**（已改好）。

## 后端职责一页纸（视频打标）

输入视频 → 校验是否为真 MP4 → 检查有没有旧 AIGC（全标签）→
在副本上写 `XMP-aigc:AIGC`（不转码）→ 回读确认恰好一份且字段全对 →
ffprobe/ffmpeg 确认媒体没坏 → 原子发布 → 落库审计 → 前端轮询拿到 carrier 和校验结果。
