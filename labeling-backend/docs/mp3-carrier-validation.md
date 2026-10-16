# MP3 AIGC 载体验证与实现边界

## 已验证载体

MP3 使用文件开头的 ID3v2 标签中的用户自定义文本帧保存 AIGC 标识：

```
ID3v2 -> TXXX -> description: AIGC -> value: 紧凑 {"AIGC": {...}} JSON
```

正式后端的 carrier 为 `mp3-id3v2-aigc-v1`。值由
`app.core.aigc.serialize_aigc()` 生成，沿用既有七字段 Schema 与字段顺序。

验证样本为 FFmpeg 生成的 48 kHz 单声道 MP3。写入后以 ExifTool 回读确认
`ID3:Aigc`，并以 ffprobe 对比容器、codec、采样率、声道数和时长；随后使用
`ffmpeg -map 0:a:0` 解码前两秒。

## 读写策略

ExifTool 可用作 MP3 全标签读取辅助，但当前不能可靠写入或删除 ID3v2 的 TXXX
用户自定义文本帧。正式实现使用 Mutagen 的 `ID3` 与 `TXXX`：

- 只读取、删除或新增 `desc == "AIGC"` 的 TXXX 帧；
- `replace` 不删除 TIT2、TPE1、TALB、APIC 封面或其他描述名的 TXXX；
- 写后重新打开文件，要求恰好一条 `TXXX:AIGC`，且 JSON 可解析并与提交的七字段逐项一致；
- Mutagen 只改写 ID3 标签区，不转码 MPEG 音频；原上传文件始终只读，操作发生在输出副本。

## 当前兼容性边界

已覆盖的是 ID3v2.4、UTF-8 文本和实验生成的 CBR/常见 MP3 样本。仍需后续补测：

- ID3v2.3、UTF-16/Latin-1 的既有标签；
- APIC 封面、多值及重复 TXXX 的边缘样本；
- VBR、超大 ID3 标签及真实设备/第三方工具导出的 MP3；
- 播放器是否保留未知 TXXX 帧的跨生态兼容性。

本模块仅支持 `/metadata-label-jobs` 打标；MP3 合规检测、MP3 之外的音频格式、
AI 识别模型、水印检测和 C2PA 均不在本阶段范围内。
