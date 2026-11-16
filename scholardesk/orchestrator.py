"""ScholarDesk coordinator: fan out / fan in rubric scoring with real LLM agents.

Flow
  1. The Intake Agent structures the raw application into the shared state.
  2. Eligibility, Merit and Need agents fan out and work concurrently against the rubric.
  3. The Panel Manager fans the results in, can question workers, and decides.
  4. The Consistency Agent can audit any two scored applications.
Every step is emitted as an event, so the browser sees the agents working live.
"""
import asyncio
import copy
import uuid

from . import config, llm
from .agents.consistency import ConsistencyAgent
from .agents.eligibility import EligibilityAgent
from .agents.intake import IntakeAgent
from .agents.merit import MeritAgent
from .agents.need import NeedAgent
from .agents.panel import PanelManager
from .events import EventBus
from .runlog import RunLog
from .state import ApplicationState


class ScholarDesk:
    def __init__(self, log: RunLog = None):
        self.log = log or RunLog()
        self.bus = EventBus(self.log)
        self.apps: dict = {}
        self.consistency: dict = {}
        self.tasks: set = set()

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def submit(self, text: str) -> ApplicationState:
        if not config.configured() and llm.client_factory is None:
            raise RuntimeError("No API key configured. Set LLM_API_KEY in the .env file.")
        state = ApplicationState(app_id="APP-" + uuid.uuid4().hex[:6].upper(), text=text.strip())
        self.apps[state.app_id] = state
        self.bus.emit(state.app_id, "run_start", "Coordinator", kind="evaluation")
        self._spawn(self.evaluate(state))
        return state

    async def evaluate(self, state: ApplicationState):
        bus, run = self.bus, state.app_id
        try:
            service = llm.make_service()
            state.status = "intake"
            await IntakeAgent(state, bus, service).run("Here is the raw application:\n\n" + state.text)
            workers = [EligibilityAgent(state, bus, service), MeritAgent(state, bus, service), NeedAgent(state, bus, service)]
            state.agents = {w.name: w for w in workers}
            state.status = "scoring"
            bus.emit(run, "fan_out", "Coordinator", agents=[w.name for w in workers])
            await asyncio.gather(*(w.run("Do your part of the evaluation of this applicant using your tools.") for w in workers))
            bus.emit(run, "fan_in", "Coordinator", message="All workers have submitted. The Panel Manager takes over.")
            state.status = "deciding"
            await PanelManager(state, bus, service).run("Review the board and make the final decision for this application.")
            state.status = "closed"
            snap = state.snapshot()
            bus.emit(run, "decision", "Panel Manager", decision=state.decision["decision"], total=snap["total"], cited=state.decision["cited"])
            bus.emit(run, "run_end", "Coordinator", app_id=run)
        except Exception as err:
            state.status, state.error = "failed", str(err)[:400]
            bus.emit(run, "run_error", "Coordinator", error=state.error)

    def submit_consistency(self, app_a: str, app_b: str, inject_error: bool = False) -> dict:
        if not config.configured() and llm.client_factory is None:
            raise RuntimeError("No API key configured. Set LLM_API_KEY in the .env file.")
        views = {}
        for app_id in (app_a, app_b):
            state = self.apps.get(app_id)
            if not state or state.decision is None or state.snapshot()["total"] is None:
                raise ValueError(f"{app_id} has no weighted score, so there is nothing to audit.")
            views[app_id] = state.view()
        if app_a == app_b:
            raise ValueError("Choose two different applications.")
        if inject_error:
            row = views[app_b]["scores"]["M1"]
            row["score"] = row["score"] - 15 if row["score"] >= 15 else row["score"] + 15
        run = "CON-" + uuid.uuid4().hex[:6].upper()
        self.consistency[run] = {"run_id": run, "app_a": app_a, "app_b": app_b, "inject_error": inject_error, "status": "running", "result": None}
        self.bus.emit(run, "run_start", "Coordinator", kind="consistency", applications=list(views))
        self._spawn(self._audit(run, views))
        return self.consistency[run]

    async def _audit(self, run: str, views: dict):
        job = self.consistency[run]
        try:
            agent = ConsistencyAgent(views, self.bus, llm.make_service(), run)
            a, b = list(views)
            await agent.run(f"Audit applications {a} and {b}. Application {a} is {views[a]['applicant']} (decision {views[a]['decision'].get('decision')}). "
                            f"Application {b} is {views[b]['applicant']} (decision {views[b]['decision'].get('decision')}).")
            job.update(status="done", result=agent.result)
            self.bus.emit(run, "run_end", "Coordinator", consistent=agent.result["consistent"])
        except Exception as err:
            job.update(status="failed", result={"error": str(err)[:400]})
            self.bus.emit(run, "run_error", "Coordinator", error=str(err)[:400])

    async def wait_idle(self):
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
