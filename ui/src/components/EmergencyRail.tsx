import { useEffect } from "react";
import { postCmd } from "../api";

// 3-level escalation. E-STOP/abort is always callable (no guard, no I/O) and bound to a
// global hotkey (Shift+Esc). NOTE: these are commanded landings, not a motor kill.
export default function EmergencyRail() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.shiftKey && e.key === "Escape") { e.preventDefault(); postCmd("abort"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="emergency">
      <h3>Emergency</h3>
      <button className="land" onClick={() => postCmd("land", { stagger: true })}>LAND (staggered)</button>
      <button className="land" onClick={() => postCmd("land", { stagger: false })}>LAND now</button>
      <button className="rtl" onClick={() => postCmd("rtl", { transition_s: 8 })}>RTL → land</button>
      <button className="estop" onClick={() => postCmd("abort")}>E-STOP / ABORT</button>
      <small>Shift+Esc = E-STOP. Commanded land, not a motor kill.</small>
    </div>
  );
}
