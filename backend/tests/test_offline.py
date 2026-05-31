"""
Offline-plane tests: the gateway wraps cli.py handlers + the formation catalog with no
MAVSDK / no live runtime. Uses the real compiler over the shipped demo show.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.offline import register_offline

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEMO = os.path.join(REPO, "shows", "four_drone_demo.py")


def _client():
    app = FastAPI()
    register_offline(app)
    return TestClient(app)


def test_formations_catalog():
    cat = _client().get("/api/formations").json()["formations"]
    assert "circle" in cat and "cat" in cat and "text" in cat


def test_formation_preview_points():
    r = _client().post("/api/formations/preview", json={"spec": "circle", "n": 8}).json()
    assert r["ok"] and len(r["points"]) == 8 and len(r["points"][0]) == 3   # (dN,dE,dU)


def test_formation_preview_bad_spec():
    r = _client().post("/api/formations/preview", json={"spec": "hexagon", "n": 8}).json()
    assert r["ok"] is False and "Unknown formation" in r["error"]


def test_compile_then_preflight_go(tmp_path):
    c = _client()
    rc = c.post("/api/compile", json={"script": DEMO, "output": str(tmp_path)}).json()
    assert rc["exit"] == 0 and "Written" in rc["stdout"]
    show = str(tmp_path / "four_drone_demo.skyforge.json")
    pf = c.post("/api/preflight", json={"show": show}).json()
    assert pf["exit"] == 0 and pf["verdict"] == "GO"


def test_preflight_nogo_over_budget(tmp_path):
    c = _client()
    c.post("/api/compile", json={"script": DEMO, "output": str(tmp_path)})
    show = str(tmp_path / "four_drone_demo.skyforge.json")
    pf = c.post("/api/preflight", json={"show": show, "endurance": 1.0}).json()   # 1 s endurance
    assert pf["verdict"] == "NO-GO" and pf["exit"] == 1


def test_export_all_slices(tmp_path):
    c = _client()
    c.post("/api/compile", json={"script": DEMO, "output": str(tmp_path)})
    show = str(tmp_path / "four_drone_demo.skyforge.json")
    r = c.post("/api/export", json={"show": show, "all": True, "output": str(tmp_path)}).json()
    assert r["exit"] == 0 and (tmp_path / "four_drone_demo.drone000.skyforge.json").exists()


def test_flightlog_summary(tmp_path):
    import json
    log = tmp_path / "bb.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"t": 0.0, "n_lost": 0, "max_pos_error_m": 0.4, "min_battery_frac": 0.9},
        {"t": 3.0, "n_lost": 1, "max_pos_error_m": 1.6, "min_battery_frac": 0.6},
    ]) + "\n")
    r = _client().post("/api/flightlog", json={"log": str(log)}).json()
    assert r["exit"] == 0 and "Records" in r["stdout"]


def test_info(tmp_path):
    c = _client()
    c.post("/api/compile", json={"script": DEMO, "output": str(tmp_path)})
    show = str(tmp_path / "four_drone_demo.skyforge.json")
    r = c.post("/api/info", json={"show": show}).json()
    assert r["exit"] == 0 and "Drones" in r["stdout"]
