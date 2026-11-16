"""Panel Manager. The coordinator: reads the shared board, can question workers, then decides.

Tools
  read_board           reads the record, eligibility result and every worker's scores and reasoning
  search_rubric        retrieval over the weights (W1) and decision threshold (D1) lines
  compute_final_score  weighted arithmetic done in code, plus the decision the rubric requires
  ask_worker           sends a follow up question to a worker agent and returns its reply
  submit_decision      records award, waitlist or reject, which must agree with the rubric
"""
import json
from typing import Annotated

from .. import rubric
from .base import LLMAgent

WORKERS = ("Eligibility Agent", "Merit Agent", "Need Agent")


class PanelManager(LLMAgent):
    name = "Panel Manager"
    plugin = "panel"
    instructions = (
        "You coordinate the other agents and make the final decision. Call read_board to see their work, search_rubric to read the "
        "weights (W1) and decision thresholds (D1), and compute_final_score for the arithmetic, which you must never do yourself. "
        "If a worker's reasoning looks weak or inconsistent, use ask_worker to question it, at most twice. Then call submit_decision. "
        "A failed hard eligibility rule means reject. Data that is unverified or missing means waitlist, held for information. "
        "Otherwise follow the D1 thresholds. Cite the rubric line ids behind the decision and explain it for the applicant file."
    )

    def tools(self):
        s = self.state
        asked = []

        def read_board() -> str:
            """Read the shared board: record, eligibility result, and each worker's scores with reasoning."""
            snap = s.snapshot()
            return json.dumps({"applicant": snap["applicant"], "eligibility": s.eligibility,
                               "scores": [{k: r.get(k) for k in ("line_id", "criterion", "score", "evidence", "reasoning", "missing", "agent")}
                                          for r in snap["rows"]], "worker_notes": s.notes})

        def search_rubric(query: Annotated[str, "What to look up, such as weights or decision thresholds"]) -> str:
            """Retrieve the weights and decision threshold lines of the published rubric."""
            return json.dumps(rubric.retrieve(query, ["weights", "decision"], k=2))

        def compute_final_score() -> str:
            """Compute section scores and the weighted final score in code, and state the decision the rubric requires."""
            calc = rubric.weighted(s.scores, s.missing)
            elig = s.eligibility or {}
            required, because = rubric.expected_decision(elig.get("status"), elig.get("failed", []), s.pending(), calc["total"], calc["thresholds"])
            s.final = {"merit": calc["merit"], "need": calc["need"], "total": calc["total"], "complete": calc["complete"],
                       "required_decision": required, "because": because}
            return json.dumps({**s.final, "pending": s.pending(), "weights": calc["weights"], "thresholds": calc["thresholds"]})

        async def ask_worker(
            agent_name: Annotated[str, "Eligibility Agent, Merit Agent or Need Agent"],
            question: Annotated[str, "A specific question about its work"],
        ) -> str:
            """Ask a worker agent a follow up question about its scores and get its reply."""
            if agent_name not in WORKERS or agent_name not in s.agents:
                return f"REJECTED: choose one of {', '.join(WORKERS)}"
            if len(asked) >= 2:
                return "REJECTED: you have already asked two follow up questions, decide now"
            asked.append(agent_name)
            self.emit("agent_message", direction="to", peer=agent_name, text=question)
            reply = await s.agents[agent_name].chat(f"[FOLLOW-UP from the Panel Manager] {question}")
            self.emit("agent_message", direction="from", peer=agent_name, text=reply)
            return reply

        def submit_decision(
            decision: Annotated[str, "award, waitlist or reject"],
            cited_lines: Annotated[str, "Comma separated rubric line ids behind the decision, such as M1,N1,D1"],
            reasoning: Annotated[str, "Plain explanation for the applicant file"],
        ) -> str:
            """Record the final decision. It must agree with the decision the rubric requires."""
            if not s.final:
                return "REJECTED: call compute_final_score first"
            decision = decision.strip().lower()
            if decision != s.final["required_decision"]:
                return f"REJECTED: the rubric requires '{s.final['required_decision']}' because {s.final['because']}"
            cited = sorted({c.strip().upper() for c in cited_lines.replace(" ", ",").split(",") if c.strip()})
            valid = {l["id"] for l in rubric.lines()}
            if not cited or any(c not in valid for c in cited):
                return f"REJECTED: cite valid rubric line ids from {', '.join(sorted(valid))}"
            failed = (s.eligibility or {}).get("failed", [])
            if failed and not set(failed) <= set(cited):
                return f"REJECTED: cite the failed rules {', '.join(failed)}"
            with s.lock:
                s.decision = {"decision": decision, "cited": cited, "reasoning": reasoning, "basis": s.final["because"]}
                s.status = "decided"
            return json.dumps({"stored": True, "decision": decision})

        return [read_board, search_rubric, compute_final_score, ask_worker, submit_decision]

    def incomplete(self):
        return [] if self.state.decision else ["submit_decision has not been called"]
