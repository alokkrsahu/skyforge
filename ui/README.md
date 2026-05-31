# SkyForge Operator UI (React + three.js)

A browser operator console for SkyForge: live 3D fleet view, command deck, and an
always-on emergency rail, talking to the in-process bridge (`backend/`) over REST + one
WebSocket. Loopback / single-operator.

## Run it

**1. Backend deps** (once, in the PX4 venv):
```bash
source ~/src/PX4-Autopilot/.venv/bin/activate
pip install -e ".[ui]"          # fastapi, uvicorn, websockets, httpx, mavsdk
```

**2. Bring up SITL + the commander with the web bridge:**
```bash
./t1_sitl.sh 16 default                       # PX4 SITL ×16 + Gazebo
SKYFORGE_WEB=1 ./t6_commander.sh 16            # commander + FastAPI bridge on :8787
```
`SKYFORGE_WEB=1` makes the web bridge **replace** the stdin REPL (same loop, no lock).

**3a. Production** — build once; the backend serves `ui/dist` at `/`:
```bash
cd ui && npm install && npm run build         # → ui/dist/
# open http://127.0.0.1:8787
```

**3b. Dev** — hot-reload frontend, proxying /api + /ws to the bridge:
```bash
cd ui && npm install && npm run dev           # http://127.0.0.1:5173 (proxies → :8787)
```

## What's here (Phase 1 MVP)
- **3D viewport** (`Viewport.tsx`) — drones in NED→scene (x=E, y=up=−D, z=−N), live LED
  tint, **ghost targets** (wireframe), stale (>2 s) = red; orbit camera + ground grid.
- **Command deck** (`CommandDeck.tsx`) — takeoff / formation (catalog chips + free `text:`
  spec + `transition_s`) / move (N/S/E/W) / altitude / colour / hover; guarded verbs
  disabled when grounded.
- **Emergency rail** (`EmergencyRail.tsx`) — LAND → RTL → E-STOP, plus **Shift+Esc** global
  E-STOP. (Commanded land, not a motor kill — see docs/ROADMAP.md.)
- **Status strip** + **command log** (tri-state from the verb's status string).

State + transport: `store.ts` (zustand + the single auto-reconnecting WebSocket), `api.ts`
(REST control). Frame/result types in `types.ts` mirror `backend/control.py`.

Later phases add: author/compile/preflight pages + arm-gate, the bring-up env form, the
health dashboard, flight-log replay, and the always-up gateway (see `docs/ROADMAP.md`).
