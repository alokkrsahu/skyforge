import { useState } from "react";
import { postCmd, acquireCommand, releaseCommand, hasCommand } from "../api";
import { useStore } from "../store";

const COLORS = ["red", "green", "blue", "white", "off", "orange", "purple", "cyan", "yellow", "pink"];
const PATTERNS = ["circle", "grid", "star", "v", "line", "spiral", "diamond", "arrow", "cat"];

export default function CommandDeck() {
  const airborne = useStore((s) => s.telemetry?.airborne ?? false);
  const [alt, setAlt] = useState(5);
  const [spec, setSpec] = useState("circle");
  const [trans, setTrans] = useState(6);
  const [dist, setDist] = useState(5);
  const [cmd, setCmd] = useState(hasCommand());

  const g = (extra = "") => (airborne ? "" : `disabled${extra}`);

  return (
    <div className="deck">
      <div className="row">
        <button onClick={async () => { cmd ? await releaseCommand() : await acquireCommand(); setCmd(hasCommand()); }}>
          {cmd ? "Release command" : "Take command"}
        </button>
        <span className="hint">{cmd ? "you hold command authority" : "single-writer lock (optional)"}</span>
      </div>
      <h3>Flight</h3>
      <div className="row">
        <button onClick={() => postCmd("takeoff", { altitude_m: alt })}>Takeoff</button>
        <input type="number" value={alt} min={1} step={1} onChange={(e) => setAlt(+e.target.value)} /> m
        <button onClick={() => postCmd("hover")}>Hover / Hold</button>
      </div>

      <h3>Formation {!airborne && <em className="hint">(airborne only)</em>}</h3>
      <div className="row">
        <input className="spec" value={spec} onChange={(e) => setSpec(e.target.value)}
               placeholder="circle | circle:radius_m=8 | text:HELLO" />
        <button className={g()} disabled={!airborne} onClick={() => postCmd("formation", { spec, transition_s: trans })}>Apply</button>
      </div>
      <div className="row chips">
        {PATTERNS.map((p) => (
          <button key={p} className={g()} disabled={!airborne} onClick={() => { setSpec(p); postCmd("formation", { spec: p, transition_s: trans }); }}>{p}</button>
        ))}
      </div>
      <div className="row">
        transition <input type="range" min={1} max={20} value={trans} onChange={(e) => setTrans(+e.target.value)} /> {trans}s
      </div>

      <h3>Move {!airborne && <em className="hint">(airborne only)</em>}</h3>
      <div className="row pad">
        <button className={g()} disabled={!airborne} onClick={() => postCmd("move", { dN: dist, dE: 0 })}>N</button>
        <button className={g()} disabled={!airborne} onClick={() => postCmd("move", { dN: -dist, dE: 0 })}>S</button>
        <button className={g()} disabled={!airborne} onClick={() => postCmd("move", { dN: 0, dE: dist })}>E</button>
        <button className={g()} disabled={!airborne} onClick={() => postCmd("move", { dN: 0, dE: -dist })}>W</button>
        <input type="number" value={dist} min={1} step={1} onChange={(e) => setDist(+e.target.value)} /> m
      </div>

      <h3>Altitude &amp; Colour</h3>
      <div className="row">
        <input type="number" defaultValue={alt} min={1} step={1} id="altset" /> m
        <button onClick={() => postCmd("altitude", { alt_m: +(document.getElementById("altset") as HTMLInputElement).value })}>Set alt</button>
      </div>
      <div className="row chips">
        {COLORS.map((c) => (
          <button key={c} className="swatch" style={{ background: c === "off" ? "#333" : c }}
                  onClick={() => postCmd("color", { name: c })} title={c} />
        ))}
      </div>
    </div>
  );
}
