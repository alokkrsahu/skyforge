import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { connectGateway } from "./store";
import "./styles.css";

// Open the always-on gateway lifecycle socket (process/log/readiness/bring-up) on load. The
// telemetry bridge socket is attached lazily once a commander is actually up — opening it
// here (against the gateway, which has no telemetry /ws) was the 400-flood bug.
connectGateway();
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
