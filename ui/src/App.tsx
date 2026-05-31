import StatusStrip from "./components/StatusStrip";
import Viewport from "./components/Viewport";
import CommandDeck from "./components/CommandDeck";
import EmergencyRail from "./components/EmergencyRail";
import CommandLog from "./components/CommandLog";
import LifecycleRail from "./components/LifecycleRail";
import AuthorPanel from "./components/AuthorPanel";
import PreflightPanel from "./components/PreflightPanel";
import BringupPanel from "./components/BringupPanel";
import MonitorPanel from "./components/MonitorPanel";
import ReviewPanel from "./components/ReviewPanel";
import { useStore } from "./store";

function FlyView() {
  return (
    <div className="flyview">
      <Viewport />
      <CommandDeck />
    </div>
  );
}

export default function App() {
  const view = useStore((s) => s.view);
  return (
    <div className="app">
      <header><h1>SkyForge</h1><StatusStrip /></header>
      <div className="body">
        <LifecycleRail />
        <main>
          {view === "author" && <AuthorPanel />}
          {view === "preflight" && <PreflightPanel />}
          {view === "bringup" && <BringupPanel />}
          {view === "fly" && <FlyView />}
          {view === "monitor" && <MonitorPanel />}
          {view === "review" && <ReviewPanel />}
        </main>
        <aside>
          <EmergencyRail />
          <CommandLog />
        </aside>
      </div>
    </div>
  );
}
