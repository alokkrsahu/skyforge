import { create } from "zustand";
import type { TelemetryFrame, HealthFrame, CmdResult, Frame } from "./types";

export type View = "author" | "preflight" | "bringup" | "fly";

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

// Single WebSocket: telemetry (10 Hz) · health (1 Hz) · cmd_result echoes. Auto-reconnect.
export function connectWs(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws`;
  const open = () => {
    const ws = new WebSocket(url);
    ws.onopen = () => useStore.setState({ connected: true });
    ws.onclose = () => {
      useStore.setState({ connected: false });
      setTimeout(open, 1000);
    };
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data) as Frame;
      if (f.type === "telemetry") useStore.setState({ telemetry: f });
      else if (f.type === "health") useStore.setState({ health: f });
      else if (f.type === "cmd_result") useStore.getState().pushCmd(f as CmdResult);
    };
  };
  open();
}
