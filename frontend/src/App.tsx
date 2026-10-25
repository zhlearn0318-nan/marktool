import { useState, useEffect } from "react";
import { ConfigProvider, App as AntdApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import { theme } from "./theme";
import TopNav from "./components/TopNav";
import Footer from "./components/Footer";
import UploadPage from "./pages/UploadPage";
import ReportPage from "./pages/ReportPage";
import OverviewPage from "./pages/OverviewPage";
import RegulationsPage from "./pages/RegulationsPage";
import StandardsPage from "./pages/StandardsPage";
import ApiPage from "./pages/ApiPage";
import HelpPage from "./pages/HelpPage";
import ConsolePage from "./pages/ConsolePage";
import LabelingPage from "./pages/LabelingPage";
import MediaInspectPage from "./pages/MediaInspectPage";
import RepairPage from "./pages/RepairPage";
import type { DetectResponse } from "./types";
import type { View } from "./nav";
import { NAV } from "./nav";
import BrandHome from "./pages/BrandHome";
import Workspace from "./components/Workspace";
import "./styles/redesign.css";

function readView(): View {
  const hash = window.location.hash.slice(1);
  return NAV.some((item) => item.view === hash) ||
    hash === "console" ||
    hash === "report"
    ? (hash as View)
    : "welcome";
}

export default function App() {
  const [view, setView] = useState<View>(readView);
  const [result, setResult] = useState<DetectResponse | null>(null);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    const label =
      NAV.find((item) => item.view === view)?.label ??
      (view === "console" ? "工作台" : "检测报告");
    document.title = `${label} · 源迹 TraceMark`;
  }, [view]);

  useEffect(() => {
    const sync = () => setView(readView());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  function navigate(next: View) {
    if (window.location.hash !== `#${next}`) window.location.hash = next;
    setView(next);
  }

  function handleResult(r: DetectResponse) {
    setResult(r);
    navigate("report");
  }

  function renderView() {
    switch (view) {
      case "welcome":
        return <BrandHome onNavigate={navigate} />;
      case "home":
        return <UploadPage onResult={handleResult} onNavigate={navigate} />;
      case "report":
        return result ? (
          <ReportPage data={result} onBack={() => navigate("home")} />
        ) : (
          <UploadPage onResult={handleResult} onNavigate={navigate} />
        );
      case "overview":
        return <OverviewPage onNavigate={navigate} />;
      case "regulations":
        return <RegulationsPage onNavigate={navigate} />;
      case "standards":
        return <StandardsPage />;
      case "api":
        return <ApiPage />;
      case "help":
        return <HelpPage onNavigate={navigate} />;
      case "console":
        return (
          <ConsolePage onNavigate={navigate} hasReport={Boolean(result)} />
        );
      case "labeling":
        return <LabelingPage />;
      case "mediaInspect":
        return <MediaInspectPage />;
      case "mediaRepair":
        return <RepairPage onNavigate={navigate} />;
      default:
        return <UploadPage onResult={handleResult} onNavigate={navigate} />;
    }
  }

  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp>
        <div className="app-bg">
          <a
            className="skip-link"
            href="#main-content"
            onClick={(e) => {
              e.preventDefault();
              document.getElementById("main-content")?.focus();
            }}
          >
            跳到主要内容
          </a>
          <TopNav current={view} onNavigate={navigate} />
          <main
            id="main-content"
            tabIndex={-1}
            className="overflow-x-hidden w-full max-w-full"
          >
            {view === "welcome" ? (
              renderView()
            ) : (
              <Workspace view={view} onNavigate={navigate}>
                {renderView()}
              </Workspace>
            )}
          </main>
          {view === "welcome" && <Footer onNavigate={navigate} />}
        </div>
      </AntdApp>
    </ConfigProvider>
  );
}
