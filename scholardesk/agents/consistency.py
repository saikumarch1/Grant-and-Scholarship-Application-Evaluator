"""Consistency Agent (group of three add on). Audits two scored applications for consistent rubric use.

Tools
  read_application      reads one scored application from the board
  replay_score          re-applies the cited rubric line to the stored evidence and compares with the recorded score
  compare_applications  line by line comparison that ranks what explains the score gap
  submit_consistency    records the verdict, which must agree with the replay results
"""
import json
from types import SimpleNamespace
from typing import Annotated

from .. import rubric
from .base import LLMAgent

SCORED = rubric.line_ids("merit") + rubric.line_ids("need")


class ConsistencyAgent(LLMAgent):
    name = "Consistency Agent"
    plugin = "consistency"
    instructions = (
        "You audit two scored scholarship applications and check that the same rubric was applied to both. Read both with "
        "read_application, replay every scored criterion of both with replay_score, use compare_applications to see where scores "
        "differ and why, then call submit_consistency. If two applicants have the same evidence on a criterion they must have the same "
        "score. Explain which criteria account for any difference in outcome."
    )

    def __init__(self, views: dict, bus, service, run_id: str):
        self.views, self.replays, self.result = views, {}, None
        super().__init__(SimpleNamespace(app_id=run_id, notes={}), bus, service, run_id)

    def tools(self):
        def read_application(app_id: Annotated[str, "Application id"]) -> str:
            """Read one scored application: record, scores with evidence, and decision."""
            view = self.views.get(app_id)
            return json.dumps(view) if view else f"REJECTED: unknown application {app_id}"

        def replay_score(app_id: Annotated[str, "Application id"], line_id: Annotated[str, "Criterion line id such as M1"]) -> str:
            """Recompute one criterion from its rubric line and the stored record and compare with the recorded score."""
            view = self.views.get(app_id)
            if not view or line_id not in SCORED:
                return "REJECTED: unknown application or criterion"
            row = view["scores"].get(line_id)
            if not row:
                return f"REJECTED: {line_id} has no recorded score"
            line = rubric.get_line(line_id)
            if line["scoring"]["type"] == "statement":
                parts = row.get("components", {})
                ok = (sum(parts.values()) == row["score"] and parts.get("length") == rubric.statement_length_points(view["record"]["statement"])
                      and all(0 <= v <= rubric.STATEMENT_CAPS[k] for k, v in parts.items()))
                result = {"match": ok, "recorded": row["score"], "replayed": sum(parts.values()), "note": "judgement line: parts, caps and length rule re-checked"}
            else:
                again, _ = rubric.expected_score(line, view["record"])
                result = {"match": again == row["score"], "recorded": row["score"], "replayed": again, "note": "banded line replayed exactly"}
            self.replays[(app_id, line_id)] = result["match"]
            return json.dumps({"app_id": app_id, "line_id": line_id, **result})

        def _rows(app_a, app_b):
            a, b = self.views[app_a], self.views[app_b]
            weights = rubric.get_line("W1")["weights"]
            rows = []
            for i in SCORED:
                ra, rb = a["scores"].get(i), b["scores"].get(i)
                if not ra or not rb:
                    continue
                same = ra["evidence"] == rb["evidence"]
                gap = round((ra["score"] - rb["score"]) * rubric.get_line(i)["weight"] * weights[rubric.get_line(i)["section"]], 2)
                rows.append({"line_id": i, "evidence_a": ra["evidence"], "evidence_b": rb["evidence"], "score_a": ra["score"], "score_b": rb["score"],
                             "same_evidence": same, "same_evidence_different_score": same and ra["score"] != rb["score"], "points_gap_a_minus_b": gap})
            return sorted(rows, key=lambda r: abs(r["points_gap_a_minus_b"]), reverse=True)

        def compare_applications(app_a: Annotated[str, "First application id"], app_b: Annotated[str, "Second application id"]) -> str:
            """Compare two applications line by line and rank the criteria that explain the score gap."""
            if app_a not in self.views or app_b not in self.views:
                return "REJECTED: unknown application"
            return json.dumps(_rows(app_a, app_b))

        def submit_consistency(
            consistent: Annotated[bool, "True only if every replay matched and no equal evidence got unequal scores"],
            flagged_lines: Annotated[str, "Comma separated line ids with a problem, empty if none"],
            explanation: Annotated[str, "Plain explanation, including why the outcomes differ if they do"],
        ) -> str:
            """Record the consistency verdict. It must agree with the replay results."""
            need = [(app, i) for app in self.views for i in SCORED if i in self.views[app]["scores"]]
            todo = [f"{a}:{i}" for a, i in need if (a, i) not in self.replays]
            if todo:
                return f"REJECTED: replay_score is still needed for {', '.join(todo[:6])}"
            ids = list(self.views)
            conflicts = [r["line_id"] for r in _rows(ids[0], ids[1]) if r["same_evidence_different_score"]]
            bad = sorted({i for (a, i), ok in self.replays.items() if not ok} | set(conflicts))
            truth = not bad
            if bool(consistent) != truth:
                return f"REJECTED: the replays show {'mismatches on ' + ', '.join(bad) if bad else 'no mismatches'}, so consistent must be {truth}"
            self.result = {"consistent": truth, "flagged_lines": bad, "explanation": explanation,
                           "replays": [{"app_id": a, "line_id": i, "match": ok} for (a, i), ok in sorted(self.replays.items())]}
            return json.dumps({"stored": True, "consistent": truth})

        return [read_application, replay_score, compare_applications, submit_consistency]

    def incomplete(self):
        return [] if self.result else ["submit_consistency has not been called"]
