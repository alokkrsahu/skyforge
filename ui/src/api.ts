import type { CmdResult } from "./types";

// REST control — each call returns the verb's tri-state result {ok, guard, status, verb}.
export async function postCmd(verb: string, body: Record<string, unknown> = {}): Promise<CmdResult> {
  const r = await fetch(`/api/cmd/${verb}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
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
