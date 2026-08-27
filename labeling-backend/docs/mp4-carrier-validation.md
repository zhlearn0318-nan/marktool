# MP4 载体兼容性技术验证记录

> 开发手册 §17.3：正式确定 MP4 字段位置前，须完成兼容性验证，记录
> **写入位置、读取命令、媒体完整性、播放兼容**。本记录对应后端 `app/adapters/video.py`
> 与 `app/core/reader.py`。验证日期：2026-08（首期基线）。

## 结论

| 项 | 结果 |
|---|---|
| 载体字段位置 | `XMP-aigc:AIGC`（ExifTool 自定义命名空间 `aigc`，`config/exiftool_aigc.config`，URI `http://example.com/aigc#`），落入 MP4 `moov` 区 |
| 是否转码 | 否（只写元数据，媒体轨道原样保留） |
| 媒体完整性 | 写入前后 ffprobe 签名一致（容器格式、时长、轨道、编解码器、分辨率） |
| 播放兼容 | 写入后可被 ffprobe 探测，且 ffmpeg 解码前 2 秒无错误 |
| 读取方式 | ExifTool 全标签 JSON：`-json -a -G -s` |
| 风险（重要） | 旧载体 AIGC（如 `QuickTime:Comment`）与新载体共存时会出现 **2 份 AIGC**，违反"仅一份" |

## 写入命令

```bash
exiftool -config config/exiftool_aigc.config \
  -overwrite_original \
  '-XMP-aigc:AIGC={"AIGC":{"Label":"1","ContentProducer":"ORG_1565201000000016", ...}}' \
  in.mp4
```

## 读取命令（全标签扫描，防漏检旧载体）

```bash
exiftool -config config/exiftool_aigc.config -json -a -G -s in.mp4
```

`-a` 保留重复标签、`-G` 带组名（`XMP:Aigc` / `QuickTime:Comment`…）、`-s` 用短标签名。
AIGC 识别规则为标签名或值中包含 `AIGC`（子串匹配），与检测器 `aigc_check.py` 同源（§14）。

## 媒体完整性校验

```bash
# 1) 结构对比：写入前后各跑一次，逐项一致
ffprobe -v error -show_entries format=format_name,duration \
  :stream=codec_type,codec_name,width,height,sample_rate,channels -of json <file>

# 2) 播放冒烟：解码第一个视频流前 2 秒（限定时间控制大文件成本）
ffmpeg -v error -t 2 -i <file> -map 0:v:0 -f null -
```

实测样例（2 秒 h264/aac 测试片，写入前后）：

| 属性 | 写入前 | 写入后 |
|---|---|---|
| format_name | mov,mp4,m4a,3gp,3g2,mj2 | 同 |
| 时长 | 2.000000s | 2.000000s |
| 视频轨道 | h264 320×240 | 同 |
| 音频轨道 | aac 48000Hz | 同 |

## 已确认的共存风险（写入模块必须处理）

对含旧载体标识的文件（`QuickTime:Comment` 里带 AIGC 文本）直接写入新载体后，
ExifTool 读回出现 **2 处 AIGC**（`QuickTime:Comment` + `XMP:Aigc`）——违反附录 E"仅一份"。

处理方式（§9.3）：写入前必须**全标签扫描**；
- `reject`：一旦发现已有标识即 409 `AIGC_METADATA_EXISTS`；
- `replace`：先移除所有可识别旧标签并回读确认归零，再写入一份新标识。

## 验收对照

- [x] 干净 MP4 写入成功，回读恰好 1 份、字段全对
- [x] 媒体零改动（上述签名一致）
- [x] 可播放（ffmpeg 解码无错）
- [x] 旧载体被识别、reject 拒绝、replace 整体替换
- [x] 检测器 §14 校准到 7 字段全必填（见 `../AIGC项目/exif实践/aigc_check.py`）
