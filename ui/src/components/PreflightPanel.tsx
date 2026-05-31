import { useState } from "react";
import { post } from "../api";
import { useStore } from "../store";

export default function PreflightPanel() {
  const { compiledShow, setArmed, setView } = useStore();
  const [show, setShow] = useState(compiledShow ?? "shows/four_drone_demo.skyforge.json");
  const [endurance, setEndurance] = useState(600);
  const [verdict, setVerdict] = useState<string | null>(null);
  const [out, setOut] = useState("");

  const run = async () => {
    const r = await post("/api/preflight", { show, endurance });
    setVerdict(r.verdict);
    setOut(r.stdout);
    setArmed(r.verdict === "GO");        // the arm gate → unlocks Fly
  };

  return (
    <div className="panel preflight">
      <h2>Preflight — go / no-go</h2>
      <div className="row">
        <label>Show</label>
        <input className="spec" value={show} onChange={(e) => setShow(e.target.value)} />
      </div>
      <div className="row">
        endurance <input type="number" value={endurance} step={30} onChange={(e) => setEndurance(+e.target.value)} /> s
        <button onClick={run}>Run preflight</button>
        <button onClick={() => post("/api/export", { show, all: true })}>Export slices</button>
      </div>
      {verdict && (
        <div className={`verdict ${verdict === "GO" ? "go" : "nogo"}`}>
          {verdict}
          {verdict === "GO" && <button className="proceed" onClick={() => setView("mission")}>Proceed → Mission Control</button>}
        </div>
      )}
      {out && <pre className="stdout">{out}</pre>}
    </div>
  );
}
