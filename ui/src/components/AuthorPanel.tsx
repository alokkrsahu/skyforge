import { useEffect, useState } from "react";
import { post, get } from "../api";
import { useStore } from "../store";
import PreviewCanvas from "./PreviewCanvas";

export default function AuthorPanel() {
  const setCompiledShow = useStore((s) => s.setCompiledShow);
  const [script, setScript] = useState("shows/four_drone_demo.py");
  const [minSep, setMinSep] = useState(1.5);
  const [margin, setMargin] = useState(0.0);
  const [out, setOut] = useState("");
  const [catalog, setCatalog] = useState<string[]>([]);
  const [spec, setSpec] = useState("circle");
  const [preview, setPreview] = useState<number[][]>([]);

  useEffect(() => { get("/api/formations").then((r) => setCatalog(r.formations ?? [])); }, []);
  useEffect(() => {
    // 33 matches the designed art point-counts (cat 31 / swastika 33 / om 34) so the preview shows
    // the full sculpture — every feature dot — instead of subsampling 33→24 and dropping an
    // isolated point (which silently hid the swastika's bottom-right quadrant dot at n=24).
    post("/api/formations/preview", { spec, n: 33 }).then((r) => setPreview(r.ok ? r.points : []));
  }, [spec]);

  const compile = async () => {
    const r = await post("/api/compile", { script, min_sep: minSep, tracking_margin: margin });
    setOut(r.stdout);
    if (r.exit === 0) {
      // Use the actual path the compiler wrote (printed as "  <...>.skyforge.json"),
      // not a client-side guess — honours --output and the server's path resolution.
      const m = (r.stdout as string).match(/(\S+\.skyforge\.json)/);
      if (m) setCompiledShow(m[1]);
    }
  };

  return (
    <div className="panel author">
      <h2>Author / Compile</h2>
      <div className="row">
        <label>Show script</label>
        <input className="spec" value={script} onChange={(e) => setScript(e.target.value)} />
      </div>
      <div className="row">
        min-sep <input type="number" value={minSep} step={0.1} onChange={(e) => setMinSep(+e.target.value)} /> m
        tracking-margin <input type="number" value={margin} step={0.1} onChange={(e) => setMargin(+e.target.value)} /> m
        <button onClick={compile}>Compile</button>
      </div>
      {out && <pre className="stdout">{out}</pre>}

      <h3>Formation catalog</h3>
      <div className="row chips">
        {catalog.map((c) => (
          <button key={c} className={spec === c ? "active" : ""} onClick={() => setSpec(c)}>{c}</button>
        ))}
      </div>
      <div className="row">
        <input className="spec" value={spec} onChange={(e) => setSpec(e.target.value)}
               placeholder="circle | circle:radius_m=8 | text:HELLO:scale=3" />
      </div>
      {preview.length > 0 && <PreviewCanvas points={preview} />}
    </div>
  );
}
