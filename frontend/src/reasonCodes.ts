/** 后端码表的中文释义。
 *
 *  两个检测引擎共用这份表：视频走本项目原有检测器，图片走
 *  app.metadata.compliance —— 它们的 reason code 词表**不同名**（前者
 *  DUPLICATE_RECORDS，后者 AIGC_MULTIPLE_RECORDS），所以是并集而非替换。
 *  界面取不到释义时回落显示原始码，不做「未知错误」这类掩盖信息的兜底。 */

export const REASON_ZH: Record<string, string> = {
  // ---- 视频检测器 ----
  DUPLICATE_RECORDS: "检测到多份 AIGC 标识（国标要求同一文件仅保留一份）",
  MISSING_FIELD: "缺失必填字段，或身份/编号字段为空",
  BAD_JSON: "元数据值无法解析为国标结构（JSON 非法或缺外层 AIGC 对象）",
  BAD_LABEL: "Label 取值非法（必须是字符串 1 / 2 / 3）",
  UNKNOWN_FIELD: "国标对象外存在字段",
  LEGACY_CARRIER: "旧载体 QuickTime:Comment（存在正式迁移规则）",
  CHARSET: "字段含国标规定字符范围外的字符，需人工复核",
  FIRST_WRITE_MISMATCH: "传播方/传播编号与制作者不一致（可能是合法二次传播）",
  // 图片与视频共用这个码，文案保持格式中立（后端会在问题清单里给具体原因）
  UNREADABLE_CARRIER: "元数据载体不可完整读取（结构损坏 / 截断 / 编码异常）",
  REGISTRY_MISMATCH: "编号登记库核对不一致",
  TOOL_DIVERGENCE: "读取工具结果分歧",

  // ---- 图片检测器（app.metadata.compliance）----
  AIGC_COMPLIANT: "标识唯一，且通过国标结构与字段检查",
  AIGC_NOT_FOUND: "未检出 AIGC 隐式标识",
  AIGC_MULTIPLE_RECORDS: "存在多份 AIGC 标识（国标要求同一文件仅保留一份）",
  AIGC_JSON_INVALID: "AIGC 属性值不是合法 JSON",
  AIGC_SCHEMA_INVALID: "AIGC 结构不符合国标字段约定",
  AIGC_INITIAL_RELATION_INVALID: "首次写入时传播方/传播编号与制作者不一致",
  AIGC_IDENTIFIER_CONFLICT: "同一编号已登记到不同内容（编号与像素指纹不匹配）",
  AIGC_READERS_DIVERGED: "两种读取方式结果分歧，元数据不可完全信赖",
  METADATA_READ_FAILED: "元数据读取失败",
  EXTENDED_XMP_UNSUPPORTED: "存在 Adobe 扩展 XMP 分段，首期不支持写入该形态",
  EXTENDED_XMP_CHECK_FAILED: "扩展 XMP 分段检测失败（文件结构不可靠）",
  EXIFTOOL_CROSSCHECK_UNAVAILABLE: "ExifTool 交叉核对不可用（自动修复已锁定）",
  PROJECT_POLICY_REJECTED: "本项目策略拒绝了该标识值",
  C2PA_PRESENT_UNVERIFIED: "检出 C2PA 清单但未验签",
  C2PA_PRESENCE_INDETERMINATE: "C2PA 存在性无法判断",
  UNSUPPORTED_MEDIA_TYPE: "不支持的文件类型",
};

/** 修复可行性。两套词表：检测报告里是 auto_fixable/needs_human（本项目原有），
 *  修复计划的 draft 里是 confirmable/manual_review（图片模块原生），两者都在此收录。 */
export const REPAIR_ZH: Record<string, string> = {
  auto_fixable: "可确定修复（去重 / Label 转字符串 / 补空值保留字段）",
  needs_human: "需人工确认（来源不明 / 冲突 / 身份缺失，不猜测不乱修）",
  confirmable: "可确定修复（去重 / Label 转字符串 / 补空值保留字段）",
  manual_review: "需人工确认（来源不明 / 冲突 / 身份缺失，不猜测不乱修）",
  forbidden: "禁止修复（需猜测来源或伪造编号）",
  not_applicable: "无需修复",
};

export const MEDIA_STATUS_ZH: Record<string, string> = {
  ok: "正常可读",
  degraded: "降级（视频流不可解）",
  unreadable: "不可读",
};

export const C2PA_ZH: Record<string, string> = {
  // 图片检测器用 not_found，视频检测器用 absent；同一含义两个码
  absent: "未发现 C2PA",
  not_found: "未发现 C2PA",
  present_unverified: "存在 C2PA（未验签）",
  indeterminate: "无法判断",
};

export const SOURCE_TYPE_ZH: Record<string, string> = {
  identifier_registry: "编号登记库",
  audit_history: "历史审计记录",
  verified_provider_record: "服务方核验记录",
  authorized_manual: "人工授权录入",
  // 系统自行派生，不由调用方声明
  derived_from_file: "原文件中的可识别记录（系统派生）",
};

export const WRITE_CONTEXT_ZH: Record<string, string> = {
  unknown: "未确认",
  initial_generation: "首次生成写入",
  propagation: "二次传播写入",
};

/** 修复工作台的业务性拒绝码。
 *  这些不是服务器故障，是「按国标不该做」，界面必须让人看懂原因。 */
export const REPAIR_ERR_ZH: Record<string, string> = {
  REPAIR_PLAN_CONFLICT: "计划已失效或与确认信息不一致，请重新生成计划",
  REPAIR_PLAN_NOT_FOUND: "修复计划不存在或已过期",
  TRUSTED_METADATA_INVALID: "可信来源提供的字段不符合国标约定",
  REPAIR_REQUEST_SCHEMA_INVALID: "修复计划参数不符合接口约定",
  INVALID_REPAIR_REQUEST: "请求内容无法解析",
  AIGC_IDENTIFIER_DUPLICATE: "该编号已登记给其它内容，不能复用",
  AIGC_INITIAL_RELATION_INVALID: "首次写入要求传播方/传播编号与制作者一致",
  INVALID_MULTIPART: "请求结构错误：multipart 字段缺失或非法",
  INVALID_MEDIA: "文件不是可识别的 JPEG / PNG",
  UNSUPPORTED_MEDIA_TYPE: "修复工作台只处理 JPEG / PNG",
  REPAIR_INTERNAL_ERROR: "修复执行过程中发生内部错误",
};
