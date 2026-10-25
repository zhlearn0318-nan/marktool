# M4A 载体验证

M4A 第一阶段使用与 MP4 相同的 `XMP-aigc:AIGC` 自定义命名空间。值为紧凑 JSON，
外层为 `{"AIGC": {...}}`，内层仍严格遵循既有七字段 Schema。

已验证的 AAC-LC 样本中，ExifTool 将 XMP 写入 M4A 文件本体顶层的 Adobe-XMP `uuid`
box（不是 sidecar 文件）。回读应只发现一份 `XMP:AIGC`，并逐字段等于归一化后的请求。

后端用 ffprobe 对比写入前后的容器、时长、音频轨数量、codec、sample rate 和 channels；
随后用 `ffmpeg -map 0:a:0 -t 2 -f null -` 解码第一个音频流。纯音频文件不得使用
`0:v:0`。

目前已验证 AAC-LC 样本。HE-AAC、不同声道配置和真实设备导出的 M4A 仍属于后续兼容性测试。
