import { useState } from "react";
import { Input, Segmented, message } from "antd";
import PageBanner from "../components/PageBanner";

type Dir = "GB→C2PA" | "C2PA→GB";

const GB_PRESET = {
  Label: "1",
  ContentProducer: "示例生成服务商",
  ProduceID: "PRD-20260610-0001",
  ContentPropagator: "示例传播平台",
  PropagateID: "PRO-20260610-0009",
};

const C2PA_PRESET = {
  claim_generator: "示例生成服务商/1.0",
  instance_id: "xmp:iid:PRD-20260610-0001",
  assertions: [
    {
      label: "c2pa.actions",
      data: { actions: [{ action: "c2pa.created", digitalSourceType: "trainedAlgorithmicMedia" }] },
    },
  ],
  ingredients: [{ title: "示例传播平台", instance_id: "PRO-20260610-0009" }],
};

const MAPPING: Record<Dir, [string, string][]> = {
  "GB→C2PA": [
    ["Label", "assertions[].actions.digitalSourceType"],
    ["ContentProducer", "claim_generator · CreativeWork.author"],
    ["ProduceID", "instance_id"],
    ["ContentPropagator", "ingredients[].title"],
    ["PropagateID", "ingredients[].instance_id"],
  ],
  "C2PA→GB": [
    ["claim_generator", "ContentProducer"],
    ["instance_id", "ProduceID"],
    ["digitalSourceType", "Label"],
    ["ingredients[].title", "ContentPropagator"],
    ["ingredients[].instance_id", "PropagateID"],
  ],
};

function gbToC2pa(gb: any) {
  return {
    claim_generator: `${gb.ContentProducer ?? "unknown"}/1.0`,
    instance_id: `xmp:iid:${gb.ProduceID ?? ""}`,
    assertions: [
      {
        label: "c2pa.actions",
        data: {
          actions: [
            {
              action: "c2pa.created",
              digitalSourceType:
                gb.Label === "1" ? "trainedAlgorithmicMedia" : "humanEdits",
            },
          ],
        },
      },
      { label: "stds.schema-org.CreativeWork", data: { author: [{ name: gb.ContentProducer }] } },
    ],
    ingredients: gb.ContentPropagator
      ? [{ title: gb.ContentPropagator, instance_id: gb.PropagateID }]
      : [],
    signature: "⚠ 不可用：签名信任链需由原始签发方建立",
  };
}

function c2paToGb(c: any) {
  const dst = c?.assertions?.[0]?.data?.actions?.[0]?.digitalSourceType;
  const ing = c?.ingredients?.[0];
  return {
    Label: dst === "trainedAlgorithmicMedia" ? "1" : "2",
    ContentProducer: String(c.claim_generator ?? "").split("/")[0],
    ProduceID: String(c.instance_id ?? "").replace("xmp:iid:", ""),
    ContentPropagator: ing?.title ?? "",
    PropagateID: ing?.instance_id ?? "",
    _note: "由 C2PA 转入：签名不可用，仅结构合规",
  };
}

export default function StandardsPage() {
  const [dir, setDir] = useState<Dir>("GB→C2PA");
  const [input, setInput] = useState(JSON.stringify(GB_PRESET, null, 2));
  const [output, setOutput] = useState("");

  function reset(d: Dir) {
    setDir(d);
    setInput(JSON.stringify(d === "GB→C2PA" ? GB_PRESET : C2PA_PRESET, null, 2));
    setOutput("");
  }

  function convert() {
    let obj: any;
    try {
      obj = JSON.parse(input);
    } catch {
      message.error("输入不是合法 JSON，请检查格式");
      return;
    }
    const out = dir === "GB→C2PA" ? gbToC2pa(obj) : c2paToGb(obj);
    setOutput(JSON.stringify(out, null, 2));
    message.success("转换完成");
  }

  return (
    <>
      <PageBanner
        eyebrow="STANDARD INTEROP"
        title="标准互转"
        sub="不同地区的标识标准互不兼容。源迹 TraceMark 在国标 AIGC JSON 与 C2PA manifest 之间建立翻译层 —— 下方为前端演示，可直接编辑并即时转换。"
      />

      <section className="section">
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 24 }}>
          <Segmented
            size="large"
            value={dir}
            onChange={(v) => reset(v as Dir)}
            options={["GB→C2PA", "C2PA→GB"]}
          />
        </div>

        <div className="grid-2" style={{ alignItems: "start" }}>
          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">⇥</span>
              <h3>源数据 · {dir.split("→")[0]}</h3>
            </div>
            <div className="ui-card-body">
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                autoSize={{ minRows: 14, maxRows: 22 }}
                style={{ fontFamily: "Consolas, Menlo, monospace", fontSize: 12.5 }}
              />
              <button className="btn-gold" style={{ width: "100%", marginTop: 16 }} onClick={convert}>
                执行转换 →
              </button>
            </div>
          </div>

          <div className="ui-card">
            <div className="ui-card-head">
              <span className="ico">⇤</span>
              <h3>目标数据 · {dir.split("→")[1]}</h3>
            </div>
            <div className="ui-card-body">
              {output ? (
                <pre className="code-block" style={{ minHeight: 320, margin: 0 }}>
                  {output}
                </pre>
              ) : (
                <div
                  style={{
                    minHeight: 320,
                    display: "grid",
                    placeItems: "center",
                    color: "var(--text-400)",
                    border: "1px dashed var(--line-strong)",
                    borderRadius: 12,
                    fontSize: 14,
                  }}
                >
                  点击「执行转换」生成目标格式
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="ui-card" style={{ marginTop: 24 }}>
          <div className="ui-card-head">
            <span className="ico">⇄</span>
            <h3>字段映射对照</h3>
          </div>
          <div className="ui-card-body" style={{ padding: 0 }}>
            <table className="spec-table">
              <thead>
                <tr>
                  <th>{dir.split("→")[0]} 字段</th>
                  <th>{dir.split("→")[1]} 字段</th>
                </tr>
              </thead>
              <tbody>
                {MAPPING[dir].map((m, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 700 }}>{m[0]}</td>
                    <td style={{ color: "var(--text-600)" }}>{m[1]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div
          style={{
            marginTop: 20,
            background: "var(--amber-bg)",
            color: "var(--amber)",
            borderRadius: 12,
            padding: "14px 18px",
            fontSize: 13.5,
            lineHeight: 1.7,
          }}
        >
          转换限制透明告知：C2PA 的签名信任链无法凭空生成（签名只有原始签发方才能打）。转换后的文件标注「签名不可用但数据结构合规」。
        </div>
      </section>
    </>
  );
}
