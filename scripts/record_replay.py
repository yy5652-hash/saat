"""Record one full dashboard run for the static replay on GitHub Pages: every API response, keyed so app.js can
answer the same calls without a backend (see `replayApi` in app/static/app.js).

    uv run python scripts/record_replay.py        -> results/replay_day.json

The run is the dashboard's own order (as "Run the whole day" plays it): issue both plants, deliver, list, plan and
buy for the hotel, then the campus, settle, and the noon-against-21:00 attempt. State and order book are stored after
every step, keyed by the steps done so far; verify answers are stored for every plant and meter export.
"""

import json
import sys
from pathlib import Path

from fastapi.encoders import jsonable_encoder

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import server  # noqa: E402
from saat.demo import DEVICES, TAMPERS  # noqa: E402
from saat.paths import RESULTS  # noqa: E402

rec = {"note": "recorded on a local Anvil chain by scripts/record_replay.py; the same day ran on Ethereum Sepolia",
       "get": {}, "post": {}, "states": {}, "books": {}}
done: list[str] = []


def sig() -> str:
    return ",".join(sorted(done))


def key(path: str, body: dict, with_sig: bool = False) -> str:
    k = f"{path}|{json.dumps(body, sort_keys=True, separators=(',', ':'))}"
    return f"{k}|{sig()}" if with_sig else k


# The server's own route functions, called in-process, encoded the way FastAPI encodes a response.
GET = {"/api/state": server.state, "/api/series": server.series, "/api/book": server.book, "/api/evidence": server.evidence}
POST = {"/api/reset": lambda b: server.reset(), "/api/deliver": lambda b: server.deliver(), "/api/list": lambda b: server.list_surplus(),
        "/api/verify": lambda b: server.verify(server.DeviceReq(**b)), "/api/issue": lambda b: server.issue(server.DeviceReq(**b)),
        "/api/plan": lambda b: server.plan(server.MeterReq(**b)), "/api/execute": lambda b: server.execute(server.MeterReq(**b)),
        "/api/settle": lambda b: server.settle(server.MeterReq(**b)), "/api/mismatch": lambda b: server.mismatch(server.MeterReq(**b))}


def get(path: str):
    return jsonable_encoder(GET[path]())


def post(path: str, body: dict):
    return jsonable_encoder(POST[path](body))


def snap():
    rec["states"][sig()] = get("/api/state")
    rec["books"][sig()] = get("/api/book")


def step(name: str, path: str, body: dict):
    rec["post"][key(path, body)] = post(path, body)
    done.append(name)
    snap()


def plan(meter: int):
    rec["post"][key("/api/plan", {"meter": meter}, True)] = post("/api/plan", {"meter": meter})


def main():
    post("/api/reset", {})
    snap()
    rec["get"]["/api/series"] = get("/api/series")
    rec["get"]["/api/evidence"] = get("/api/evidence")
    for d in DEVICES:
        for t in TAMPERS:
            rec["post"][key("/api/verify", {"device": d, "tamper": t})] = post("/api/verify", {"device": d, "tamper": t})
    step("issue-1", "/api/issue", {"device": 1, "tamper": "none"})      # the state after the solar plant alone
    post("/api/reset", {})
    done.clear()
    step("issue-2", "/api/issue", {"device": 2, "tamper": "none"})
    step("issue-1", "/api/issue", {"device": 1, "tamper": "none"})
    step("deliver", "/api/deliver", {})
    step("list", "/api/list", {})
    plan(8)
    plan(7)                                   # the hotel's plan is the one its purchase uses
    step("execute-7", "/api/execute", {"meter": 7})
    plan(8)
    step("execute-8", "/api/execute", {"meter": 8})
    step("settle-7", "/api/settle", {"meter": 7})
    rec["post"][key("/api/mismatch", {"meter": 7})] = post("/api/mismatch", {"meter": 7})
    step("settle-8", "/api/settle", {"meter": 8})
    rec["post"][key("/api/mismatch", {"meter": 8})] = post("/api/mismatch", {"meter": 8})
    hotel = rec["post"][key("/api/settle", {"meter": 7})]
    out = RESULTS / "replay_day.json"
    out.write_text(json.dumps(rec, separators=(",", ":")))
    print(f"hotel {hotel['cfe']:.1%} · {len(rec['post'])} answers · {len(rec['states'])} states -> {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
