import { useState } from "react";
import { post } from "../api";

// Post-flight review — summarize a black-box JSONL recording (skyforge flightlog).
export default function ReviewPanel() {
  const [log, setLog] = useState("/tmp/flight.jsonl");
  const [out, setOut] = useState("");

  const run = async () => {
    const r = await post("/api/flightlog", { log });
    setOut(r.stdout || "(empty)");
  };

  return (
    <div className="panel review">
      <h2>Post-flight review</h2>
      <div className="row">
        <label>Black-box log</label>
        <input className="spec" value={log} onChange={(e) => setLog(e.target.value)} />
        <button onClick={run}>Summarize</button>
      </div>
      {out && <pre className="stdout">{out}</pre>}
      <small>Set <code>SKYFORGE_BLACKBOX</code> at bring-up to record. (3D timeline replay → a later phase.)</small>
    </div>
  );
}
