"""End to end tests of the LLM agents against a stand in model server."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scholardesk import RunLog, rubric  # noqa: E402
from scholardesk.agents.intake import IntakeAgent  # noqa: E402
from scholardesk.state import ApplicationState  # noqa: E402

SAMPLES = {s["label"]: s["text"] for s in json.loads((Path(__file__).resolve().parent.parent / "data" / "samples.json").read_text())}


def evaluate(desk, label):
    async def go():
        state = desk.submit(SAMPLES[label])
        await desk.wait_idle()
        return state
    return asyncio.run(go())


def events(desk, state, type_=None):
    return [e for e in desk.bus.history[state.app_id] if type_ is None or e["type"] == type_]


@pytest.mark.parametrize("label,decision,total", [
    ("Strong merit, real need", "award", 91.08), ("Solid but modest profile", "waitlist", 65.05),
    ("Borderline A", "award", 79.65), ("Borderline B", "waitlist", 73.88)])
def test_scored_applications_get_the_rubric_decision(desk, label, decision, total):
    state = evaluate(desk, label)
    snap = state.snapshot()
    assert state.status == "closed" and state.decision["decision"] == decision and snap["total"] == total
    assert {"W1", "D1"} <= set(state.decision["cited"]) or decision == "reject"


def test_hard_rule_failure_rejects_and_names_the_failed_rules(desk):
    state = evaluate(desk, "Fails score and income rules")
    assert state.decision["decision"] == "reject" and state.eligibility["failed"] == ["E1", "E2"]
    assert {"E1", "E2"} <= set(state.decision["cited"]) and state.snapshot()["total"] is None


def test_missing_document_fails_rule_e4(desk):
    state = evaluate(desk, "Income certificate missing")
    assert state.decision["decision"] == "reject" and state.eligibility["failed"] == ["E4"]


def test_missing_income_is_reported_by_agents_and_application_is_held(desk):
    state = evaluate(desk, "Income not stated")
    assert state.eligibility["unknown"] == ["E2"] and state.missing == {"N1": "family income not stated"}
    assert state.decision["decision"] == "waitlist" and state.snapshot()["total"] is None


def test_guards_force_the_agent_to_correct_a_wrong_score(make):
    desk = make(delay=0.02, mistake=True)
    state = evaluate(desk, "Strong merit, real need")
    guard = [e for e in events(desk, state, "tool_result") if e["data"]["result"].startswith("REJECTED") and e["data"]["tool"] == "submit_score"]
    assert guard and state.scores["N1"]["score"] == 80 and state.scores["M1"]["score"] == 100
    assert state.decision["decision"] == "award"


def test_three_workers_run_concurrently(desk):
    state = evaluate(desk, "Strong merit, real need")
    ev = events(desk, state)
    names = ("Eligibility Agent", "Merit Agent", "Need Agent")
    starts = [e["seq"] for e in ev if e["type"] == "agent_start" and e["agent"] in names]
    first_done = min(e["seq"] for e in ev if e["type"] == "agent_done" and e["agent"] in names)
    assert len(starts) == 3 and max(starts) < first_done


def test_panel_manager_questions_a_worker_before_deciding(desk):
    state = evaluate(desk, "Strong merit, real need")
    talk = [e for e in events(desk, state, "agent_message") if e["agent"] == "Panel Manager" and e["data"].get("peer")]
    assert [t["data"]["direction"] for t in talk] == ["to", "from"] and talk[0]["data"]["peer"] == "Merit Agent"


def test_every_agent_calls_at_least_two_tools(desk):
    state = evaluate(desk, "Strong merit, real need")
    for agent in ("Intake Agent", "Eligibility Agent", "Merit Agent", "Need Agent", "Panel Manager"):
        used = {e["data"]["tool"] for e in events(desk, state, "tool_call") if e["agent"] == agent}
        assert len(used) >= 2, agent


def test_every_score_cites_its_rubric_line_and_weights_add_up(desk):
    state = evaluate(desk, "Borderline A")
    snap = state.snapshot()
    assert [r["line_id"] for r in snap["rows"]] == ["M1", "M2", "M3", "N1", "N2", "N3"]
    assert all(r["rule_text"] and r["reasoning"] for r in snap["rows"])
    assert round(sum(r["points"] for r in snap["rows"]), 1) == round(snap["total"], 1)


def test_consistency_agent_audits_the_borderline_pair(desk):
    async def go():
        a, b = desk.submit(SAMPLES["Borderline A"]), desk.submit(SAMPLES["Borderline B"])
        await desk.wait_idle()
        job = desk.submit_consistency(a.app_id, b.app_id)
        await desk.wait_idle()
        return job
    job = asyncio.run(go())
    assert job["status"] == "done" and job["result"]["consistent"] and len(job["result"]["replays"]) == 12


def test_consistency_agent_catches_an_injected_scoring_error(desk):
    async def go():
        a, b = desk.submit(SAMPLES["Borderline A"]), desk.submit(SAMPLES["Borderline B"])
        await desk.wait_idle()
        job = desk.submit_consistency(a.app_id, b.app_id, inject_error=True)
        await desk.wait_idle()
        return job
    job = asyncio.run(go())
    assert not job["result"]["consistent"] and job["result"]["flagged_lines"] == ["M1"]


def test_consistency_refuses_applications_without_a_weighted_score(desk):
    async def go():
        a, b = desk.submit(SAMPLES["Strong merit, real need"]), desk.submit(SAMPLES["Fails score and income rules"])
        await desk.wait_idle()
        return a, b
    a, b = asyncio.run(go())
    with pytest.raises(ValueError):
        desk.submit_consistency(a.app_id, b.app_id)


def test_intake_guards_reject_invented_values(desk):
    state = ApplicationState("APP-TEST01", "Name: Test Person. I scored 70 percent. Statement: I like maths and want to study it.")
    submit = {f.__name__: f for f in IntakeAgent(state, desk.bus, None).tools()}["submit_record"]
    assert submit(percentage="99").startswith("REJECTED")
    assert submit(statement="Something the applicant never wrote").startswith("REJECTED")
    assert submit(percentage="70", family_income="3").startswith("REJECTED")
    assert submit(percentage="70", course_level="wizard").startswith("REJECTED")
    assert "stored" in submit(name="Test Person", percentage="70", course_level="undergraduate", statement="I like maths and want to study it.")


def test_no_api_key_gives_a_clear_error(tmp_path, monkeypatch):
    from scholardesk import ScholarDesk, llm
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr("scholardesk.config.os.getenv", lambda k, d=None: d if k != "LLM_API_KEY" else "")
    llm.client_factory = None
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        ScholarDesk(RunLog(tmp_path / "l.jsonl")).submit("x" * 40)


def test_run_log_is_append_only_and_tamper_evident(desk, tmp_path):
    state = evaluate(desk, "Strong merit, real need")
    before = desk.log.rows()
    evaluate(desk, "Borderline A")
    assert desk.log.rows()[: len(before)] == before and desk.log.verify()[0]
    path = tmp_path / "log.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[5]); row["detail"] = "edited"; lines[5] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")
    assert not RunLog(path).verify()[0]
