import { Canvas } from "@react-three/fiber";
import { OrbitControls, Grid } from "@react-three/drei";
import { useStore } from "../store";
import type { DroneState } from "../types";

// NED → three.js (Y-up): x = East, y = up = -Down, z = -North.
function toScene(p: [number, number, number]): [number, number, number] {
  return [p[1], -p[2], -p[0]];
}

function Drone({ d, led }: { d: DroneState; led: [number, number, number] }) {
  const color = d.stale ? "#e23" : `rgb(${led.map((c) => Math.round(c * 255)).join(",")})`;
  return (
    <group>
      {d.pos && (
        <mesh position={toScene(d.pos)}>
          <sphereGeometry args={[0.6, 16, 16]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.6} />
        </mesh>
      )}
      {/* ghost target (where the drone is commanded to be) */}
      <mesh position={toScene(d.target)}>
        <sphereGeometry args={[0.7, 8, 8]} />
        <meshBasicMaterial color="#39f" wireframe transparent opacity={0.35} />
      </mesh>
    </group>
  );
}

export default function Viewport() {
  const telemetry = useStore((s) => s.telemetry);
  const commanderPort = useStore((s) => s.commanderPort);
  const led = telemetry?.led ?? [0, 0.8, 0];
  return (
    <div className="viewport">
      {/* frameloop="demand": render only when telemetry updates (~10 Hz) or the camera
          moves, not at the display's 60 Hz — ~6× less GPU work for the same picture. */}
      <Canvas frameloop="demand" camera={{ position: [25, 25, 25], fov: 50 }}>
        <ambientLight intensity={0.6} />
        <directionalLight position={[20, 40, 10]} intensity={0.8} />
        <Grid args={[120, 120]} cellSize={2} sectionSize={10} infiniteGrid fadeDistance={120}
              cellColor="#2a3550" sectionColor="#3a4a70" position={[0, 0, 0]} />
        {telemetry?.drones.map((d) => <Drone key={d.id} d={d} led={led} />)}
        <OrbitControls makeDefault />
      </Canvas>
      {!telemetry && (
        <div className="viewport-overlay">
          {commanderPort == null ? "Bring up the system in Mission Control" : "Waiting for telemetry…"}
        </div>
      )}
    </div>
  );
}
