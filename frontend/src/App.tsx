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

export default function App() {
  const [view, setView] = useState<View>("home");
  const [result, setResult] = useState<DetectResponse | null>(null);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [view]);

  function handleResult(r: DetectResponse) {
    setResult(r);
    setView("report");
  }

  function renderView() {
    switch (view) {
      case "home":
        return <UploadPage onResult={handleResult} onNavigate={setView} />;
      case "report":
        return result ? (
          <ReportPage data={result} onBack={() => setView("home")} />
        ) : (
          <UploadPage onResult={handleResult} onNavigate={setView} />
        );
      case "overview":
        return <OverviewPage onNavigate={setView} />;
      case "regulations":
        return <RegulationsPage onNavigate={setView} />;
      case "standards":
        return <StandardsPage />;
      case "api":
        return <ApiPage />;
      case "help":
        return <HelpPage onNavigate={setView} />;
      case "console":
        return <ConsolePage onNavigate={setView} />;
      case "labeling":
        return <LabelingPage />;
      case "mediaInspect":
        return <MediaInspectPage />;
      case "mediaRepair":
        return <RepairPage onNavigate={setView} />;
      default:
        return <UploadPage onResult={handleResult} onNavigate={setView} />;
    }
  }

  return (
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp>
        <div className="app-bg">
          <TopNav current={view} onNavigate={setView} />
          {renderView()}
          <Footer onNavigate={setView} />
        </div>
      </AntdApp>
    </ConfigProvider>
  );
}
