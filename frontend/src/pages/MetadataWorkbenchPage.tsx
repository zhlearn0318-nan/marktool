import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Input,
  Progress,
  Radio,
  Row,
  Space,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  FolderOpenOutlined,
  InboxOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import {
  ApiError,
  createMetadataLabelJob,
  createRepairJob,
  createRepairPlan,
  detectImage,
  downloadMetadataLabelOutput,
  downloadRepairOutput,
  generateProduceId,
  getMetadataLabelJob,
  getRepairJob,
} from "../api";
import PageBanner from "../components/PageBanner";
import type {
  DetectResponse,
  MetadataConclusion,
  MetadataLabelJob,
  RepairJob,
  RepairPlan,
} from "../types";

const { Text, Paragraph } = Typography;
const AIGC_FIELDS = [
  "Label",
  "ContentProducer",
  "ProduceID",
  "ReservedCode1",
  "ContentPropagator",
  "PropagateID",
  "ReservedCode2",
] as const;

const CONCLUSION_LABEL: Record<MetadataConclusion, string> = {
  not_found: "未发现标识",
  compliant: "结构合规",
  noncompliant: "标识不合规",
  indeterminate: "无法可靠判断",
};

const CONCLUSION_COLOR: Record<MetadataConclusion, string> = {
  not_found: "blue",
  compliant: "green",
  noncompliant: "red",
  indeterminate: "orange",
};

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.code) {
    return `${error.message}（${error.code}）`;
  }
  return error instanceof Error ? error.message : "操作失败";
}

function extractAigc(value: Record<string, unknown> | null | undefined) {
  if (!value) return null;
  const nested = value.AIGC;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    return nested as Record<string, unknown>;
  }
  return value;
}

function responseFilename(response: Response, fallbackName: string) {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
  return match ? decodeURIComponent(match[1].replace(/\"$/, "")) : fallbackName;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

interface LocalWritableFile {
  write(data: Blob): Promise<void>;
  close(): Promise<void>;
}

interface LocalFileHandle {
  createWritable(): Promise<LocalWritableFile>;
}

type SaveFilePicker = (options: {
  suggestedName: string;
  types: Array<{
    description: string;
    accept: Record<string, string[]>;
  }>;
}) => Promise<LocalFileHandle>;

async function saveResponseWithPicker(response: Response, fallbackName: string) {
  const filename = responseFilename(response, fallbackName);
  const blob = await response.blob();
  const picker = (
    window as typeof window & { showSaveFilePicker?: SaveFilePicker }
  ).showSaveFilePicker;

  if (!picker) {
    downloadBlob(blob, filename);
    message.info("当前浏览器不支持选择保存位置，已使用浏览器默认下载方式");
    return;
  }

  const extension = filename.toLowerCase().endsWith(".png") ? ".png" : ".jpg";
  const mimeType = blob.type || (extension === ".png" ? "image/png" : "image/jpeg");
  const handle = await picker({
    suggestedName: filename,
    types: [
      {
        description: extension === ".png" ? "PNG 图片" : "JPEG 图片",
        accept: { [mimeType]: [extension] },
      },
    ],
  });
  const writable = await handle.createWritable();
  await writable.write(blob);
  await writable.close();
  message.success(`文件已保存：${filename}`);
}

async function waitForLabelJob(jobId: string) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const job = await getMetadataLabelJob(jobId);
    if (job.status === "succeeded") return job;
    if (job.status === "failed") {
      throw new Error(job.error?.message || "打标任务执行失败");
    }
    await sleep(attempt < 20 ? 500 : 1000);
  }
  throw new Error("打标任务等待超时，请使用 job_id 查询后端状态");
}

async function waitForRepairJob(jobId: string) {
  for (let attempt = 0; attempt < 180; attempt += 1) {
    const job = await getRepairJob(jobId);
    if (job.status === "succeeded") return job;
    if (job.status === "failed") {
      throw new Error(job.error?.message || "修复任务执行失败");
    }
    await sleep(attempt < 20 ? 500 : 1000);
  }
  throw new Error("修复任务等待超时，请使用 job_id 查询后端状态");
}

function ResultSummary({ result, title }: { result: DetectResponse; title: string }) {
  const compliance = result.detection.metadata_compliance;
  const conclusion = compliance?.conclusion;
  const aigc = extractAigc(result.detection.aigc_metadata);

  return (
    <div className="ui-card" style={{ marginTop: 18 }}>
      <div className="ui-card-head">
        <span className="ico">✓</span>
        <h3>{title}</h3>
      </div>
      <div className="ui-card-body">
        <Space wrap style={{ marginBottom: 14 }}>
          {conclusion ? (
            <Tag color={CONCLUSION_COLOR[conclusion]}>
              {CONCLUSION_LABEL[conclusion]}
            </Tag>
          ) : null}
          <Tag>标识份数：{compliance?.record_count ?? "—"}</Tag>
          <Tag>格式：{compliance?.detected_format ?? "—"}</Tag>
          <Tag>修复性：{compliance?.repairability ?? "—"}</Tag>
        </Space>

        {aigc ? (
          <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
            {AIGC_FIELDS.map((field) => (
              <Descriptions.Item key={field} label={field}>
                {String(aigc[field] ?? "") || "（空字符串）"}
              </Descriptions.Item>
            ))}
          </Descriptions>
        ) : (
          <Alert
            showIcon
            type="info"
            message="文件中未提取到可识别的 AIGC 七字段标识"
            description="not_found 只代表当前文件中没有发现可识别标识，不代表图片一定不是 AI 生成。"
          />
        )}

        {compliance?.issues?.length ? (
          <div style={{ marginTop: 14 }}>
            <Text strong>检测问题</Text>
            <ul style={{ marginBottom: 0 }}>
              {compliance.issues.map((issue) => (
                <li key={`${issue.code}-${issue.detail}`}>
                  <Text code>{issue.code}</Text>：{issue.detail}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function MetadataWorkbenchPage() {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [detection, setDetection] = useState<DetectResponse | null>(null);
  const [outputDetection, setOutputDetection] = useState<DetectResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [label, setLabel] = useState<"1" | "2" | "3">("1");
  const [producer, setProducer] = useState("ORG_DEMO_001");
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [labelJob, setLabelJob] = useState<MetadataLabelJob | null>(null);
  const [repairPlan, setRepairPlan] = useState<RepairPlan | null>(null);
  const [repairJob, setRepairJob] = useState<RepairJob | null>(null);
  const [operatorLabel, setOperatorLabel] = useState("前端用户确认");
  const [repairConfirmed, setRepairConfirmed] = useState(false);
  const outputResultRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const conclusion = detection?.detection.metadata_compliance?.conclusion;
  const mayLabel =
    conclusion === "not_found" ||
    (conclusion === "compliant" && replaceConfirmed);
  const mayPlanRepair = conclusion === "noncompliant";
  const validationPassed = useMemo(() => {
    const validation = labelJob?.validation || repairJob?.validation;
    return validation
      ? Object.values(validation).filter((value) => typeof value === "boolean").every(Boolean)
      : false;
  }, [labelJob, repairJob]);

  function chooseFile(next: File) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(next);
    setPreviewUrl(URL.createObjectURL(next));
    setDetection(null);
    setOutputDetection(null);
    setLabelJob(null);
    setRepairPlan(null);
    setRepairJob(null);
    setReplaceConfirmed(false);
    setRepairConfirmed(false);
  }

  async function inspect() {
    if (!file) {
      message.warning("请先选择 JPEG/JPG 或 PNG 图片");
      return;
    }
    setBusy("detect");
    try {
      const result = await detectImage(file);
      setDetection(result);
      setOutputDetection(null);
      setRepairPlan(null);
      setRepairJob(null);
      message.success("标识提取与合规检测完成");
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function labelImage() {
    if (!file || !detection) {
      message.warning("请先上传并检测图片");
      return;
    }
    if (!producer.trim()) {
      message.warning("请填写内容生产者标识");
      return;
    }
    if (!mayLabel) {
      message.warning("已有标识时必须明确勾选整体替换，不能直接追加");
      return;
    }
    setBusy("label");
    try {
      const identifier = await generateProduceId();
      const accepted = await createMetadataLabelJob(file, {
        label,
        producer: producer.trim(),
        produceId: identifier.produce_id,
        existingPolicy: conclusion === "not_found" ? "reject" : "replace",
      });
      setLabelJob(accepted);
      const completed = await waitForLabelJob(accepted.job_id);
      setLabelJob(completed);

      const response = await downloadMetadataLabelOutput(completed.job_id);
      const blob = await response.clone().blob();
      const outputName = completed.output?.file_name || `${file.name}_labeled`;
      const outputFile = new File([blob], outputName, {
        type: completed.output?.mime_type || file.type,
      });
      setOutputDetection(await detectImage(outputFile));
      window.setTimeout(
        () => outputResultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
        0
      );
      message.success("打标完成，结果已自动回读并复检");
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function planRepair() {
    if (!file || !mayPlanRepair) {
      message.warning("只有检测为 noncompliant 的已有标识才进入修复计划");
      return;
    }
    setBusy("plan");
    try {
      const plan = await createRepairPlan(file);
      setRepairPlan(plan);
      setRepairJob(null);
      setRepairConfirmed(false);
      message.success("修复计划已生成，请检查动作和阻断原因");
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function executeRepair() {
    if (!repairPlan?.repair_plan.executable) {
      message.warning("当前计划不可执行，需要人工复核或补充可信来源");
      return;
    }
    if (!repairConfirmed || !operatorLabel.trim()) {
      message.warning("请填写操作标签并明确勾选确认修复");
      return;
    }
    setBusy("repair");
    try {
      const accepted = await createRepairJob(repairPlan, operatorLabel.trim());
      setRepairJob(accepted);
      const completed = await waitForRepairJob(accepted.job_id);
      setRepairJob(completed);

      const response = await downloadRepairOutput(completed.job_id);
      const blob = await response.clone().blob();
      const outputName =
        typeof completed.output?.file_name === "string"
          ? completed.output.file_name
          : `${file?.name || "image"}_repaired`;
      const outputFile = new File([blob], outputName, { type: file?.type });
      setOutputDetection(await detectImage(outputFile));
      window.setTimeout(
        () => outputResultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
        0
      );
      message.success("修复完成，结果已自动回读并复检");
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageBanner
        eyebrow="JPEG / PNG METADATA WORKBENCH"
        title="图片元数据标识联调台"
        sub="在一个页面完成标识提取、合规检测、首次打标、明确替换、修复计划、人工确认、异步任务查询和结果下载。"
      />

      <section className="section">
        <Alert
          showIcon
          type="warning"
          message="当前范围仅为 JPEG/JPG、PNG 文件元数据隐式标识"
          description="页面不会覆盖原图；not_found 不代表内容不是 AI 生成；compliant 仅表示当前元数据数量和七字段结构检查通过。"
          style={{ marginBottom: 18 }}
        />

        <Row gutter={[18, 18]} align="top">
          <Col xs={24} lg={10}>
            <div className="ui-card">
              <div className="ui-card-head">
                <span className="ico"><InboxOutlined /></span>
                <h3>1. 上传与只读检测</h3>
              </div>
              <div className="ui-card-body">
                <Upload.Dragger
                  accept=".jpg,.jpeg,.png,image/jpeg,image/png"
                  maxCount={1}
                  showUploadList={false}
                  beforeUpload={(selected) => {
                    chooseFile(selected);
                    return false;
                  }}
                >
                  <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                  <p className="ant-upload-text">点击或拖入 JPEG/PNG 图片</p>
                  <p className="ant-upload-hint">检测操作只读取文件，不修改原图</p>
                </Upload.Dragger>

                {file ? (
                  <div style={{ marginTop: 16 }}>
                    <Text strong>{file.name}</Text>
                    <Text type="secondary"> · {(file.size / 1024).toFixed(1)} KiB</Text>
                    {previewUrl ? (
                      <img
                        src={previewUrl}
                        alt="待检测图片预览"
                        style={{
                          display: "block",
                          width: "100%",
                          maxHeight: 260,
                          objectFit: "contain",
                          marginTop: 12,
                          borderRadius: 10,
                          background: "#f3f5f8",
                        }}
                      />
                    ) : null}
                  </div>
                ) : null}

                <Button
                  type="primary"
                  block
                  size="large"
                  onClick={inspect}
                  loading={busy === "detect"}
                  disabled={!file || busy !== null}
                  style={{ marginTop: 16 }}
                >
                  提取标识并检测合规性
                </Button>
              </div>
            </div>
          </Col>

          <Col xs={24} lg={14}>
            <div className="ui-card">
              <div className="ui-card-head">
                <span className="ico"><SafetyCertificateOutlined /></span>
                <h3>2. 根据检测结论处理</h3>
              </div>
              <div className="ui-card-body">
                {!detection ? (
                  <Alert type="info" showIcon message="请先完成标识提取与合规检测" />
                ) : (
                  <>
                    <Space wrap>
                      <Tag color={CONCLUSION_COLOR[conclusion || "indeterminate"]}>
                        {CONCLUSION_LABEL[conclusion || "indeterminate"]}
                      </Tag>
                      <Text>
                        标识份数：{detection.detection.metadata_compliance?.record_count ?? "—"}
                      </Text>
                    </Space>

                    {conclusion === "not_found" ? (
                      <Alert
                        type="info"
                        showIcon
                        message="未发现标识，可以进入普通首次打标流程"
                        description="后端将自动生成 ProduceID，并按首次写入关系构造完整七字段。"
                        style={{ marginTop: 14 }}
                      />
                    ) : null}
                    {conclusion === "compliant" ? (
                      <Alert
                        type="success"
                        showIcon
                        message="已有唯一且结构合规的标识，无需修复"
                        description="如确需更新，必须明确选择整体替换；系统不会追加第二份标识。"
                        style={{ marginTop: 14 }}
                      />
                    ) : null}
                    {conclusion === "noncompliant" ? (
                      <Alert
                        type="error"
                        showIcon
                        message="检测到不合规标识，请先生成修复计划"
                        description="系统只自动处理确定性问题；损坏 JSON、来源冲突或证据不足会转人工审核。"
                        style={{ marginTop: 14 }}
                      />
                    ) : null}
                    {conclusion === "indeterminate" ? (
                      <Alert
                        type="warning"
                        showIcon
                        message="当前证据不足，已停止自动处理"
                        description="请人工检查文件来源、读取器差异、Extended XMP 或 C2PA 数据。"
                        style={{ marginTop: 14 }}
                      />
                    ) : null}

                    <Divider orientation="left">首次打标 / 明确整体替换</Divider>
                    <Row gutter={[12, 12]}>
                      <Col xs={24} md={8}>
                        <Text strong>Label</Text>
                        <Radio.Group
                          value={label}
                          onChange={(event) => setLabel(event.target.value)}
                          style={{ display: "flex", marginTop: 8 }}
                        >
                          <Radio.Button value="1">1</Radio.Button>
                          <Radio.Button value="2">2</Radio.Button>
                          <Radio.Button value="3">3</Radio.Button>
                        </Radio.Group>
                      </Col>
                      <Col xs={24} md={16}>
                        <Text strong>ContentProducer</Text>
                        <Input
                          value={producer}
                          onChange={(event) => setProducer(event.target.value)}
                          maxLength={1024}
                          style={{ marginTop: 8 }}
                        />
                      </Col>
                    </Row>
                    {conclusion === "compliant" ? (
                      <Checkbox
                        checked={replaceConfirmed}
                        onChange={(event) => setReplaceConfirmed(event.target.checked)}
                        style={{ marginTop: 14 }}
                      >
                        我已确认整体替换现有标识，并理解旧标识不会继续保留在文件内
                      </Checkbox>
                    ) : null}
                    <Button
                      type="primary"
                      onClick={labelImage}
                      loading={busy === "label"}
                      disabled={!mayLabel || busy !== null}
                      style={{ marginTop: 14 }}
                    >
                      {conclusion === "not_found"
                        ? "生成编号并首次打标"
                        : conclusion === "compliant"
                          ? "确认并整体替换"
                          : "请使用下方修复流程"}
                    </Button>

                    <Divider orientation="left">不合规修复</Divider>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Button
                        icon={<ToolOutlined />}
                        onClick={planRepair}
                        loading={busy === "plan"}
                        disabled={!mayPlanRepair || busy !== null}
                      >
                        生成修复计划
                      </Button>
                      {repairPlan ? (
                        <>
                          <Descriptions size="small" bordered column={1}>
                            <Descriptions.Item label="plan_id">
                              {repairPlan.plan_id}
                            </Descriptions.Item>
                            <Descriptions.Item label="可执行">
                              {repairPlan.repair_plan.executable ? "是" : "否"}
                            </Descriptions.Item>
                            <Descriptions.Item label="修复性">
                              {repairPlan.repair_plan.repairability}
                            </Descriptions.Item>
                            <Descriptions.Item label="过期时间">
                              {repairPlan.expires_at}
                            </Descriptions.Item>
                          </Descriptions>
                          {repairPlan.repair_plan.blocking_reasons.length ? (
                            <Alert
                              type="warning"
                              showIcon
                              message="阻断原因"
                              description={repairPlan.repair_plan.blocking_reasons.join("；")}
                            />
                          ) : null}
                          {repairPlan.repair_plan.warnings.length ? (
                            <Alert
                              type="warning"
                              showIcon
                              message="风险提示"
                              description={repairPlan.repair_plan.warnings.join("；")}
                            />
                          ) : null}
                          <Paragraph style={{ marginBottom: 0 }}>
                            <Text strong>拟执行动作：</Text>
                            {repairPlan.repair_plan.actions.length
                              ? repairPlan.repair_plan.actions
                                  .map((action) => JSON.stringify(action))
                                  .join("；")
                              : "无"}
                          </Paragraph>
                          {repairPlan.repair_plan.executable ? (
                            <>
                              <Input
                                addonBefore="操作标签"
                                value={operatorLabel}
                                onChange={(event) => setOperatorLabel(event.target.value)}
                                maxLength={200}
                              />
                              <Checkbox
                                checked={repairConfirmed}
                                onChange={(event) => setRepairConfirmed(event.target.checked)}
                              >
                                我已核对 plan_id、plan_hash、字段变化，并确认生成新文件
                              </Checkbox>
                              <Button
                                danger
                                onClick={executeRepair}
                                loading={busy === "repair"}
                                disabled={!repairConfirmed || busy !== null}
                              >
                                确认并执行修复
                              </Button>
                            </>
                          ) : null}
                        </>
                      ) : null}
                    </Space>
                  </>
                )}
              </div>
            </div>
          </Col>
        </Row>

        {outputDetection ? (
          <div ref={outputResultRef} style={{ scrollMarginTop: 100 }}>
            <Alert
              showIcon
              type="success"
              message="下面是新生成文件中的标识，请以此结果为准"
              description="原文件检测结果会继续保留在后面用于对照，系统没有覆盖原文件。"
              style={{ marginTop: 18 }}
            />
            <ResultSummary
              result={outputDetection}
              title="新文件自动回读与复检结果（替换/修复后）"
            />
          </div>
        ) : null}

        {detection ? <ResultSummary result={detection} title="原文件检测结果（替换前对照）" /> : null}

        {labelJob || repairJob ? (
          <div className="ui-card" style={{ marginTop: 18 }}>
            <div className="ui-card-head">
              <span className="ico">↻</span>
              <h3>3. 异步任务与结果下载</h3>
            </div>
            <div className="ui-card-body">
              {labelJob ? (
                <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
                  <Descriptions.Item label="任务类型">元数据打标</Descriptions.Item>
                  <Descriptions.Item label="job_id">{labelJob.job_id}</Descriptions.Item>
                  <Descriptions.Item label="状态">{labelJob.status}</Descriptions.Item>
                  <Descriptions.Item label="阶段">{labelJob.stage}</Descriptions.Item>
                </Descriptions>
              ) : null}
              {repairJob ? (
                <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
                  <Descriptions.Item label="任务类型">不合规修复</Descriptions.Item>
                  <Descriptions.Item label="job_id">{repairJob.job_id}</Descriptions.Item>
                  <Descriptions.Item label="状态">{repairJob.status}</Descriptions.Item>
                  <Descriptions.Item label="阶段">{repairJob.stage}</Descriptions.Item>
                </Descriptions>
              ) : null}
              {busy === "label" || busy === "repair" ? (
                <Progress percent={labelJob?.progress || repairJob?.progress || 0} status="active" />
              ) : null}
              {validationPassed ? (
                <Alert
                  type="success"
                  showIcon
                  message="回读、七字段结构、唯一性和图片完整性校验均通过"
                  style={{ marginTop: 14 }}
                />
              ) : null}
              <Space wrap style={{ marginTop: 14 }}>
                {labelJob?.status === "succeeded" ? (
                  <Button
                    type="primary"
                    icon={<FolderOpenOutlined />}
                    onClick={() =>
                      downloadMetadataLabelOutput(labelJob.job_id)
                        .then((response) =>
                          saveResponseWithPicker(
                            response,
                            labelJob.output?.file_name || "labeled-image.jpg"
                          )
                        )
                        .catch((error) => {
                          if ((error as DOMException)?.name !== "AbortError") {
                            message.error(errorMessage(error));
                          }
                        })
                    }
                  >
                    选择位置保存打标结果
                  </Button>
                ) : null}
                {repairJob?.status === "succeeded" ? (
                  <Button
                    type="primary"
                    icon={<FolderOpenOutlined />}
                    onClick={() =>
                      downloadRepairOutput(repairJob.job_id)
                        .then((response) =>
                          saveResponseWithPicker(response, "repaired-image.jpg")
                        )
                        .catch((error) => {
                          if ((error as DOMException)?.name !== "AbortError") {
                            message.error(errorMessage(error));
                          }
                        })
                    }
                  >
                    选择位置保存修复结果
                  </Button>
                ) : null}
              </Space>
              {(labelJob?.status === "succeeded" || repairJob?.status === "succeeded") ? (
                <Paragraph type="secondary" style={{ margin: "12px 0 0" }}>
                  点击保存按钮后，Chrome/Edge 会打开系统“另存为”窗口；取消选择不会删除服务器中的临时结果。
                </Paragraph>
              ) : null}
            </div>
          </div>
        ) : null}

      </section>
    </>
  );
}
