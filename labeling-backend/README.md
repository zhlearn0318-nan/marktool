# MarkTool 元数据服务

为 JPEG、PNG、MP4、M4A、MP3、Markdown、PDF、HTML 和 DOCX 提供统一的 AIGC 元数据标注接口。图片、视频与文档还支持只读合规检测及按计划确认的修复；音频当前仅支持标注、回读和完整性检查。

安装、外部工具与前后端启动方式见 [仓库主页](../README.md)。本服务在项目联调中使用 **8002** 端口，交互 API 文档为 http://127.0.0.1:8002/docs 。

## 请求与任务流程

`POST /api/v1/metadata-label-jobs` 接收 multipart 的 `file` 和 `request` 两个字段，其中 `request` 是 JSON 字符串，例如：

```json
{
  "standard": "GB45438-2025",
  "modality": "video",
  "existing_metadata_policy": "reject",
  "AIGC": {
    "Label": "1",
    "ContentProducer": "ORG_1565201000000016",
    "ProduceID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode1": "",
    "ContentPropagator": "ORG_1565201000000016",
    "PropagateID": "0198F21A-6F28-7000-A102-123456789ABC",
    "ReservedCode2": ""
  }
}
```

`modality` 按文件选择 `image`、`video`、`audio` 或 `text`。七字段均需提供，具体类型、枚举和长度限制由共享 Schema 校验。示例编号仅用于说明，实际任务应使用符合项目编号规则的内容编号。

任务创建后返回 202，通过 `GET /api/v1/metadata-label-jobs/{job_id}` 查询进度；仅成功任务可通过对应的 `/output` 路径下载结果。流水线包括已有标识处理、在副本上写入、回读比对、内容完整性检查、编号登记和结果发布。

`existing_metadata_policy` 可选 `reject` 或 `replace`：前者拒绝已有标识，后者先移除已有标识再写入。对受保护或无法安全修改的载体，服务会拒绝处理。

## 格式与载体

| 格式 | 标识载体 |
|---|---|
| JPEG / PNG | 图片 XMP 的 AIGC 属性 |
| MP4 | XMP-aigc:AIGC |
| M4A | Adobe-XMP uuid box 中的 XMP-aigc:AIGC |
| MP3 | ID3v2 的 TXXX:AIGC |
| Markdown | YAML frontmatter 顶层 AIGC 字段 |
| PDF | XMP 元数据 |
| HTML | head 中的 meta[name=AIGC] |
| DOCX | OOXML Custom XML Part |

MP3 读写使用 Mutagen，并保留无关的 ID3 帧。视频和音频通过 ffprobe 比较关键媒体属性，再用 ffmpeg 解码短片段；该检查不等于对整个文件完成解码。图片、文档采用适合其载体的内容指纹与结构校验。

音频不进入合规检测与修复流程；例如 `/api/v1/compliance-inspect` 对 MP3 返回 415。

## 检测与修复

- `POST /api/v1/compliance-inspect`：上传文件，只读返回结论、问题、候选标识与载体信息。结论分为合规、不合规、未检出、无法判定；来源登记与结构合规是不同的证据。
- `POST /api/v1/metadata-repair-plans`：分析文件并生成可审阅的修复计划。
- `GET /api/v1/metadata-repair-plans/{plan_id}`：读取计划。
- `POST /api/v1/metadata-repair-jobs`：提交明确的计划确认后执行。
- `GET /api/v1/metadata-repair-jobs/{job_id}`：查询任务和修复后的复检结果。
- `GET /api/v1/metadata-repair-jobs/{job_id}/output`：下载成功的修复结果。

修复保留原文件。是否可修复由载体检查、计划和保护规则决定，并非所有损坏文件都可恢复。完整请求字段以交互 API 文档为准。

## 目录与配置

- `app/adapters/`：视频、音频和文档适配器。多模态和场景复用这里，不另写一套 M4A/MP3/Markdown 写入。
- `app/metadata/`：图片适配器、各文档载体、合规判断、编号登记与修复。
- `app/core/`：七字段模型、全标签读取、检测器、任务流水线、存储与工作线程。
- `app/modalities/`：按角色把成分文件分发到上述适配器；字幕和纯文本仍用尾段。
- `app/multimodal/`、`app/scenes/`：拆分重组，以及虚拟场景、交互场景。
- `app/api/`：标注、检测、修复、多模态、场景和健康检查接口。
- `app/cli.py`：`python -m app.cli multimodal` 与 `python -m app.cli scene`。
- `config/default_config.yaml`：外部工具、存储、保留期、并发限制和能力配置。
- `config/exiftool_aigc.config`：ExifTool 自定义 XMP 命名空间。
- `tests/`：单元测试、接口测试和真实文件回归测试。

从本目录启动时，默认运行数据保存在 `storage/`，已被 Git 忽略。不要将上传文件、数据库或本机工具复制到源码目录提交。

## 测试与范围

从本目录运行 `python -m pytest tests -q`；完整验收需准备外部工具并设置 `AIGC_REQUIRE_TOOLS=1`，具体环境命令见仓库主页。

多模态拆分、虚拟场景和交互场景已接入，说明见仓库根目录 `README.md` 与 `docs/技术交接文档.md`。单文件的 `metadata-label-jobs`、`compliance-inspect` 和 `metadata-repair-*` 行为保持不变。仍未实现 C2PA 互转、数字签名或信号层鲁棒水印。

## 参考

- [共享开发手册](../docs/gb45438-metadata-labeling-development-guide.md)
- [视频模块设计要点](docs/guide-keypoints.md)
- [MP4 载体验证](docs/mp4-carrier-validation.md)
- [M4A 载体验证](docs/m4a-carrier-validation.md)
- [MP3 载体验证](docs/mp3-carrier-validation.md)
