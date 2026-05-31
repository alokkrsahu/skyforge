import { useEffect, useState } from "react";
import { post, get, setBridgeBase } from "../api";
import { connectWs } from "../store";

// Compose the bring-up environment + spawn/track the stack via the gateway supervisor.
const ARENAS = ["default", "walls", "windy", "frictionless", "forest", "baylands", "lawn", "aruco"];

export default function BringupPanel() {
  const [n, setN] = useState(16);
  const [arena, setArena] = useState("default");
  const [gcs, setGcs] = useState("beacon");
  const [led, setLed] = useState("gazebo");
  const [blackbox, setBlackbox] = useState("/tmp/flight.jsonl");
  const [autoabort, setAutoabort] = useState(true);

  const [procs, setProcs] = useState<Record<string, { pid: number; running: boolean }>>({});
  const opts = { gcs, led, blackbox, autoabort };
  const refresh = () => get("/api/procs").then(setProcs).catch(() => {});
  useEffect(() => { refresh(); const id = setInterval(refresh, 2000); return () => clearInterval(id); }, []);

  const env = [
    `SKYFORGE_GCS=${gcs}`, `SKYFORGE_LED_BACKEND=${led}`,
    blackbox ? `SKYFORGE_BLACKBOX=${blackbox}` : "",
    autoabort ? "SKYFORGE_AUTOABORT=1" : "", "SKYFORGE_WEB=1",
  ].filter(Boolean).join(" ");
  const cmds = `./t1_sitl.sh ${n} ${arena}\n${env} ./t6_commander.sh ${n}`;

  const spawn = async (target: string) => {
    const r = await post("/api/bringup", { target, n, arena, opts: { ...opts, web: target === "commander" } });
    if (target === "commander" && r.port) {
      // the live bridge is on its own port — point control + telemetry there
      setBridgeBase(`http://${location.hostname}:${r.port}`);
      connectWs();
    }
    refresh();
  };

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
      <h3>Launch (via the gateway supervisor)</h3>
      <div className="row">
        <button onClick={() => spawn("sitl")}>Start SITL</button>
        <button onClick={() => spawn("commander")}>Start commander+web</button>
        <button onClick={() => spawn("qgc")}>QGC</button>
        <button className="estop" onClick={() => post("/api/teardown").then(refresh)}>Teardown all</button>
      </div>
      <div className="row procs">
        {Object.entries(procs).map(([k, v]) => (
          <span key={k} className={`dot ${v.running ? "ok" : "bad"}`} title={`pid ${v.pid}`}>&nbsp;{k}</span>
        ))}
        {Object.keys(procs).length === 0 && <span className="muted">nothing running</span>}
      </div>
      <h3>Equivalent commands</h3>
      <pre className="stdout">{cmds}</pre>
      <small>Supervisor lives on the gateway (<code>uvicorn backend.app:app</code>); the commander+web
      bridge serves live control on its own port. Then go to <b>Fly</b>.</small>
    </div>
  );
}
