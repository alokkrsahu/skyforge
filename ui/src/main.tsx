import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// NOTE: we no longer open a WebSocket at module load. The telemetry bridge lives on the
// spawned commander's own port — connecting here (against the gateway, which has no
// telemetry /ws) produced a perpetual 400 reconnect flood. Bring-up connects it instead.
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
