"""Saat demo server: one Antalya market day on a local chain, driven from the browser.

    uv run uvicorn app.server:app --port 8417
"""

import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from saat.demo import DEVICES, METERS, TAMPERS, Demo
from saat.paths import RESULTS

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Saat")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_lock = threading.Lock()
_demo: Demo | None = None
_done: set[str] = set()


def demo() -> Demo:
    global _demo
    if _demo is None:
        _demo = Demo()
    return _demo


class DeviceReq(BaseModel):
    device: int
    tamper: str = "none"


class MeterReq(BaseModel):
    meter: int


def _once(step: str):
    if step in _done:
        raise HTTPException(409, f"{step} already done for this market day; reset to replay")
    _done.add(step)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state():
    with _lock:
        d = demo()
        w3 = d.chain.w3
        return {
            "day": d.day,
            "chain": {"rpc": d.chain.url, "chain_id": w3.eth.chain_id, "block": w3.eth.block_number, "registry": d.saat.registry.address, "market": d.saat.market.address},
            "who": d.who,
            "devices": {k: {kk: vv for kk, vv in v.items()} for k, v in DEVICES.items()},
            "meters": {k: {kk: vv for kk, vv in v.items()} for k, v in METERS.items()},
            "tampers": TAMPERS,
            "done": sorted(_done),
            "log": d.log[-60:],
        }


@app.get("/api/series")
def series():
    with _lock:
        return demo().series()


@app.post("/api/verify")
def verify(req: DeviceReq):
    with _lock:
        return demo().verify(req.device, req.tamper)


@app.post("/api/issue")
def issue(req: DeviceReq):
    with _lock:
        _once(f"issue-{req.device}")
        return demo().issue(req.device, "none")


@app.post("/api/deliver")
def deliver():
    with _lock:
        _once("deliver")
        return demo().deliver_contracts()


@app.post("/api/list")
def list_surplus():
    with _lock:
        _once("list")
        return demo().list_surplus()


@app.get("/api/book")
def book():
    with _lock:
        return demo().order_book()


@app.post("/api/plan")
def plan(req: MeterReq):
    with _lock:
        return demo().plan(req.meter)


@app.post("/api/execute")
def execute(req: MeterReq):
    with _lock:
        _once(f"execute-{req.meter}")
        d = demo()
        if METERS[req.meter]["key"] not in d.plans:
            d.plan(req.meter)
        return d.execute(req.meter)


@app.post("/api/settle")
def settle(req: MeterReq):
    with _lock:
        _once(f"settle-{req.meter}")
        return demo().settle(req.meter)


@app.post("/api/mismatch")
def mismatch(req: MeterReq):
    with _lock:
        return demo().attempt_mismatch(req.meter)


@app.get("/api/evidence")
def evidence():
    out = {}
    for name in ("turkiye_gap", "hourly_gap", "verifier_eval", "antalya_eval"):
        p = RESULTS / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text())
    return out


@app.post("/api/reset")
def reset():
    global _demo
    with _lock:
        if _demo is not None:
            _demo.close()
        _demo = None
        _done.clear()
        demo()
        return {"ok": True}


@app.on_event("shutdown")
def _shutdown():
    if _demo is not None:
        _demo.close()
