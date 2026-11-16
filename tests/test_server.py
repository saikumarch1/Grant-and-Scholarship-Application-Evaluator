"""Backend API tests, including the live event stream."""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
from conftest import make_desk  # noqa: E402
from scholardesk import llm  # noqa: E402

SAMPLES = json.loads((Path(__file__).resolve().parent.parent / "data" / "samples.json").read_text())


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test")
    server.desk = make_desk(tmp_path, delay=0.02)
    yield httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test")
    llm.client_factory = None


async def run_to_end(client, text):
    app_id = (await client.post("/api/applications", json={"text": text})).json()["app_id"]
    events = []
    async with client.stream("GET", f"/api/runs/{app_id}/events") as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return app_id, events


@pytest.mark.asyncio
async def test_submit_streams_live_events_then_serves_the_result(api):
    app_id, events = await run_to_end(api, SAMPLES[0]["text"])
    types = [e["type"] for e in events]
    assert types[0] == "run_start" and types[-1] == "run_end" and "fan_out" in types and "decision" in types
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    snap = (await api.get(f"/api/applications/{app_id}")).json()
    assert snap["decision"]["decision"] == "award" and snap["total"] == 91.08 and snap["status"] == "closed"


@pytest.mark.asyncio
async def test_stream_replays_history_for_a_late_subscriber(api):
    app_id, first = await run_to_end(api, SAMPLES[3]["text"])
    async with api.stream("GET", f"/api/runs/{app_id}/events") as stream:
        replay = [json.loads(l[6:]) async for l in stream.aiter_lines() if l.startswith("data: ")]
    assert [e["seq"] for e in replay] == [e["seq"] for e in first]


@pytest.mark.asyncio
async def test_board_is_ranked_and_trace_is_available(api):
    ids = [(await run_to_end(api, SAMPLES[i]["text"]))[0] for i in (0, 3, 4, 2)]
    board = (await api.get("/api/applications")).json()
    assert [b["applicant"] for b in board] == ["Priya Nair", "Neha Kapoor", "Sameer Khan", "Rohan Das"]
    assert board[-1]["total"] is None and board[-1]["decision"] == "reject"
    assert len((await api.get(f"/api/runs/{ids[0]}/trace")).json()) > 20


@pytest.mark.asyncio
async def test_consistency_runs_over_the_api_and_refuses_unscored(api):
    a, _ = await run_to_end(api, SAMPLES[3]["text"])
    b, _ = await run_to_end(api, SAMPLES[4]["text"])
    r, _ = await run_to_end(api, SAMPLES[2]["text"])
    job = (await api.post("/api/consistency", json={"app_a": a, "app_b": b})).json()
    async with api.stream("GET", f"/api/runs/{job['run_id']}/events") as stream:
        [l async for l in stream.aiter_lines()]
    done = (await api.get(f"/api/consistency/{job['run_id']}")).json()
    assert done["status"] == "done" and done["result"]["consistent"]
    assert (await api.post("/api/consistency", json={"app_a": a, "app_b": r})).status_code == 422


@pytest.mark.asyncio
async def test_validation_not_found_and_missing_key(api, monkeypatch):
    assert (await api.post("/api/applications", json={"text": "short"})).status_code == 422
    assert (await api.get("/api/applications/APP-NOPE")).status_code == 404
    assert (await api.get("/api/runs/NOPE/events")).status_code == 404
    health = (await api.get("/api/health")).json()
    assert health["llm_configured"] and health["log_chain_valid"]
    monkeypatch.setenv("LLM_API_KEY", "")
    llm.client_factory = None
    denied = await api.post("/api/applications", json={"text": "x" * 40})
    assert denied.status_code == 503 and "LLM_API_KEY" in denied.json()["detail"]
