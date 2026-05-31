import { create } from "zustand";
import { bridgeWsUrl } from "./api";
import type { TelemetryFrame, HealthFrame, CmdResult, Frame } from "./types";

export type View = "author" | "preflight" | "bringup" | "fly" | "monitor" | "review";

interface State {
  connected: boolean;
  telemetry: TelemetryFrame | null;
  health: HealthFrame | null;
  cmdLog: CmdResult[];
  view: View;
  armed: boolean;                      // preflight GO → Fly unlocked
  compiledShow: string | null;         // path of the last compiled/selected .skyforge.json
  pushCmd: (r: CmdResult) => void;
  setView: (v: View) => void;
  setArmed: (a: boolean) => void;
  setCompiledShow: (p: string | null) => void;
}

export const useStore = create<State>((set) => ({
  connected: false,
  telemetry: null,
  health: null,
  cmdLog: [],
  view: "author",
  armed: false,
  compiledShow: null,
  pushCmd: (r) => set((s) => ({ cmdLog: [r, ...s.cmdLog].slice(0, 60) })),
  setView: (view) => set({ view }),
  setArmed: (armed) => set({ armed }),
  setCompiledShow: (compiledShow) => set({ compiledShow }),
}));

// Single WebSocket to the bridge: telemetry (10 Hz) · health (1 Hz) · cmd_result echoes.
// Auto-reconnect with capped exponential backoff (no tight 1 Hz storm when the bridge
// isn't up yet — e.g. before bring-up, or when served by the gateway which has no /ws).
let sock: WebSocket | null = null;

export function connectWs(): void {
  if (sock) { try { sock.close(); } catch { /* ignore */ } sock = null; }
  let delay = 1000;
  const open = () => {
    const ws = new WebSocket(bridgeWsUrl());
    sock = ws;
    ws.onopen = () => { delay = 1000; useStore.setState({ connected: true }); };
    ws.onclose = () => {
      useStore.setState({ connected: false });
      if (sock === ws) { setTimeout(open, delay); delay = Math.min(delay * 2, 8000); }
    };
    ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data) as Frame;
      if (f.type === "telemetry") useStore.setState({ telemetry: f });
      else if (f.type === "health") useStore.setState({ health: f });
      else if (f.type === "cmd_result") useStore.getState().pushCmd(f as CmdResult);
    };
  };
  open();
}
