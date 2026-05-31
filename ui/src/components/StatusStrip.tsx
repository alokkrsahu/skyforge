import { useStore } from "../store";

export default function StatusStrip() {
  const t = useStore((s) => s.telemetry);
  const connected = useStore((s) => s.connected);
  const led = t?.led ?? [0, 0.8, 0];
  const swatch = `rgb(${led.map((c) => Math.round(c * 255)).join(",")})`;
  return (
    <div className="strip">
      <span className={`dot ${connected ? "ok" : "bad"}`} title={connected ? "connected" : "disconnected"} />
      <b className={t?.airborne ? "airborne" : "grounded"}>{t?.airborne ? "AIRBORNE" : "GROUNDED"}</b>
      {t && <span>ready {t.ready[0]}/{t.ready[1]}</span>}
      {t?.transition && <span className="warn">transition {t.transition.remaining_s.toFixed(1)}s</span>}
      {t && <span>alt {t.alt_m.toFixed(1)} m</span>}
      {t && <span>drones {t.drones.length}</span>}
      <span className="led">LED <i style={{ background: swatch }} /></span>
      {t?.abort && <span className="estop-flag">ABORT</span>}
    </div>
  );
}
