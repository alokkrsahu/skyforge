import { useState } from "react";

// Phase 2: compose the bring-up environment and show the exact launch commands to run.
// (Phase 3's gateway will spawn + supervise these from the UI directly.)
const ARENAS = ["default", "walls", "windy", "frictionless", "forest", "baylands", "lawn", "aruco"];

export default function BringupPanel() {
  const [n, setN] = useState(16);
  const [arena, setArena] = useState("default");
  const [gcs, setGcs] = useState("beacon");
  const [led, setLed] = useState("gazebo");
  const [blackbox, setBlackbox] = useState("/tmp/flight.jsonl");
  const [autoabort, setAutoabort] = useState(true);

  const env = [
    `SKYFORGE_GCS=${gcs}`,
    `SKYFORGE_LED_BACKEND=${led}`,
    blackbox ? `SKYFORGE_BLACKBOX=${blackbox}` : "",
    autoabort ? "SKYFORGE_AUTOABORT=1" : "",
    "SKYFORGE_WEB=1",
  ].filter(Boolean).join(" ");
  const cmds = `./t1_sitl.sh ${n} ${arena}\n${env} ./t6_commander.sh ${n}`;

  return (
    <div className="panel bringup">
      <h2>Bring-up</h2>
      <div className="row">N drones <input type="number" value={n} min={1} onChange={(e) => setN(+e.target.value)} /></div>
      <div className="row">arena
        <select value={arena} onChange={(e) => setArena(e.target.value)}>
          {ARENAS.map((a) => <option key={a} value={a}>{a}{a === "default" ? " (DART, 100+)" : " (ODE, ≤40)"}</option>)}
        </select>
      </div>
      <div className="row">GCS
        <select value={gcs} onChange={(e) => setGcs(e.target.value)}>{["beacon", "qgc", "none"].map((x) => <option key={x}>{x}</option>)}</select>
        LED
        <select value={led} onChange={(e) => setLed(e.target.value)}>{["gazebo", "stub", "hardware"].map((x) => <option key={x}>{x}</option>)}</select>
      </div>
      <div className="row">black-box <input className="spec" value={blackbox} onChange={(e) => setBlackbox(e.target.value)} /></div>
      <div className="row"><label><input type="checkbox" checked={autoabort} onChange={(e) => setAutoabort(e.target.checked)} /> auto-abort</label></div>
      <h3>Run these (Phase 3 spawns them for you)</h3>
      <pre className="stdout">{cmds}</pre>
      <small>Then open this UI at <code>127.0.0.1:8787</code> (served by the commander+web bridge) and go to <b>Fly</b>.</small>
    </div>
  );
}
