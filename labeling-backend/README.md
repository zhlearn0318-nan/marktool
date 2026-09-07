# MarkTool 元数据标注服务（GB 45438-2025）

依据《GB 45438—2025 网络安全技术 人工智能生成合成内容标识方法》及团队
《gb45438-metadata-labeling-development-guide.md 开发手册》实现的后端标注服务。

> 文档状态：首期前后端联调基线。视频（MP4）适配器已实现；图片适配器由队友开发，见下文"分工"。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

运行测试：

```bash
.venv/bin/python -m pytest tests/ -q
```

## 接口契约（开发手册 §7）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/metadata-label-jobs` | 创建标注任务（`multipart`: `file` + `request`），返回 `202` |
| GET | `/api/v1/metadata-label-jobs/{job_id}` | 查询状态/阶段/结果 |
| GET | `/api/v1/metadata-label-jobs/{job_id}/output` | 下载结果文件（仅 `succeeded`） |
| POST | `/api/v1/compliance-inspect` | 合规检测（`multipart`: `file`，只读）：同步返回 GB45438 合规报告（`conclusion`/`reason_code`/`media_status`/…） |
| GET | `/api/v1/health` | 健康检查 + 能力列表 |

`request` 示例：

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

## 架构（模块化，对应 UniMark 框架图）

```
用户 → watermark_tool（统一入口）
        → unified_engine（= 本服务的 pipeline.py：按真实文件类型分发）
             → 载体适配器（video.py / image.py）
                 → ExifTool（XMP-aigc:AIGC 读写） + ffprobe（媒体完整性）
```

- `app/core/` 共享层：`aigc.py`（七字段模型/校验/序列化）、`aigc_schema.json`（§5.7 共享 Schema）、
  `reader.py`（全标签 AIGC 扫描，与检测器 aigc_check.py 同源）、`store.py`（SQLite 任务+审计）、
  `storage.py`（临时写入 + 原子发布 + 随机命名）、`pipeline.py`（状态机流水线）、`worker.py`（进程内任务）。
- `app/adapters/video.py` **视频负责人交付**：MP4 写入/回读/replace/媒体完整性。

## 视频模块关键决策

- **载体**：`XMP-aigc:AIGC`（ExifTool 自定义命名空间，`config/exiftool_aigc.config`），
  写入 MP4 `moov` 区域，**不转码**。输出 `carrier = "mp4-aigc-v1"`（§6.4）。
- **已有标识检测必须全标签扫描**：旧载体（如 `QuickTime:Comment`）里的 AIGC 也会被识别，
  `reject` 时返回 409，`replace` 时整体移除后再写一份（§9.3）。
- **媒体完整性**：ffprobe 对比写入前后的时长/分辨率/轨道/编解码器（§9.4），
  再以 ffmpeg 解码前 2 秒确认结果文件可播放（§15.5.8），全程不转码。
- **保留策略**：原文件、结果文件、任务记录保留期可配置，过期一并清理（§12.3）。
- **读取与检测器复用**：写入后的回读校验与检测器共用 `reader.py`（§14），避免"自己写入、自己检测器不认"。
- **检测器校准**：`AIGC项目/exif实践/aigc_check.py` 已按 §14 校准为 7 字段全必填（含 ReservedCode1/2），
  并新增未知字段告警。

## 分工边界

- ✅ 已完成：共享核心层、视频（MP4）适配器、任务生命周期、契约测试、MP4 文件测试矩阵（33 个测试）。
  配套文档：`docs/guide-keypoints.md`（手册重点提炼）、`docs/mp4-carrier-validation.md`（§17.3 载体验证记录）。
- ⏳ 待队友：图片（JPEG/PNG）适配器 —— `app/adapters/image.py` 已留扩展点，
  媒体完整性校验（可解码、宽高不变，§6.3）待补充。当前 `/api/v1/health` 返回
  `image/jpeg: false, image/png: false`，前端据此禁用图片入口（§7.5）。
- 首期**不在范围内**（§3.2）：C2PA 互转、AI 概率检测、数字签名、文本/音频模态、批量上传。

## 参考

- 开发手册：`/Users/shu/Desktop/gb45438-metadata-labeling-development-guide.md`
- 团队文档：`/Users/shu/Desktop/AIGC项目/`（元数据相关.docx、多格式AI标识元数据写入_要点整理.docx 等）
- 国标原文：`AIGC项目/20251017_网络安全技术人工智能生成合成内容标识方法(1).pdf`（附录 E/F）
