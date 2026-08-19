# JPEG/PNG 文件元数据隐式标识适配器

## 1. 当前范围

本模块只负责后端 JPEG/JPG、PNG 文件元数据隐式标识，不包含前端页面、MP4、显式标识、内容水印、C2PA 或数字签名。

模块完成以下闭环：

1. 按文件内容识别真实图片格式；
2. 校验 `{"AIGC": {...}}` 外层对象和七个规定字段；
3. 检查文件中已有 AIGC 记录；
4. 按 `reject` 或 `replace` 策略处理；
5. 复制原文件，在副本的 XMP 中写入一份 AIGC JSON；
6. 使用公共 XMP 读取器和 ExifTool 两条路径回读；
7. 检查仅一份记录、逐字段一致、图片可解码、尺寸与像素不变；
8. 全部通过后才发布结果文件，原文件不被覆盖。

## 2. 模块位置

```text
backend/app/metadata/
├── exiftool_aigc.config   # AIGC XMP 自定义标签定义
├── exiftool_client.py     # ExifTool 安全调用封装
├── identifier_registry.py # ProduceID/PropagateID SQLite 唯一性登记
├── image_adapter.py       # JPEG/PNG 适配器与写入服务
└── xmp_reader.py          # 公共 XMP 读取、计数与解析

backend/app/schemas/
├── gb45438_appendix_e.json
└── validation.py
```

`JpegXmpAdapter` 和 `PngXmpAdapter` 是格式适配层；`ImageMetadataService` 负责公共处理流程；`ExifToolClient` 只负责调用外部工具。以后增加其他格式时，不需要把格式细节写进公共业务流程。

## 3. 文件内写入形式

载体使用 XMP 自定义属性：

```text
XMP-aigc:AIGC
```

属性值是完整、紧凑的 JSON 字符串：

```json
{"AIGC":{"Label":"1","ContentProducer":"ORG_001","ProduceID":"P_001","ReservedCode1":"","ContentPropagator":"ORG_001","PropagateID":"P_001","ReservedCode2":""}}
```

属性名称包含 `AIGC`，值中只保存标准对象，不混入任务编号、文件哈希或处理时间。项目 XMP 命名空间集中定义在 `exiftool_aigc.config` 中，读写两端共用。

## 4. 配置 ExifTool

开发机先设置 ExifTool 路径。PowerShell 示例：

```powershell
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
```

代码也允许在创建客户端时显式传入路径。生产代码没有写死个人电脑盘符，便于其他队员和部署环境使用。

Windows 命令行会按系统代码页重新编码参数，因此封装层把参数和文件名写入 UTF-8 参数文件，再交给 ExifTool。这一步用于保证中文路径等 Unicode 文件名可以正确处理；AIGC 字段值是否允许写入仍由后面的国标字符规则单独判断。

还必须设置 SQLite 编号登记库。数据库保存“角色、提供者、编号、内容指纹”的对应关系，不写入图片文件：

```powershell
$env:AIGC_ID_REGISTRY_PATH = (Resolve-Path '.').Path + '\data\aigc_identifiers.sqlite3'
```

同一提供者的同一编号可以用于同一图片内容的副本，但不能指向不同图片内容。写入失败时登记事务会回滚。

## 5. Python 调用示例

```python
from app.metadata.image_adapter import ImageMetadataService

document = {
    "AIGC": {
        "Label": "1",
        "ContentProducer": "ORG_001",
        "ProduceID": "P_001",
        "ReservedCode1": "",
        "ContentPropagator": "ORG_001",
        "PropagateID": "P_001",
        "ReservedCode2": "",
    }
}

service = ImageMetadataService()
result = service.write(
    source_path="input.jpg",
    output_path="input_labeled.jpg",
    document=document,
    policy="reject",
)
```

已有标识时，`reject` 返回 `AIGC_METADATA_EXISTS`；只有上层业务已经获得用户明确确认时，才传入 `policy="replace"`。替换采用“删除旧记录后整体写入一份新记录”，不做字段合并。

默认按“生成服务提供者首次写入”处理，因此强制：

```text
ContentPropagator = ContentProducer
PropagateID = ProduceID
```

后续传播环节可以明确传入 `initial_write=False`，允许传播者字段更新。

首期写入采用开发手册的严格字符子集：允许 `!`、`#`～`[`、`]`～`~`，空字符串仅用于预留字段；不允许空格、双引号、反斜杠、换行和中文。该策略比“应主要由这些字符组成”更严格，用于保证本项目写出的值稳定落在国标推荐范围内。

## 6. 主要错误码

| 错误码 | 含义 |
|---|---|
| `UNSUPPORTED_MEDIA_TYPE` | 文件内容不是可解码的 JPEG/PNG |
| `AIGC_SCHEMA_INVALID` | 外层对象、七字段、类型或 `Label` 枚举错误 |
| `AIGC_CHARACTER_INVALID` | 字段值超出首期严格字符范围 |
| `AIGC_INITIAL_RELATION_INVALID` | 首次写入时生产字段与传播字段不一致 |
| `AIGC_IDENTIFIER_DUPLICATE` | 同一提供者的编号已指向其他内容 |
| `AIGC_METADATA_EXISTS` | 已有标识且策略为 `reject` |
| `METADATA_WRITE_FAILED` | 旧记录无法安全删除或 ExifTool 写入失败 |
| `METADATA_READBACK_FAILED` | 写入后读不到、解析失败或内容不一致 |
| `AIGC_DUPLICATE_RECORDS` | 写入后仍存在多份 AIGC 记录 |
| `MEDIA_INTEGRITY_FAILED` | 图片格式、尺寸、模式或像素内容发生变化 |
| `OUTPUT_FILE_EXISTS` | 指定结果文件已存在，拒绝覆盖 |

## 7. 测试

在 `backend` 目录运行：

```powershell
$env:EXIFTOOL_PATH = 'D:\exiftool\exiftool.exe'
$env:AIGC_ID_REGISTRY_PATH = (Resolve-Path '.').Path + '\data\aigc_identifiers.sqlite3'
.\.venv\Scripts\python.exe -m pytest -q
```

JPEG 和 PNG 均覆盖：无标识写入、已有标识默认拒绝、明确替换、重复记录清理、扩展名伪装、损坏文件、独立回读、唯一性和媒体完整性。还覆盖严格七字段 Schema、严格字符规则、首次写入关系、编号跨内容冲突、事务回滚、中文路径、原文件不覆盖和无关 XMP 元数据保留。

如果测试环境没有安装 ExifTool，适配器集成测试会跳过；Schema、读取器和检测器单元测试仍可运行。持续集成环境应安装 ExifTool 并设置 `EXIFTOOL_PATH`，以免把集成测试跳过当作通过。
