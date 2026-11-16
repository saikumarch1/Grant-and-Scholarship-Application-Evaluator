"""Grant and Scholarship Application Evaluator backend. REST for actions and data, Server Sent Events for live agent activity.

Run with: uvicorn server:app --reload     then open http://localhost:8000
"""
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scholardesk import ScholarDesk, config, rubric
from scholardesk.config import ROOT

app = FastAPI(title="Grant and Scholarship Application Evaluator", version="1.0")
desk = ScholarDesk()


class ApplicationIn(BaseModel):
    text: str = Field(min_length=20, max_length=20000)


class ConsistencyIn(BaseModel):
    app_a: str
    app_b: str
    inject_error: bool = False


@app.get("/api/health")
async def health():
    cfg = config.settings()
    ok, entries, _ = desk.log.verify()
    return {"llm_configured": config.configured(), "model": cfg["model"], "custom_endpoint": bool(cfg["base_url"]),
            "log_entries": entries, "log_chain_valid": ok}


@app.get("/api/rubric")
async def get_rubric():
    return rubric.lines()


@app.get("/api/samples")
async def samples():
    return json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))


@app.post("/api/applications", status_code=202)
async def submit(body: ApplicationIn):
    try:
        state = desk.submit(body.text)
    except RuntimeError as err:
        raise HTTPException(503, str(err))
    return {"app_id": state.app_id}


@app.get("/api/applications")
async def applications():
    rows = [s.snapshot() for s in desk.apps.values()]
    brief = [{k: r[k] for k in ("app_id", "applicant", "status", "total", "merit", "need", "created")}
             | {"decision": (r["decision"] or {}).get("decision"), "cited": (r["decision"] or {}).get("cited", [])} for r in rows]
    return sorted(brief, key=lambda r: (r["total"] is None, -(r["total"] or 0), -r["created"]))


@app.get("/api/applications/{app_id}")
async def application(app_id: str):
    if app_id not in desk.apps:
        raise HTTPException(404, f"Unknown application {app_id}")
    return desk.apps[app_id].snapshot()


def _known(run_id: str):
    if run_id not in desk.bus.history:
        raise HTTPException(404, f"Unknown run {run_id}")


@app.get("/api/runs/{run_id}/trace")
async def trace(run_id: str):
    _known(run_id)
    return desk.bus.history[run_id]


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str):
    _known(run_id)

    async def generate():
        async for event in desk.bus.stream(run_id, idle=15):
            yield ": ping\n\n" if event is None else f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/consistency", status_code=202)
async def consistency(body: ConsistencyIn):
    try:
        return desk.submit_consistency(body.app_a, body.app_b, body.inject_error)
    except RuntimeError as err:
        raise HTTPException(503, str(err))
    except ValueError as err:
        raise HTTPException(422, str(err))


@app.get("/api/consistency/{run_id}")
async def consistency_result(run_id: str):
    if run_id not in desk.consistency:
        raise HTTPException(404, f"Unknown consistency run {run_id}")
    return desk.consistency[run_id]


@app.get("/api/log")
async def log(limit: int = 40):
    ok, entries, bad = desk.log.verify()
    return {"valid": ok, "entries": entries, "first_bad": bad, "latest": desk.log.rows()[-limit:]}


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
