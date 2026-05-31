import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the SPA and proxies /api + /ws to the in-loop bridge (run_commander
// with SKYFORGE_WEB=1 on :8787). Prod: `npm run build` → dist/, served by the backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8787", changeOrigin: true },
      "/ws":  { target: "ws://127.0.0.1:8787", ws: true },
    },
  },
  build: { outDir: "dist" },
});
