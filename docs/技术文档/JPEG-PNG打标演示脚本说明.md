# JPEG/PNG 文件元数据完整流程演示脚本

## 1. 演示范围

脚本位置：`backend/scripts/demo_image_metadata_label.py`。

脚本通过后端接口连续演示当前 JPEG/PNG 文件元数据模块的主要能力：

1. 检查后端、ExifTool 与图片格式能力；
2. 可选读取一张已有标识的对照图片，提取七字段并确认 `compliant`；
3. 检测未打标原图，得到 `not_found`、标识份数为 0；
4. 后端生成 `ProduceID`，提交异步首次打标任务；
5. 校验回读、七字段 Schema、标识唯一性和图片完整性；
6. 下载打标结果，保留原图；
7. 独立提取七字段，确认结论为 `compliant`；
8. 再次以 `reject` 写入，演示 `AIGC_METADATA_EXISTS` 重复写入保护；
9. 模拟用户明确选择 `replace`，生成新 `ProduceID` 并整体替换；
10. 复检替换结果，确认旧记录没有残留且仍只有一份标识；
11. 在结果副本上故意删除 `ReservedCode1/2`，构造不合规测试样本；
12. 检出 `noncompliant` 和 `AIGC_SCHEMA_INVALID`；
13. 生成修复计划，记录人工确认并执行异步修复；
14. 下载修复结果并复检，确认七字段完整、唯一且为 `compliant`；
15. 再构造一个损坏 JSON 的独立副本，检出 `AIGC_JSON_INVALID`；
16. 确认该数据只能进入 `manual_review`，系统不会猜测字段或自动修复。

第 11—19 步仅用于展示不合规检测、安全修复和自动修复边界。脚本只修改自动生成的测试副本，不会修改原图、正常打标结果或替换结果。

## 2. 演示前准备

主输入文件必须是尚未写入 AIGC 标识的 `.jpg`、`.jpeg` 或 `.png` 图片。还可以通过 `--existing-image` 提供一张已有且合规标识的对照图片。建议使用已知来源的 AIGC 测试图片。系统只负责标识写入与检测，不负责判断图片是否由人工智能生成。

在第一个 PowerShell 窗口启动后端：

```powershell
cd '<marktool 仓库路径>\backend'
$env:EXIFTOOL_PATH = '<ExifTool 可执行文件路径>'
.\.venv\Scripts\python.exe -m uvicorn app.main:app
```

## 3. 运行完整演示

在第二个 PowerShell 窗口执行：

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = '1'
cd '<marktool 仓库路径>\backend'
$env:EXIFTOOL_PATH = '<ExifTool 可执行文件路径>'

.\.venv\Scripts\python.exe `
    scripts\demo_image_metadata_label.py `
    '<未打标图片路径>' `
    --existing-image '<已合规打标图片路径>' `
    --producer 'ORG_DEMO_001' `
    --operator-label '演示操作人' `
    --output-dir '<演示结果目录>'
```

如果使用 PNG，只需要把输入路径改成实际的 `.png` 文件路径。

## 4. 输出文件

输出目录中会保留以下几类文件：

- 首次打标结果；
- 明确替换后的结果；
- 故意缺少两个保留字段的不合规演示副本；
- 自动修复后的最终结果。

如遇同名文件，脚本会追加任务编号片段，不会覆盖旧文件。

## 5. 成功标准

终端应依次显示 `[1/19]` 至 `[19/19]`，最后包含：

```text
- 未标识检测：not_found / 0 份
- 首次打标：成功
- 标识提取：成功，七字段完整
- 国标文件元数据结构检测：compliant
- 重复写入保护：已拦截
- 明确整体替换：成功，仍仅 1 份标识
- 不合规标识检测：noncompliant
- 安全修复及复检：成功，compliant
- 损坏 JSON 安全边界：manual_review，禁止自动修复
- 图片像素完整性：通过
- 原始文件未被覆盖：是
- ExifTool 交叉读取：matched
- 编号来源核验：verified（以实际登记状态为准）
- C2PA 存在性检查：absent（当前只检查存在性，不代表签名验证）
=== 完整演示完成 ===
```

本脚本验证的是 GB 45438—2025 中当前项目已实现的 JPEG/PNG“文件元数据隐式标识”结构及项目安全策略，不代表已经覆盖显式标识、数字水印、其他文件格式或完整 C2PA 签名验证。

## 6. 常见错误

- 输入检测不是 `not_found`：图片已经有标识或处于无法可靠判断的状态，应换用未打标原图。
- 无法连接后端：先启动 Uvicorn，确认地址为 `http://127.0.0.1:8000`。
- JPEG/PNG 能力不可用：检查 `EXIFTOOL_PATH` 和 `/api/v1/health`。
- 异步任务超时：查看后端窗口错误，必要时增加 `--poll-timeout 120`。
- 修复计划不可执行：确认演示样本确实只缺少两个保留字段；来源不明或 JSON 损坏的数据不会自动修复。
