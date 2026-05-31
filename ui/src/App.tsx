import StatusStrip from "./components/StatusStrip";
import Viewport from "./components/Viewport";
import CommandDeck from "./components/CommandDeck";
import EmergencyRail from "./components/EmergencyRail";
import CommandLog from "./components/CommandLog";

export default function App() {
  return (
    <div className="app">
      <header><h1>SkyForge</h1><StatusStrip /></header>
      <main>
        <Viewport />
        <aside>
          <CommandDeck />
          <EmergencyRail />
          <CommandLog />
        </aside>
      </main>
    </div>
  );
}
