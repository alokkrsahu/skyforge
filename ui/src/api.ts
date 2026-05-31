import type { CmdResult } from "./types";

// Single-writer command authority: when held, the token is sent with every control call.
let cmdToken: string | null = null;
export function hasCommand(): boolean { return cmdToken !== null; }
export async function acquireCommand(): Promise<void> {
  cmdToken = (await post("/api/command/acquire")).token ?? null;
}
export async function releaseCommand(): Promise<void> {
  await post("/api/command/release"); cmdToken = null;
}

// REST control — each call returns the verb's tri-state result {ok, guard, status, verb}.
export async function postCmd(verb: string, body: Record<string, unknown> = {}): Promise<CmdResult> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (cmdToken) headers["x-command-token"] = cmdToken;
  const r = await fetch(`/api/cmd/${verb}`, { method: "POST", headers, body: JSON.stringify(body) });
  return (await r.json()) as CmdResult;
}

export async function killSession(): Promise<void> {
  await fetch("/api/session/kill", { method: "POST" });
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
