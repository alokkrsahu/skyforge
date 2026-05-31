import { create } from "zustand";
import { bridgeWsUrl, gatewayWsUrl, setBridgeBase } from "./api";
import type {
  TelemetryFrame, HealthFrame, CmdResult, BridgeFrame,
  GatewayFrame, ProcInfo,
} from "./types";

export type View = "mission" | "author" | "preflight" | "fly" | "monitor" | "review";

export interface LogLine { target: string; line: string; t: number; level?: string; }

interface State {
  // ── gateway lifecycle socket (always-on, same-origin) ──
  gatewayConnected: boolean;
  procs: Record<string, ProcInfo>;
  logs: LogLine[];                       // combined ring (cap 2000)
  sitlReady: { n: number; of: number } | null;
  bringupPhase: string | null;           // latest lifecycle phase
  bringupMsg: string | null;
  commanderPort: number | null;          // arrives via the `bringup` frame / a manual spawn
  logFilter: string;                     // "all" | a target
  logVerbose: boolean;                   // show known-benign noise (off → clean console)

  // ── bridge telemetry socket (only once a commander is up) ──
  bridgeConnected: boolean;
  telemetry: TelemetryFrame | null;
  health: HealthFrame | null;
  cmdLog: CmdResult[];

  // ── view / gates ──
  view: View;
  armed: boolean;                        // preflight GO
  compiledShow: string | null;

  pushCmd: (r: CmdResult) => void;
  setView: (v: View) => void;
  setArmed: (a: boolean) => void;
  setCompiledShow: (p: string | null) => void;
  setLogFilter: (t: string) => void;
  setLogVerbose: (v: boolean) => void;
  clearLogs: () => void;
}

export const useStore = create<State>((set) => ({
  gatewayConnected: false,
  procs: {},
  logs: [],
  sitlReady: null,
  bringupPhase: null,
  bringupMsg: null,
  commanderPort: null,
  logFilter: "all",
  logVerbose: false,
  bridgeConnected: false,
  telemetry: null,
  health: null,
  cmdLog: [],
  view: "mission",
  armed: false,
  compiledShow: null,
  pushCmd: (r) => set((s) => ({ cmdLog: [r, ...s.cmdLog].slice(0, 60) })),
  setView: (view) => set({ view }),
  setArmed: (armed) => set({ armed }),
  setCompiledShow: (compiledShow) => set({ compiledShow }),
  setLogFilter: (logFilter) => set({ logFilter }),
  setLogVerbose: (logVerbose) => set({ logVerbose }),
  clearLogs: () => set({ logs: [] }),
}));

// ── Gateway lifecycle socket ──────────────────────────────────────────────────
// Always-on, same-origin. Carries process/log/readiness/lifecycle/bringup frames. The
// gateway is always up, so a closed socket is a real outage worth retrying (capped backoff).
// This is the socket main.tsx opens on load — never the bridge (that was the 400-flood bug).
let gatewaySock: WebSocket | null = null;
let gwDelay = 1000;

export function connectGateway(): void {
  if (gatewaySock) return;                                 // single gateway socket
  const open = () => {
    const ws = new WebSocket(gatewayWsUrl());
    gatewaySock = ws;
    ws.onopen = () => { gwDelay = 1000; useStore.setState({ gatewayConnected: true }); };
    ws.onclose = () => {
      useStore.setState({ gatewayConnected: false });
      if (gatewaySock === ws) { gatewaySock = null; setTimeout(open, gwDelay); gwDelay = Math.min(gwDelay * 2, 8000); }
    };
    ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
    ws.onmessage = (ev) => handleGateway(JSON.parse(ev.data) as GatewayFrame);
  };
  open();
}

function handleGateway(f: GatewayFrame): void {
  switch (f.type) {
    case "proc":
      useStore.setState({ procs: f.procs });
      // commander gone → drop the live socket so it doesn't spin reconnecting to a dead port
      if (useStore.getState().commanderPort !== null) {
        const c = f.procs.commander;
        if (c && c.running === false) disconnectBridge();
      }
      break;
    case "log":
      useStore.setState((s) => ({ logs: [...s.logs, { target: f.target, line: f.line, t: f.t, level: f.level }].slice(-2000) }));
      break;
    case "ready":
      useStore.setState({ sitlReady: { n: f.n, of: f.of } });
      break;
    case "lifecycle":
      useStore.setState({ bringupPhase: f.phase, bringupMsg: f.msg });
      break;
    case "bringup":
      connectBridge(f.port);                                // commander is up — attach the live feed
      break;
  }
}

// ── Bridge telemetry socket (lazy) ──────────────────────────────────────────────
// Opened ONLY once a commander bridge port is known, and reconnects ONLY while a port is
// still set — so it never spins against a non-existent socket (before bring-up / after teardown).
let bridgeSock: WebSocket | null = null;
let brDelay = 1000;

export function connectBridge(port: number): void {
  setBridgeBase(`http://${location.hostname}:${port}`);
  useStore.setState({ commanderPort: port });
  if (bridgeSock) { try { bridgeSock.close(); } catch { /* ignore */ } bridgeSock = null; }
  brDelay = 1000;
  const open = () => {
    const ws = new WebSocket(bridgeWsUrl());
    bridgeSock = ws;
    ws.onopen = () => { brDelay = 1000; useStore.setState({ bridgeConnected: true }); };
    ws.onclose = () => {
      useStore.setState({ bridgeConnected: false });
      if (bridgeSock === ws && useStore.getState().commanderPort !== null) {
        bridgeSock = null; setTimeout(open, brDelay); brDelay = Math.min(brDelay * 2, 8000);
      }
    };
    ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
    ws.onmessage = (ev) => handleBridge(JSON.parse(ev.data) as BridgeFrame);
  };
  open();
}

export function disconnectBridge(): void {
  useStore.setState({ commanderPort: null, bridgeConnected: false, telemetry: null, health: null });
  if (bridgeSock) { try { bridgeSock.close(); } catch { /* ignore */ } bridgeSock = null; }
}

function handleBridge(f: BridgeFrame): void {
  if (f.type === "telemetry") useStore.setState({ telemetry: f });
  else if (f.type === "health") useStore.setState({ health: f });
  else if (f.type === "cmd_result") useStore.getState().pushCmd(f as CmdResult);
}
