import type { CmdResult } from "./types";

// The live control/telemetry bridge may live on a different origin than the page: the
// gateway (offline plane + supervisor) serves the UI, and when it SPAWNS the commander+web
// bridge that runs on its own port. After bring-up the UI points control + WS at that base;
// empty = same-origin (the standalone commander+web case). CORS is enabled on the bridge.
let bridgeBase = "";
export function setBridgeBase(b: string): void { bridgeBase = b.replace(/\/$/, ""); }
export function getBridgeBase(): string { return bridgeBase; }
export function bridgeWsUrl(): string {
  if (bridgeBase) return bridgeBase.replace(/^http/, "ws") + "/ws";
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}
// The gateway's always-on lifecycle socket is ALWAYS same-origin (never the bridge base).
export function gatewayWsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

// ── Supervisor (gateway, same-origin): spawn/track the runtime stack from the UI ──
export interface LaunchOpts { gcs?: string; led?: string; blackbox?: string; autoabort?: boolean; [k: string]: unknown; }
export async function launch(body: { n: number; arena: string; opts: LaunchOpts; mode?: string }): Promise<any> {
  return post("/api/launch", body);            // one-click: backend sequences SITL → commander
}
export async function bringup(body: { target: string; n: number; arena: string; opts: LaunchOpts; mode?: string; show?: string }): Promise<any> {
  return post("/api/bringup", body);           // granular per-process spawn; mode: background|terminal
}
export async function teardown(): Promise<any> { return post("/api/teardown"); }

// Single-writer command authority: when held, the token is sent with every control call.
let cmdToken: string | null = null;
export function hasCommand(): boolean { return cmdToken !== null; }
export async function acquireCommand(): Promise<void> {
  cmdToken = (await post(`${bridgeBase}/api/command/acquire`)).token ?? null;
}
export async function releaseCommand(): Promise<void> {
  await post(`${bridgeBase}/api/command/release`); cmdToken = null;
}

// REST control — each call returns the verb's tri-state result {ok, guard, status, verb}.
export async function postCmd(verb: string, body: Record<string, unknown> = {}): Promise<CmdResult> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (cmdToken) headers["x-command-token"] = cmdToken;
  const r = await fetch(`${bridgeBase}/api/cmd/${verb}`, { method: "POST", headers, body: JSON.stringify(body) });
  return (await r.json()) as CmdResult;
}

export async function killSession(): Promise<void> {
  await fetch(`${bridgeBase}/api/session/kill`, { method: "POST" });
}

// Generic JSON POST/GET for the offline plane (compile/validate/preflight/formations…).
export async function post(path: string, body: Record<string, unknown> = {}): Promise<any> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}
export async function get(path: string): Promise<any> {
  return (await fetch(path)).json();
}
