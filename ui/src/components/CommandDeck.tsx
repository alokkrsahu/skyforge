import { useEffect, useState } from "react";
import { postCmd, acquireCommand, releaseCommand, hasCommand, get } from "../api";
import { useStore } from "../store";

const COLORS = ["red", "green", "blue", "white", "off", "orange", "purple", "cyan", "yellow", "pink"];

export default function CommandDeck() {
  const airborne = useStore((s) => s.telemetry?.airborne ?? false);
  const live = useStore((s) => s.bridgeConnected);          // a commander bridge is connected
  const takeoffAt = useStore((s) => s.takeoffAt);
  const noteTakeoff = useStore((s) => s.noteTakeoff);
  const ready = useStore((s) => s.telemetry?.ready);
  // Takeoff stalled? (issued, not airborne after ~22 s) — usually a mavsdk_server crash under
  // SITL load; re-evaluated on each 10 Hz telemetry frame. Beats a silently-stuck GROUNDED.
  const stalled = takeoffAt !== null && !airborne && Date.now() - takeoffAt > 22000;
  // Live catalog from the backend (compiler.formations.list_formations) — not hardcoded.
  const [patterns, setPatterns] = useState<string[]>([]);
  useEffect(() => {
    get("/api/formations")
      .then((r) => setPatterns((r.formations ?? []).filter((p: string) => p !== "text")))
      .catch(() => {});
  }, []);
  const [alt, setAlt] = useState(5);
  const [spec, setSpec] = useState("circle");
  const [trans, setTrans] = useState(6);
  const [dist, setDist] = useState(5);
  const [altSet, setAltSet] = useState(8);
  const [cmd, setCmd] = useState(hasCommand());

  const flyable = airborne && live;                          // airborne-gated verbs need a live link too
  const g = () => (flyable ? "" : "disabled");

  return (
    <div className="deck">
      {!live && <div className="banner warn">No live commander — launch the stack in Mission Control to fly.</div>}
      {stalled && (
        <div className="banner danger">
          Takeoff stalled — {ready?.[0] ?? 0}/{ready?.[1] ?? "?"} drones ready after 22 s. A mavsdk_server
          likely crashed under SITL load (too many drones for this machine). Check the Mission Control
          console, then E‑STOP and relaunch with fewer drones.
        </div>
      )}
      <div className="row">
        <button disabled={!live} onClick={async () => { cmd ? await releaseCommand() : await acquireCommand(); setCmd(hasCommand()); }}>
          {cmd ? "Release command" : "Take command"}
        </button>
        <span className="hint">{cmd ? "you hold command authority" : "single-writer lock (optional)"}</span>
      </div>
      <h3>Flight</h3>
      <div className="row">
        <button disabled={!live} onClick={() => { noteTakeoff(); postCmd("takeoff", { altitude_m: alt }); }}>Takeoff</button>
        <input type="number" value={alt} min={1} step={1} onChange={(e) => setAlt(+e.target.value)} /> m
        <button disabled={!live} onClick={() => postCmd("hover")}>Hover / Hold</button>
      </div>

      <h3>Formation {!flyable && <em className="hint">(airborne only)</em>}</h3>
      <div className="row">
        <input className="spec" value={spec} onChange={(e) => setSpec(e.target.value)}
               placeholder="circle | circle:radius_m=8 | text:HELLO" />
        <button className={g()} disabled={!flyable} onClick={() => postCmd("formation", { spec, transition_s: trans })}>Apply</button>
      </div>
      <div className="row chips">
        {patterns.map((p) => (
          <button key={p} className={g()} disabled={!flyable} onClick={() => { setSpec(p); postCmd("formation", { spec: p, transition_s: trans }); }}>{p}</button>
        ))}
      </div>
      <div className="row">
        transition <input type="range" min={1} max={20} value={trans} onChange={(e) => setTrans(+e.target.value)} /> {trans}s
      </div>

      <h3>Move {!flyable && <em className="hint">(airborne only)</em>}</h3>
      <div className="row pad">
        <button className={g()} disabled={!flyable} onClick={() => postCmd("move", { dN: dist, dE: 0 })}>N</button>
        <button className={g()} disabled={!flyable} onClick={() => postCmd("move", { dN: -dist, dE: 0 })}>S</button>
        <button className={g()} disabled={!flyable} onClick={() => postCmd("move", { dN: 0, dE: dist })}>E</button>
        <button className={g()} disabled={!flyable} onClick={() => postCmd("move", { dN: 0, dE: -dist })}>W</button>
        <input type="number" value={dist} min={1} step={1} onChange={(e) => setDist(+e.target.value)} /> m
      </div>

      <h3>Altitude &amp; Colour</h3>
      <div className="row">
        <input type="number" value={altSet} min={1} step={1} onChange={(e) => setAltSet(+e.target.value)} /> m
        <button disabled={!live} onClick={() => postCmd("altitude", { alt_m: altSet })}>Set alt</button>
      </div>
      <div className="row chips">
        {COLORS.map((c) => (
          <button key={c} className="swatch" disabled={!live} style={{ background: c === "off" ? "#333" : c }}
                  onClick={() => postCmd("color", { name: c })} title={c} />
        ))}
      </div>
    </div>
  );
}
