# JPEG/PNG 已有标识合规检测与修复接口说明

日期：2026-08-29

适用范围：后端 JPEG/JPG、PNG 文件元数据隐式标识。本文不包含前端、显式标识、像素水印以及 C2PA 签名验证。

## 1. 核心原则

1. 检测是只读操作，不修改上传文件。
2. `not_found` 只表示没有发现标识，不属于“已有标识修复”；如需新增，应调用普通标注接口。
3. 修复分成“生成计划”和“确认执行”两步。计划与输入文件 SHA-256、检测结果和拟修复结果绑定。
4. 系统不能凭空补造机构名称、ProduceID、PropagateID 或 Label。
5. 仅以下确定性转换可以在计划中提出，并且仍需用户确认：
   - 给旧版内层七字段补上外层 `AIGC` 对象；
   - 将整数 `1`、`2`、`3` 转成等值字符串；
   - 将缺失的 `ReservedCode1`、`ReservedCode2` 补成空字符串；
   - 对内容完全相同的重复记录去重。
6. 多份记录内容冲突、JSON 损坏且没有可信来源、编号登记冲突、读取器结果不一致、Extended XMP 无法安全解析时，不自动修复。
7. 发现 C2PA 承载时，当前版本只报告“存在但未验证”，并阻止自动改写；不把它解释为国标不合规。
8. 修复输出为新文件，原文件不被原地覆盖。

## 2. 检测结论

| 结论 | 含义 | 是否进入自动修复计划 |
|---|---|---|
| `compliant` | 检出唯一一份记录，JSON 和七字段结构通过国标检查 | 否，无需修复 |
| `noncompliant` | 已发现标识，但份数、JSON 或七字段结构不符合要求 | 视问题类型决定 |
| `not_found` | 未发现标识 | 否，转普通标注流程 |
| `indeterminate` | 当前读取能力不足以可靠下结论 | 否，人工复核 |

项目的字段长度、严格字符子集等安全与兼容性规则通过 `project_policy` 单独报告，不与国标结构结论混为一谈。

## 3. 接口调用顺序

### 3.1 创建修复计划

`POST /api/v1/metadata-repair-plans`

请求类型为 `multipart/form-data`：

- `file`：必填，JPEG/JPG 或 PNG；
- `request`：可选，UTF-8 JSON。只有在七字段来自登记库、审计历史、已验证提供方记录或授权人工输入时才填写。

不提供可信外部信息时，可以只上传文件。接口返回：检测结论、候选记录证据、来源核验状态、C2PA 存在性、拟执行动作、字段变化、阻断原因、`plan_id` 和 `plan_hash`。

可信来源示例：

```json
{
  "trusted_input": {
    "AIGC": {
      "Label": "1",
      "ContentProducer": "ORG_TEST_001",
      "ProduceID": "PRODUCE-ID-001",
      "ReservedCode1": "",
      "ContentPropagator": "ORG_TEST_001",
      "PropagateID": "PRODUCE-ID-001",
      "ReservedCode2": ""
    },
    "source_type": "authorized_manual",
    "source_reference": "审批单或其他可核验记录编号",
    "write_context": "unknown"
  }
}
```

`write_context` 可选值为 `unknown`、`initial_generation`、`propagation`。只有明确知道是首次生成写入时，才选择 `initial_generation` 并检查生产者/传播者及两个编号的首次写入关系；来源不明时使用 `unknown`，不能擅自推断。

### 3.2 查询修复计划

`GET /api/v1/metadata-repair-plans/{plan_id}`

计划默认保留 24 小时。只有 `repair_plan.executable=true` 的计划能够创建任务。计划确认后，响应中的 `job_id` 和 `links.job` 可用于找回关联任务，避免操作人漏存异步任务编号。

### 3.3 确认并创建异步修复任务

`POST /api/v1/metadata-repair-jobs`

```json
{
  "plan_id": "plan_xxx",
  "plan_hash": "64位小写SHA-256",
  "confirmed": true,
  "operator_label": "当前操作人的可识别名称"
}
```

`confirmed` 必须明确为 `true`。当前尚未接入账号系统，因此审计记录固定说明 `identity_verified=false`；`operator_label` 是操作标签，不等同于已完成身份认证。

### 3.4 查询任务

`GET /api/v1/metadata-repair-jobs/{job_id}`

任务状态为 `queued`、`running`、`succeeded` 或 `failed`。成功响应包含回读校验、唯一性校验、结构校验、像素一致性和项目策略结果。

### 3.5 下载结果

`GET /api/v1/metadata-repair-jobs/{job_id}/output`

只有成功且文件尚在保留期内时可以下载。修复原文件和输出文件保留 180 天；过期后返回 `410 OUTPUT_EXPIRED`。文件哈希、原始标识、拟修复结果、校验信息和哈希链审计记录长期保存。

## 4. 前端接入时必须展示的内容

1. 国标结论与项目策略结论分开展示；
2. 原始文件 SHA-256、计划哈希和计划过期时间；
3. 每个字段修改前后的值及修改原因；
4. 所有阻断原因和警告；
5. `ReservedCode1/2` 补空、重复项删除等具体动作；
6. 明确的“确认修复”操作，不得默认勾选；
7. 当前操作标签未完成身份认证的提示；
8. 修复产生新文件、不会覆盖原文件的提示。

## 5. 当前边界

- C2PA：JPEG 检查 APP11/JUMBF 的 C2PA Manifest Store 特征，PNG 检查 `caBX` 数据块；仅用于防止误改，不验证签名、证书链或清单有效性。承载位置依据 [C2PA Technical Specification 2.4](https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html)。
- Extended XMP：当前检测后停止自动处理，不进行分片组装。
- 基础设施：单机线程池、SQLite、本地隔离文件目录，修复 Worker 固定为 1。
- 安全：尚未加入账号鉴权、下载鉴权、限流、生产域名 CORS 和集中式监控，因此当前接口适合受控开发与联调环境。
- 合规性质：自动化测试能证明当前实现符合已编码规则，但不能替代第三方标准认证或正式合规审计。
