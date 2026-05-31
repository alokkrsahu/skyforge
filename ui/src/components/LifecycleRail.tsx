import { useStore, type View } from "../store";

const STEPS: { id: View; label: string }[] = [
  { id: "mission", label: "1 · Mission Control" },
  { id: "author", label: "2 · Author" },
  { id: "preflight", label: "3 · Preflight" },
  { id: "fly", label: "4 · Fly" },
  { id: "monitor", label: "5 · Monitor" },
  { id: "review", label: "6 · Review" },
];

export default function LifecycleRail() {
  const { view, armed, setView } = useStore();
  return (
    <nav className="rail">
      {STEPS.map((s) => {
        const locked = s.id === "fly" && !armed;          // arm-gate
        return (
          <button key={s.id} className={`${view === s.id ? "active" : ""} ${locked ? "locked" : ""}`}
                  disabled={locked} onClick={() => setView(s.id)}
                  title={locked ? "locked until preflight = GO" : ""}>
            {s.label}{locked ? " 🔒" : ""}
          </button>
        );
      })}
    </nav>
  );
}
