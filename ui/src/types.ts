// WebSocket frame shapes (mirror backend/control.py) + REST control result.

export interface DroneState {
  id: number;
  pos: [number, number, number] | null;     // global NED (N, E, D); D down, up = -D
  vel: [number, number, number] | null;
  target: [number, number, number];          // ghost target (peek_target)
  stale: boolean;
}

export interface TelemetryFrame {
  type: "telemetry";
  t: number;
  airborne: boolean;
  abort: boolean;
  alt_m: number;
  led: [number, number, number];
  center: [number, number];
  flight_cycle: number;
  ready: [number, number];
  transition: { remaining_s: number; duration_s: number } | null;
  drones: DroneState[];
}

export interface HealthFrame {
  type: "health";
  n_total: number;
  n_seen: number;
  n_lost: number;
  min_battery_frac: number | null;
  max_pos_error_m: number | null;
  anomalies: string[];
}

export interface CmdResult {
  type?: "cmd_result";
  ok: boolean;
  guard: boolean;
  status: string;
  verb: string;
}

// ── Bridge socket (the spawned commander's own port): telemetry / health / cmd_result ──
export type BridgeFrame = TelemetryFrame | HealthFrame | (CmdResult & { type: "cmd_result" });
export type Frame = BridgeFrame;                         // back-compat alias

// ── Gateway socket (:8787, always on): process / log / readiness / lifecycle ──────────
export type ProcState = "idle" | "starting" | "ready" | "running" | "exited" | "failed";

export interface ProcInfo {
  state: ProcState | string;
  pid: number | null;
  running: boolean;
  code: number | null;
  ready_n: number;
  ready_of: number;
}

export interface ProcFrame      { type: "proc"; procs: Record<string, ProcInfo>; }
export interface LogFrame       { type: "log"; target: string; line: string; t: number; }
export interface ReadyFrame     { type: "ready"; target: string; n: number; of: number; }
export interface LifecycleFrame { type: "lifecycle"; phase: string; msg: string; t: number; }
export interface BringupFrame   { type: "bringup"; target: string; port: number; pid: number | null; }

export type GatewayFrame = ProcFrame | LogFrame | ReadyFrame | LifecycleFrame | BringupFrame;
