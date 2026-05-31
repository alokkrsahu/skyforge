import { useEffect, useRef, useState } from "react";
import { launch, bringup, teardown, stopTarget, type LaunchOpts } from "../api";
import { useStore, connectBridge, disconnectBridge } from "../store";

const ARENAS = ["default", "walls", "windy", "frictionless", "forest", "baylands", "lawn", "aruco"];

// Per-process launch targets. `needsShow` ones (player/agents) run a compiled show; `display`
// ones need a desktop (skipped by the one-click launch, but startable on their own).
const TARGETS: { key: string; label: string; desc: string; needsShow?: boolean; display?: boolean }[] = [
  { key: "sitl",      label: "SITL",          desc: "PX4 ×N + Gazebo physics" },
  { key: "commander", label: "Commander+web", desc: "live control + telemetry bridge" },
  { key: "player",    label: "Show player",   desc: "fly a compiled show", needsShow: true },
  { key: "agents",    label: "On-board agents", desc: "upload-and-go", needsShow: true },
  { key: "gui",       label: "Gazebo GUI",    desc: "3D viewer (needs display)", display: true },
  { key: "qgc",       label: "QGroundControl", desc: "ground station (needs display)", display: true },
];

type StepState = "idle" | "active" | "done" | "fail";
function Stepper({ steps }: { steps: { label: string; detail?: string; state: StepState }[] }) {
  return (
    <div className="stepper">
      {steps.map((s, i) => (
        <div key={s.label} className={`step step--${s.state}`}>
          <span className="step-dot">{s.state === "done" ? "✓" : s.state === "fail" ? "✕" : i + 1}</span>
          <span className="step-label">{s.label}{s.detail ? <em> {s.detail}</em> : null}</span>
        </div>
      ))}
    </div>
  );
}

export default function MissionControl() {
  const procs        = useStore((s) => s.procs);
  const sitlReady    = useStore((s) => s.sitlReady);
  const phase        = useStore((s) => s.bringupPhase);
  const phaseMsg     = useStore((s) => s.bringupMsg);
  const commanderPort = useStore((s) => s.commanderPort);
  const bridgeConnected = useStore((s) => s.bridgeConnected);
  const gatewayConnected = useStore((s) => s.gatewayConnected);
  const compiledShow = useStore((s) => s.compiledShow);

  const [n, setN] = useState(4);
  const [arena, setArena] = useState("default");
  const [gcs, setGcs] = useState("beacon");
  const [led, setLed] = useState("gazebo");
  const [blackbox, setBlackbox] = useState("/tmp/flight.jsonl");
  const [autoabort, setAutoabort] = useState(true);
  const [mode, setMode] = useState<"background" | "terminal">("background");
  const opts: LaunchOpts = { gcs, led, blackbox, autoabort };

  const sitlUp     = !!sitlReady && sitlReady.of > 0 && sitlReady.n >= sitlReady.of;
  const commanderUp = commanderPort !== null;
  const failed     = phase === "failed" || phase === "timeout";
  const launching  = (phase === "sitl_starting" || phase === "commander_starting") && !commanderUp && !failed;

  const steps: { label: string; detail?: string; state: StepState }[] = [
    { label: "SITL", detail: sitlReady ? `${sitlReady.n}/${sitlReady.of}` : "",
      state: sitlUp ? "done" : phase === "sitl_starting" ? "active" : (failed && !sitlUp ? "fail" : "idle") },
    { label: "Commander",
      state: commanderUp ? "done" : phase === "commander_starting" ? "active" : (failed && sitlUp ? "fail" : "idle") },
    { label: "Telemetry",
      state: bridgeConnected ? "done" : commanderUp ? "active" : "idle" },
  ];

  const onLaunch   = () => launch({ n, arena, opts, mode });
  const onTeardown = async () => { await teardown(); disconnectBridge(); };

  const startTarget = async (target: string, needsShow?: boolean) => {
    const body: Parameters<typeof bringup>[0] = {
      target, n, arena, mode, opts: { ...opts, web: target === "commander" },
    };
    if (needsShow) body.show = compiledShow ?? undefined;
    const r = await bringup(body);
    if (target === "commander" && r.port) connectBridge(r.port);
  };

  const stop = async (target: string) => {
    await stopTarget(target);                  // reaps just this process's tree
    if (target === "commander") disconnectBridge();
  };

  return (
    <div className="panel mission">
      <h2>Mission Control</h2>
      {!gatewayConnected && (
        <div className="banner warn">Gateway offline — run <code>uvicorn backend.app:app --port 8787</code> under the PX4 venv.</div>
      )}

      {/* ── One-click launch ── */}
      <section className="card hero">
        <div className="hero-head">
          <div>
            <h3>Launch the stack</h3>
            <p className="helper">Spawns PX4 SITL ×{n} ({arena}), waits for all instances, then starts the commander + live bridge — one click.</p>
          </div>
          {commanderUp
            ? <button className="btn-danger lg" onClick={onTeardown}>Shut down stack</button>
            : <button className="btn-primary lg" disabled={launching || !gatewayConnected} onClick={onLaunch}>
                {launching ? "Launching…" : "▶ Launch stack"}
              </button>}
        </div>
        <Stepper steps={steps} />
        {phase && <div className={`phase ${failed ? "fail" : ""}`}>{phaseMsg || phase}</div>}
        {sitlReady && !sitlUp && (
          <div className="bar"><div className="bar-fill" style={{ width: `${(100 * sitlReady.n) / Math.max(1, sitlReady.of)}%` }} />
            <span className="bar-label">SITL {sitlReady.n}/{sitlReady.of} ready</span></div>
        )}
      </section>

      {/* ── Configuration ── */}
      <section className="card">
        <h3>Configuration</h3>
        <div className="grid2">
          <label>N drones<input type="number" value={n} min={1} onChange={(e) => setN(+e.target.value)} /></label>
          <label>Arena
            <select value={arena} onChange={(e) => setArena(e.target.value)}>
              {ARENAS.map((a) => <option key={a} value={a}>{a}{a === "default" ? " · DART, 100+" : " · ODE, ≤40"}</option>)}
            </select>
          </label>
          <label>GCS<select value={gcs} onChange={(e) => setGcs(e.target.value)}>{["beacon", "qgc", "none"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>LED<select value={led} onChange={(e) => setLed(e.target.value)}>{["gazebo", "stub", "hardware"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Black-box<input value={blackbox} onChange={(e) => setBlackbox(e.target.value)} /></label>
          <label className="chk"><input type="checkbox" checked={autoabort} onChange={(e) => setAutoabort(e.target.checked)} /> auto-abort</label>
        </div>
        <div className="row mode-row">
          <span className="helper">Run processes:</span>
          <div className="toggle">
            <button className={mode === "background" ? "on" : ""} onClick={() => setMode("background")}>background + console</button>
            <button className={mode === "terminal" ? "on" : ""} onClick={() => setMode("terminal")}>open in Terminal</button>
          </div>
        </div>
      </section>

      {/* ── Per-process control ── */}
      <section className="card">
        <h3>Processes</h3>
        <div className="proc-grid">
          {TARGETS.map((t) => {
            const p = procs[t.key];
            const state = p?.state ?? "idle";
            const ready = t.key === "sitl" && p && p.ready_of > 1 ? `${p.ready_n}/${p.ready_of}` : "";
            const disabled = !gatewayConnected || (t.needsShow && !compiledShow);
            return (
              <div key={t.key} className="proc-card">
                <div className="proc-top">
                  <b>{t.label}</b>
                  <span className={`badge badge--${state}`}>{state}{ready ? ` ${ready}` : ""}</span>
                </div>
                <div className="proc-desc">{t.desc}</div>
                <div className="proc-meta">{p?.pid ? `pid ${p.pid}` : "—"}{p?.code != null ? ` · exit ${p.code}` : ""}</div>
                <div className="proc-actions">
                  <button className="btn-ghost sm" disabled={disabled}
                          title={t.needsShow && !compiledShow ? "compile a show first (Author)" : ""}
                          onClick={() => startTarget(t.key, t.needsShow)}>
                    {p?.running ? "Restart" : "Start"}{t.display ? " (display)" : ""}
                  </button>
                  <button className="btn-danger sm" disabled={!p?.running} onClick={() => stop(t.key)}>Stop</button>
                </div>
              </div>
            );
          })}
        </div>
        <p className="helper">Stopping is global (Shut down stack). Re-Start restarts a single process.</p>
      </section>

      <LogConsole />
    </div>
  );
}

function LogConsole() {
  const logs    = useStore((s) => s.logs);
  const filter  = useStore((s) => s.logFilter);
  const setFilter = useStore((s) => s.setLogFilter);
  const verbose = useStore((s) => s.logVerbose);
  const setVerbose = useStore((s) => s.setLogVerbose);
  const clear   = useStore((s) => s.clearLogs);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const targets = Array.from(new Set(logs.map((l) => l.target)));
  const byTarget = filter === "all" ? logs : logs.filter((l) => l.target === filter);
  // Hide known-benign vendor noise unless verbose; count what's hidden so it's never silent.
  const visible = verbose ? byTarget : byTarget.filter((l) => l.level !== "noise");
  const hidden = byTarget.length - visible.length;
  // Collapse consecutive identical lines into one with a ×N badge (kills repeat floods).
  const rows: { line: string; target: string; level?: string; n: number }[] = [];
  for (const l of visible.slice(-1200)) {
    const prev = rows[rows.length - 1];
    if (prev && prev.line === l.line && prev.target === l.target) prev.n++;
    else rows.push({ line: l.line, target: l.target, level: l.level, n: 1 });
  }
  const shown = rows.slice(-500);

  useEffect(() => { const el = ref.current; if (el && stick.current) el.scrollTop = el.scrollHeight; });
  const onScroll = () => { const el = ref.current; if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; };

  return (
    <section className="card console">
      <div className="console-head">
        <h3>Live output</h3>
        <div className="tabs">
          <button className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>all</button>
          {targets.map((t) => <button key={t} className={filter === t ? "on" : ""} onClick={() => setFilter(t)}>{t}</button>)}
        </div>
        <button className={`btn-ghost sm ${verbose ? "on" : ""}`} onClick={() => setVerbose(!verbose)}
                title="show benign Gazebo/transport noise">verbose{!verbose && hidden ? ` (${hidden})` : ""}</button>
        <button className="btn-ghost sm" onClick={clear}>clear</button>
      </div>
      <div className="console-body" ref={ref} onScroll={onScroll}>
        {shown.length === 0 && <div className="empty">No process output yet — launch the stack or start a process above.</div>}
        {shown.map((l, i) => (
          <div key={i} className={`logline lvl-${l.level ?? "info"}`}>
            <span className={`tag tag-${l.target}`}>{l.target}</span>{l.line}{l.n > 1 ? <span className="rep"> ×{l.n}</span> : null}
          </div>
        ))}
      </div>
    </section>
  );
}
