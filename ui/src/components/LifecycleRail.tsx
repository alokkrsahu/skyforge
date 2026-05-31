import { useStore, type View } from "../store";

const STEPS: { id: View; label: string }[] = [
  { id: "mission", label: "Mission Control" },
  { id: "author", label: "Author" },
  { id: "preflight", label: "Preflight" },
  { id: "fly", label: "Fly" },
  { id: "monitor", label: "Monitor" },
  { id: "review", label: "Review" },
];

export default function LifecycleRail() {
  const { view, armed, setView } = useStore();
  const bridgeConnected = useStore((s) => s.bridgeConnected);
  const compiledShow    = useStore((s) => s.compiledShow);

  // A step is "done" when its work is demonstrably complete (from live state), "locked" when
  // its preconditions aren't met. Fly needs a LIVE COMMANDER — manual flight (takeoff/formation/
  // move) doesn't require a compiled/validated show; the preflight GO gate is for the show path.
  const done = (id: View): boolean =>
    id === "author" ? !!compiledShow :
    id === "preflight" ? armed :
    id === "mission" ? bridgeConnected : false;
  const flyLocked = !bridgeConnected;

  return (
    <nav className="rail">
      {STEPS.map((s, i) => {
        const locked = s.id === "fly" && flyLocked;
        const cls = [view === s.id ? "active" : "", locked ? "locked" : "", done(s.id) ? "done" : ""].join(" ").trim();
        const tip = locked ? "needs a live commander — Launch the stack in Mission Control" : "";
        return (
          <button key={s.id} className={cls} disabled={locked} onClick={() => setView(s.id)} title={tip}>
            <span className="rail-n">{done(s.id) ? "✓" : i + 1}</span>
            <span className="rail-l">{s.label}</span>
            {locked && <span className="rail-lock">🔒</span>}
          </button>
        );
      })}
    </nav>
  );
}
