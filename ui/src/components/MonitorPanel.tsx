import { useStore } from "../store";

// Fleet health dashboard — driven by the 1 Hz `health` WS frames (fleet_monitor.summarize)
// plus per-drone tracking error from the 10 Hz telemetry. Battery is "unknown" until the
// battery telemetry subscription lands (a documented deferred gap).
export default function MonitorPanel() {
  const h = useStore((s) => s.health);
  const t = useStore((s) => s.telemetry);
  const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);
  return (
    <div className="panel monitor">
      <h2>Fleet monitor</h2>
      <div className="tiles">
        <div className="tile"><b>{h ? h.n_seen : "—"}/{h ? h.n_total : t?.drones.length ?? "—"}</b><span>seen</span></div>
        <div className={`tile ${h && h.n_lost ? "bad" : ""}`}><b>{h ? h.n_lost : 0}</b><span>lost</span></div>
        <div className={`tile ${h && h.max_pos_error_m != null && h.max_pos_error_m > 5 ? "bad" : ""}`}>
          <b>{h?.max_pos_error_m != null ? h.max_pos_error_m.toFixed(1) + " m" : "—"}</b><span>max track err</span></div>
        <div className="tile"><b>{pct(h?.min_battery_frac)}</b><span>min battery</span></div>
      </div>
      {h && h.anomalies.length > 0 && <div className="verdict nogo">{h.anomalies.join(" · ")}</div>}
      <h3>Per-drone</h3>
      <table className="grid">
        <thead><tr><th>id</th><th>N</th><th>E</th><th>alt</th><th>track err</th><th>state</th></tr></thead>
        <tbody>
          {(t?.drones ?? []).map((d) => {
            const err = d.pos ? Math.hypot(d.pos[0] - d.target[0], d.pos[1] - d.target[1], d.pos[2] - d.target[2]) : null;
            return (
              <tr key={d.id} className={d.stale ? "stale" : ""}>
                <td>{d.id}</td>
                <td>{d.pos ? d.pos[0].toFixed(1) : "—"}</td>
                <td>{d.pos ? d.pos[1].toFixed(1) : "—"}</td>
                <td>{d.pos ? (-d.pos[2]).toFixed(1) : "—"}</td>
                <td>{err != null ? err.toFixed(2) : "—"}</td>
                <td>{d.stale ? "STALE" : "ok"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!t && <p className="muted">no live session — bring up the fleet (commander+web).</p>}
    </div>
  );
}
