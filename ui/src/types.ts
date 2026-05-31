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

export type Frame = TelemetryFrame | HealthFrame | (CmdResult & { type: "cmd_result" });
