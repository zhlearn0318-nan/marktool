import { useRef, useState } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import Icon from "../components/Icon";
import type { View } from "../nav";

gsap.registerPlugin(ScrollTrigger, useGSAP);
const scenes = [
  {
    title: "内容发布",
    sub: "给创作一个清晰的来源",
    text: "为即将发布的图片、视频、音频和文档写入 AIGC 元数据，让内容生产者与内容编号随文件一同流转。",
    view: "labeling" as View,
    action: "为内容添加标识",
  },
  {
    title: "内容审核",
    sub: "让核查有据可依",
    text: "读取文件中的已有标识，查看结构、字段与媒体完整性检查结果，准确定位需要关注的问题。",
    view: "mediaInspect" as View,
    action: "检查一份文件",
  },
  {
    title: "标识修复",
    sub: "每一次修改，都心中有数",
    text: "先查看图片、视频或文档的元数据修复计划，再确认执行。保留原件，修复后下载新文件。",
    view: "mediaRepair" as View,
    action: "进入修复工作台",
  },
];

export default function BrandHome({
  onNavigate,
}: {
  onNavigate: (view: View) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        gsap.from(".brand-hero .enter", {
          y: 25,
          opacity: 0,
          duration: 1,
          stagger: 0.13,
          ease: "power3.out",
        });
        gsap.utils.toArray<HTMLElement>(".scroll-image").forEach((el) => {
          gsap.fromTo(
            el,
            { scale: 0.8 },
            {
              scale: 1,
              ease: "none",
              scrollTrigger: {
                trigger: el,
                start: "top 95%",
                end: "center 65%",
                scrub: true,
              },
            },
          );
          gsap.to(el, {
            opacity: 0.2,
            ease: "none",
            scrollTrigger: {
              trigger: el,
              start: "bottom 35%",
              end: "bottom top",
              scrub: true,
            },
          });
        });
      });
      mm.add(
        "(min-width: 1000px) and (prefers-reduced-motion: no-preference)",
        () => {
          ScrollTrigger.create({
            trigger: ".process-layout",
            start: "top 128px",
            end: "bottom 640px",
            pin: ".process-intro",
            pinSpacing: false,
          });
        },
      );
      return () => mm.revert();
    },
    { scope: root },
  );

  return (
    <div ref={root} className="brand-home">
      <section className="brand-hero">
        <div className="hero-copy">
          <div className="hero-kicker enter">
            <span /> 让可信，随内容同行
          </div>
          <h1 className="max-w-6xl enter">
            让每一份创作
            <br />
            都有<span>迹</span>可循。
          </h1>
          <p className="hero-description enter">
            从生成到传播，为 AI 内容建立清晰的标识。
            <br className="desktop-break" />
            源迹将元数据标注、合规检测与修复，融入你的内容工作流。
          </p>
          <div className="hero-actions enter">
            <button
              className="tm-button primary"
              onClick={() => onNavigate("console")}
            >
              进入工作台 <Icon name="arrow" size={18} />
            </button>
            <button
              className="tm-button secondary"
              onClick={() =>
                document
                  .getElementById("capabilities")
                  ?.scrollIntoView({
                    behavior: matchMedia("(prefers-reduced-motion: reduce)")
                      .matches
                      ? "instant"
                      : "smooth",
                  })
              }
            >
              探索平台能力 <span aria-hidden="true">↓</span>
            </button>
          </div>
          <div className="hero-footnote enter">
            面向图片、视频、音频与文档的 AIGC 元数据管理
          </div>
        </div>
        <div
          className="hero-art enter"
          role="img"
          aria-label="层叠的透明内容载体与环形轨迹，象征内容来源的可追溯性"
        >
          <div className="art-grid" />
          <div className="art-halo" />
          <div className="orbital orbital-one" />
          <div className="orbital orbital-two" />
          <div className="artifact-stack">
            <div className="artifact-sheet sheet-back" />
            <div className="artifact-sheet sheet-middle" />
            <div className="artifact-sheet sheet-front">
              <div className="artifact-photo" />
              <div className="artifact-mark">
                <span />
                <span />
                <span />
              </div>
              <div className="artifact-rule" />
            </div>
          </div>
          <div className="art-caption">
            <span>CONTENT WITH ORIGIN</span>
            <span>TraceMark</span>
          </div>
        </div>
      </section>

      <div
        className="format-ribbon"
        aria-label="支持图片、视频、音频与文档的 AIGC 元数据标注"
      >
        <span className="ribbon-label">让标准融入工作流</span>
        <div className="marquee-window">
          <div className="marquee-track">
            {[0, 1].map((copy) => (
              <div
                className="marquee-group"
                key={copy}
                aria-hidden={copy === 1}
              >
                {["GB 45438–2025", "JPEG", "PNG", "MP4", "M4A", "MP3", "Markdown", "PDF", "HTML", "DOCX"].map(
                  (name) => (
                    <span key={name}>
                      {name}
                      <i />
                    </span>
                  ),
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      <section className="brand-section" id="capabilities">
        <div className="section-heading">
          <div>
            <p className="section-caption">一份内容，完整照看</p>
            <h2>
              复杂的标识流程，
              <br />
              清晰的每一步。
            </h2>
          </div>
          <p>
            从第一次写入到发布前核查，
            <br />
            把专业能力交给工具，把判断留给你。
          </p>
        </div>
        <div className="capability-grid grid-flow-dense">
          <button
            className="capability-card capability-label"
            onClick={() => onNavigate("labeling")}
          >
            <div className="cap-card-copy">
              <span className="card-icon">
                <Icon name="label" size={24} />
              </span>
              <h3>为创作，写下来源</h3>
              <p>
                为图片、视频、音频和文档添加 AIGC 隐式标识。
                <br />
                让生产者与内容编号成为文件的一部分。
              </p>
              <span className="text-link">
                元数据标注 <Icon name="arrow" size={18} />
              </span>
            </div>
            <div className="metadata-art" aria-hidden="true">
              <div className="mini-file">
                <Icon name="image" size={24} />
                <span>创作原件</span>
              </div>
              <div className="connecting-line" />
              <div className="mini-metadata">
                <span>AIGC</span>
                <div>
                  内容生产者 <b>已记录</b>
                </div>
                <div>
                  内容唯一编号 <b>已写入</b>
                </div>
                <div>
                  标识类别 <b>AI 生成</b>
                </div>
              </div>
            </div>
          </button>
          <button
            className="capability-card capability-inspect"
            onClick={() => onNavigate("mediaInspect")}
          >
            <span className="card-icon">
              <Icon name="scan" size={24} />
            </span>
            <h3>看见标识，也看清问题</h3>
            <p>
              逐项检查元数据结构与字段，
              <br />
              把每个结论追溯到具体依据。
            </p>
            <div className="scan-art" aria-hidden="true">
              <span />
              <span />
              <span />
              <span />
              <i />
            </div>
            <span className="text-link">
              合规检测 <Icon name="arrow" size={18} />
            </span>
          </button>
          <button
            className="capability-card capability-repair"
            onClick={() => onNavigate("mediaRepair")}
          >
            <span className="card-icon">
              <Icon name="repair" size={24} />
            </span>
            <h3>确认之后，安心修复</h3>
            <p>
              先预览文件修复计划，再执行修改。
              <br />
              保留原件，只调整元数据。
            </p>
            <span className="text-link">
              元数据修复 <Icon name="arrow" size={18} />
            </span>
          </button>
          <button
            className="capability-card capability-guide"
            onClick={() => onNavigate("help")}
          >
            <div>
              <span className="card-icon">
                <Icon name="book" size={24} />
              </span>
              <h3>专业的事，也能轻松上手</h3>
              <p>
                从选择文件到下载结果，
                <br />
                找到适合当前任务的操作路径。
              </p>
              <span className="text-link">
                查看使用指南 <Icon name="arrow" size={18} />
              </span>
            </div>
            <div className="guide-art" aria-hidden="true">
              <div />
              <div />
              <div />
              <span>
                清晰
                <br />
                有序
              </span>
            </div>
          </button>
        </div>
      </section>

      <section className="process-section">
        <div className="process-layout">
          <div className="process-intro">
            <p className="section-caption">从文件到可信依据</p>
            <h2>
              有来源。
              <br />
              有依据。
              <br />
              <span>有把握。</span>
            </h2>
            <p>
              不必在复杂术语之间来回寻找。
              <br />
              跟随清晰的步骤，完成一次内容核查。
            </p>
            <button
              className="text-link"
              onClick={() => onNavigate("mediaInspect")}
            >
              开始你的第一次检测 <Icon name="arrow" size={18} />
            </button>
          </div>
          <div className="process-steps">
            <article className="process-step">
              <span className="step-number">01</span>
              <div>
                <h3>从一份文件开始</h3>
                <p>选择图片、视频、音频或文档，平台根据实际文件类型进入对应处理流程。</p>
              </div>
              <div className="process-visual scroll-image">
                <img
                  src="/assets/content-landscape.jpg"
                  alt="林间景观，作为待核查的图片内容示意"
                  loading="lazy"
                />
                <div className="visual-file">
                  <Icon name="image" />
                  <span>一份创作，无限可能</span>
                </div>
              </div>
            </article>
            <article className="process-step">
              <span className="step-number">02</span>
              <div>
                <h3>每一项检查，都有解释</h3>
                <p>
                  查看标识是否存在、字段是否完整，以及媒体本身是否可正常读取。
                </p>
              </div>
              <div className="check-illustration" aria-label="检查项目示意">
                <span>检查内容</span>
                {["元数据载体读取", "AIGC 字段结构", "媒体完整性"].map((s) => (
                  <div key={s}>
                    <Icon name="scan" size={18} />
                    {s}
                    <span>逐项核查</span>
                  </div>
                ))}
                <small>界面示意 · 实际结论以文件检测结果为准</small>
              </div>
            </article>
            <article className="process-step">
              <span className="step-number">03</span>
              <div>
                <h3>带着明确结果，继续下一步</h3>
                <p>
                  根据检测结果补充标识，或为图片、视频和文档生成修复计划。确认修改后，再下载处理好的文件。
                </p>
              </div>
              <div className="workflow-mini">
                <span>查看结果</span>
                <Icon name="arrow" />
                <span>确认处理</span>
                <Icon name="arrow" />
                <span>下载文件</span>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section className="brand-section scenarios-section">
        <div className="section-heading">
          <div>
            <p className="section-caption">融入你的日常工作</p>
            <h2>
              让每一次 <span className="inline-image" aria-hidden="true" />{" "}
              交付，
              <br />
              多一份确定。
            </h2>
          </div>
          <p>
            无论你在创作、审核还是管理内容，
            <br />
            都能从这里找到下一步。
          </p>
        </div>
        <div className="scenario-accordion">
          {scenes.map((scene, i) => (
            <article
              key={scene.title}
              className={`scenario ${active === i ? "is-active" : ""}`}
            >
              <button
                className="scenario-toggle"
                onClick={() => setActive(i)}
                onMouseEnter={() => setActive(i)}
                aria-expanded={active === i}
                aria-controls={`scene-${i}`}
              >
                <span>{scene.title}</span>
                <span aria-hidden="true">{active === i ? "−" : "+"}</span>
              </button>
              <div
                id={`scene-${i}`}
                className="scenario-detail"
                hidden={active !== i}
              >
                <h3>{scene.sub}</h3>
                <p>{scene.text}</p>
                <button
                  className="text-link"
                  onClick={() => onNavigate(scene.view)}
                >
                  {scene.action}
                  <Icon name="arrow" size={18} />
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="brand-cta">
        <div className="cta-orbit" aria-hidden="true" />
        <p>源迹 TraceMark</p>
        <h2>
          让内容的下一程，
          <br />
          从清晰的来源开始。
        </h2>
        <button
          className="tm-button light"
          onClick={() => onNavigate("console")}
        >
          开始使用源迹 <Icon name="arrow" size={18} />
        </button>
        <span>标注 · 检测 · 修复</span>
      </section>
    </div>
  );
}
