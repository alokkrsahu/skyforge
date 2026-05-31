import { create } from "zustand";
import type { TelemetryFrame, HealthFrame, CmdResult, Frame } from "./types";

interface State {
  connected: boolean;
  telemetry: TelemetryFrame | null;
  health: HealthFrame | null;
  cmdLog: CmdResult[];
  pushCmd: (r: CmdResult) => void;
}

export const useStore = create<State>((set) => ({
  connected: false,
  telemetry: null,
  health: null,
  cmdLog: [],
  pushCmd: (r) => set((s) => ({ cmdLog: [r, ...s.cmdLog].slice(0, 60) })),
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
