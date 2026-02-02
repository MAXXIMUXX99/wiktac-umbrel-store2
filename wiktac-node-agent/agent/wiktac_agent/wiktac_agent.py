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
DOCKER_PROXY_URL = os.getenv("DOCKER_PROXY_URL", "http://dockerproxy:2375")

app = FastAPI(title="WIKTAC Node Agent", version="0.1.1")

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

async def docker_list() -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{DOCKER_PROXY_URL}/containers/json?all=1")
        r.raise_for_status()
        return r.json()

async def docker_restart(container_id: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{DOCKER_PROXY_URL}/containers/{container_id}/restart")
        if r.status_code not in (204, 304):
            raise RuntimeError(f"restart failed: {r.status_code} {r.text}")

def classify(containers: List[Dict[str, Any]]) -> Dict[str, Any]:
    def pick(keys: List[str]) -> Optional[Dict[str, Any]]:
        for cc in containers:
            name = ((cc.get("Names") or [""])[0]).lower()
            img = (cc.get("Image") or "").lower()
            if any(k in name for k in keys) or any(k in img for k in keys):
                return cc
        return None
    return {
        "btc": pick(["bitcoin", "bitcoind", "bitcoin-node"]),
        "bch": pick(["bch", "bitcoincash", "bitcoin-cash", "bchn"]),
        "dgb": pick(["digibyte", "dgb"]),
        "miningcore": pick(["miningcore"]),
    }

def push(state: Dict[str, Any], key: str, item: Dict[str, Any]) -> None:
    state.setdefault(key, [])
    state[key].append(item)
    state[key] = state[key][-200:]

async def tick() -> None:
    state = load_state()
    try:
        containers = await docker_list()
        state["intel"]["containers"] = [
            {"id": x.get("Id"), "names": x.get("Names"), "image": x.get("Image"), "state": x.get("State"), "status": x.get("Status")}
            for x in containers
        ]
        roles = classify(containers)
        state["intel"]["roles"] = {k: {"id": (v or {}).get("Id"), "name": ((v or {}).get("Names") or [None])[0]} for k, v in roles.items()}
        if ARMED_MODE:
            for k in ("btc","bch","dgb","miningcore"):
                v = roles.get(k)
                if not v:
                    continue
                if (v.get("State") or "").lower() in ("exited","dead"):
                    await docker_restart(v["Id"])
                    push(state, "actions", {"ts": int(time.time()), "kind":"restart", "role": k})
        state["last_run"] = int(time.time())
    except Exception as e:
        push(state, "alerts", {"ts": int(time.time()), "level":"error", "msg": str(e)})
    save_state(state)

async def loop():
    await asyncio.sleep(3)
    while True:
        await tick()
        await asyncio.sleep(TICK_SECONDS)

@app.on_event("startup")
async def startup():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.create_task(loop())

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse("<h1 style='font-family:system-ui'>WIKTAC Node Agent</h1><p>Open <a href=/api/state>/api/state</a></p>")

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
    return {"ok": True}
