"""Shared application state. Every agent reads and writes this one object."""
import threading
import time
from dataclasses import dataclass, field

from . import rubric


@dataclass
class ApplicationState:
    app_id: str
    text: str
    record: dict = None
    checks: dict = field(default_factory=dict)       # rule line id to check result
    eligibility: dict = None
    scores: dict = field(default_factory=dict)       # criterion line id to scored row
    missing: dict = field(default_factory=dict)      # criterion line id to the missing field
    final: dict = None
    decision: dict = None
    notes: dict = field(default_factory=dict)        # agent name to its closing message
    agents: dict = field(default_factory=dict, repr=False)
    status: str = "received"
    error: str = None
    created: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def applicant(self) -> str:
        return (self.record or {}).get("name") or "Unnamed applicant"

    def pending(self) -> list:
        unknown = (self.eligibility or {}).get("unknown", [])
        return sorted(set(unknown) | set(self.missing))

    def view(self) -> dict:
        """Plain data copy used by the Consistency Agent."""
        with self.lock:
            return {"app_id": self.app_id, "applicant": self.applicant, "record": dict(self.record or {}),
                    "scores": {k: dict(v) for k, v in self.scores.items()}, "missing": dict(self.missing),
                    "decision": dict(self.decision or {}), "status": self.status}

    def snapshot(self) -> dict:
        with self.lock:
            calc = rubric.weighted(self.scores, self.missing)
            rows = []
            for line_id in rubric.line_ids("merit") + rubric.line_ids("need"):
                line = rubric.get_line(line_id)
                row = self.scores.get(line_id)
                rows.append({"line_id": line_id, "section": line["section"], "criterion": line["criterion"], "weight": line["weight"],
                             "rule_text": line["text"], "missing": self.missing.get(line_id),
                             **({k: row[k] for k in ("score", "evidence", "reasoning", "agent", "components") if k in row} if row else {}),
                             "points": calc["rows"].get(line_id)})
            checks = [{**c, "criterion": rubric.get_line(i)["criterion"], "rule_text": rubric.get_line(i)["text"]}
                      for i, c in sorted(self.checks.items())]
            ineligible = bool((self.eligibility or {}).get("failed"))
            return {"app_id": self.app_id, "applicant": self.applicant, "status": self.status, "error": self.error,
                    "created": self.created, "record": self.record, "eligibility": self.eligibility, "checks": checks,
                    "rows": rows, "merit": None if ineligible else calc["merit"], "need": None if ineligible else calc["need"],
                    "total": None if ineligible else calc["total"], "weights": calc["weights"], "thresholds": calc["thresholds"],
                    "decision": self.decision, "notes": self.notes, "text": self.text}
