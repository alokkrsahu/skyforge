import { useStore } from "../store";

// A pill with a status dot. `tone` colours the dot (ok/warn/bad/idle).
function Pill({ label, value, tone, title }: { label: string; value: string; tone: string; title?: string }) {
  return (
    <span className="pill" title={title}>
      <span className={`dot ${tone}`} /> <span className="pill-k">{label}</span> <b>{value}</b>
    </span>
  );
}

export default function StatusStrip() {
  const t            = useStore((s) => s.telemetry);
  const gateway      = useStore((s) => s.gatewayConnected);
  const sitlReady    = useStore((s) => s.sitlReady);
  const commanderPort = useStore((s) => s.commanderPort);
  const bridge       = useStore((s) => s.bridgeConnected);
  const armed        = useStore((s) => s.armed);

  const led = t?.led ?? [0, 0.8, 0];
  const swatch = `rgb(${led.map((c) => Math.round(c * 255)).join(",")})`;
  const sitlTone = !sitlReady ? "idle" : sitlReady.n >= sitlReady.of ? "ok" : "warn";

  return (
    <div className="strip">
      <Pill label="gateway" value={gateway ? "up" : "down"} tone={gateway ? "ok" : "bad"}
            title="the always-on supervisor at :8787" />
      <Pill label="SITL" value={sitlReady ? `${sitlReady.n}/${sitlReady.of}` : "—"} tone={sitlTone} />
      <Pill label="bridge" value={commanderPort ? (bridge ? "live" : "connecting") : "—"}
            tone={!commanderPort ? "idle" : bridge ? "ok" : "warn"} title="the spawned commander's live socket" />
      <Pill label="preflight" value={armed ? "GO" : "—"} tone={armed ? "ok" : "idle"} />
      <span className="sep" />
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
