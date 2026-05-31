import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";

// Small read-only 3D preview of a formation's (dN, dE, dU) offsets — volumetric when dU>0.
export default function PreviewCanvas({ points }: { points: number[][] }) {
  return (
    <div className="preview">
      <Canvas camera={{ position: [14, 14, 14], fov: 50 }}>
        <ambientLight intensity={0.7} />
        <directionalLight position={[10, 20, 10]} intensity={0.7} />
        {points.map((p, i) => (
          <mesh key={i} position={[p[1], p[2], -p[0]]}>{/* x=E, y=U, z=-N */}
            <sphereGeometry args={[0.4, 12, 12]} />
            <meshStandardMaterial color="#3aa0ff" emissive="#1a5" emissiveIntensity={0.3} />
          </mesh>
        ))}
        <OrbitControls makeDefault autoRotate autoRotateSpeed={1.5} />
      </Canvas>
    </div>
  );
}
