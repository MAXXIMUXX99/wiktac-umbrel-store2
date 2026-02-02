from __future__ import annotations

import os, time, json, asyncio
from typing import Any, Dict, List, Optional
from pathlib import Path
import httpx, yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

DATA_DIR = Path("/data")
ALLOWLIST_PATH = DATA_DIR / "allowed-payouts.yml"
STATE_PATH = DATA_DIR / "state.json"

TICK_SECONDS = int(os.getenv("TICK_SECONDS", "30"))
ARMED_MODE = os.getenv("ARMED_MODE", "false").lower() == "true"
FAILSAFE_STOP_MINING = os.getenv("FAILSAFE_STOP_MINING", "true").lower() == "true"
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "http://dockerproxy:2375")

app = FastAPI(title="WIKTAC Node Agent", version="0.1.2")

class Allowlist(BaseModel):
    btc: Dict[str, List[str]] = Field(default_factory=lambda: {"allowed_addresses": []})
    bch: Dict[str, List[str]] = Field(default_factory=lambda: {"allowed_addresses": []})
    dgb: Dict[str, List[str]] = Field(default_factory=lambda: {"allowed_addresses": []})

def load_allowlist() -> Allowlist:
    if not ALLOWLIST_PATH.exists():
        return Allowlist()
    try:
        data = yaml.safe_load(ALLOWLIST_PATH.read_text("utf-8")) or {}
        return Allowlist.model_validate(data)
    except Exception:
        return Allowlist()

def save_allowlist(a: Allowlist) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ALLOWLIST_PATH.write_text(yaml.safe_dump(a.model_dump(), sort_keys=False), encoding="utf-8")

def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"last_run": None, "intel": {}, "actions": [], "alerts": []}
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except Exception:
        return {"last_run": None, "intel": {}, "actions": [], "alerts": []}

def save_state(state: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

async def docker_list_containers() -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{DOCKER_PROXY_URL}/containers/json?all=1")
        r.raise_for_status()
        return r.json()

async def docker_restart(container_id: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{DOCKER_PROXY_URL}/containers/{container_id}/restart")
        if r.status_code not in (204, 304):
            raise RuntimeError(f"Restart failed: {r.status_code} {r.text}")

def classify(containers: List[Dict[str, Any]]) -> Dict[str, Any]:
    def pick(keys: List[str]) -> Optional[Dict[str, Any]]:
        for c in containers:
            name = (c.get("Names") or [""])[0].lower()
            img = (c.get("Image") or "").lower()
            if any(k in name for k in keys) or any(k in img for k in keys):
                return c
        return None
    return {
        "btc": pick(["bitcoin", "bitcoind", "bitcoin-node"]),
        "bch": pick(["bch", "bitcoincash", "bitcoin-cash", "bchn"]),
        "dgb": pick(["digibyte", "dgb"]),
        "miningcore": pick(["miningcore"]),
    }

def action_log(state: Dict[str, Any], kind: str, details: Dict[str, Any]) -> None:
    state.setdefault("actions", [])
    state["actions"].append({"ts": int(time.time()), "kind": kind, "details": details})
    state["actions"] = state["actions"][-200:]

def alert(state: Dict[str, Any], level: str, msg: str, meta: Optional[Dict[str, Any]] = None) -> None:
    state.setdefault("alerts", [])
    state["alerts"].append({"ts": int(time.time()), "level": level, "msg": msg, "meta": meta or {}})
    state["alerts"] = state["alerts"][-200:]

def has_allowlist(a: Allowlist) -> bool:
    return bool(a.btc.get("allowed_addresses") or a.bch.get("allowed_addresses") or a.dgb.get("allowed_addresses"))

async def agent_tick() -> None:
    state = load_state()
    a = load_allowlist()
    try:
        containers = await docker_list_containers()
        state["intel"]["containers"] = [
            {"id": c.get("Id"), "names": c.get("Names"), "image": c.get("Image"), "state": c.get("State"), "status": c.get("Status")}
            for c in containers
        ]
        roles = classify(containers)
        state["intel"]["roles"] = {k: {"id": (v or {}).get("Id"), "name": ((v or {}).get("Names") or [None])[0]} for k, v in roles.items()}
        if ARMED_MODE:
            for k in ("btc","bch","dgb","miningcore"):
                c = roles.get(k)
                if not c: 
                    continue
                st = (c.get("State") or "").lower()
                if st in ("exited","dead"):
                    if k == "miningcore" and FAILSAFE_STOP_MINING and not has_allowlist(a):
                        alert(state, "critical", "MiningCore present but allowlist not set. Failsafe posture active.", {"role": k})
                        continue
                    await docker_restart(c["Id"])
                    action_log(state, "restart", {"role": k, "id": c.get("Id")})
        state["last_run"] = int(time.time())
    except Exception as e:
        alert(state, "error", "Agent tick failed.", {"error": str(e)})
    save_state(state)

async def loop_task():
    await asyncio.sleep(2)
    while True:
        await agent_tick()
        await asyncio.sleep(TICK_SECONDS)

@app.on_event("startup")
async def startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(loop_task())

@app.get("/api/state")
def api_state():
    return load_state()

@app.get("/api/allowlist")
def api_allowlist():
    return load_allowlist().model_dump()

@app.post("/api/allowlist")
def api_set_allowlist(payload: Dict[str, Any]):
    a = Allowlist.model_validate(payload)
    save_allowlist(a)
    st = load_state()
    action_log(st, "allowlist_set", {"btc": len(a.btc.get("allowed_addresses", [])),
                                    "bch": len(a.bch.get("allowed_addresses", [])),
                                    "dgb": len(a.dgb.get("allowed_addresses", []))})
    save_state(st)
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse("<h1 style='font-family:system-ui'>WIKTAC Node Agent v0.1.2</h1><p>Status: <a href='/api/state'>/api/state</a></p>")
