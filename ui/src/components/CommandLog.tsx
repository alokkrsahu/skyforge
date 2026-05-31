import { useStore } from "../store";

export default function CommandLog() {
  const log = useStore((s) => s.cmdLog);
  return (
    <div className="log">
      <h3>Command log</h3>
      <ul>
        {log.map((r, i) => (
          <li key={i} className={!r.ok ? "err" : r.guard ? "guard" : "ok"}>
            <b>{r.verb}</b> {r.status}
          </li>
        ))}
        {log.length === 0 && <li className="muted">no commands yet</li>}
      </ul>
    </div>
  );
}
