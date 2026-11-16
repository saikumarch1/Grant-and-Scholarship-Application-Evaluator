"""Intake Agent. Reads a raw application in any wording and records a validated structured record.

Tools
  parse_amount   converts rupee amounts written with commas or lakh to whole rupees
  submit_record  validates and stores the structured record in the shared state
"""
import json
import re
from typing import Annotated

from .base import LLMAgent

LEVELS = ("undergraduate", "postgraduate", "diploma", "other")


def parse_amount_text(text: str):
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?)?", str(text), re.I)
    if not match or not match.group(1).replace(",", "").strip("."):
        return None
    value = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").lower()
    return int(value * (10_000_000 if unit.startswith("crore") else 100_000 if unit else 1))


def _number(raw, label, low, high):
    raw = str(raw).strip().replace(",", "").rstrip("%")
    if raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{label} must be a plain number, got '{raw}'")
    if not low <= value <= high:
        raise ValueError(f"{label} {value} is outside {low} to {high}")
    return value


class IntakeAgent(LLMAgent):
    name = "Intake Agent"
    plugin = "intake"
    instructions = (
        "The user message holds a raw scholarship application, which may be a form, a letter or messy text. Extract the facts "
        "and call submit_record exactly once with the final values. Use parse_amount for any rupee amount written with commas "
        "or lakh. Leave a field empty when the application does not state it. Never estimate a missing value. Copy the personal "
        "statement word for word. Set course_level to one of undergraduate, postgraduate, diploma or other. List achievements "
        "one per line as 'level: title' where level is national, state, institute or other."
    )

    def tools(self):
        s = self.state

        def parse_amount(text: Annotated[str, "An amount such as Rs 3,20,000 or 4.5 lakh"]) -> str:
            """Convert an Indian rupee amount written with commas or lakh into whole rupees."""
            value = parse_amount_text(text)
            return json.dumps({"rupees": value} if value is not None else {"error": "no amount found"})

        def submit_record(
            name: Annotated[str, "Applicant name"] = "",
            course_level: Annotated[str, "undergraduate, postgraduate, diploma or other"] = "",
            percentage: Annotated[str, "Latest percentage as a number, empty if not stated"] = "",
            family_income: Annotated[str, "Annual family income in whole rupees, empty if not stated"] = "",
            dependents: Annotated[str, "Number of dependents, empty if not stated"] = "",
            existing_aid_pct: Annotated[str, "Percent of fees already covered by other aid, empty if not stated"] = "",
            documents: Annotated[str, "Comma separated documents submitted"] = "",
            achievements: Annotated[str, "One per line as 'level: title'"] = "",
            statement: Annotated[str, "Personal statement copied verbatim, empty if none"] = "",
        ) -> str:
            """Validate and store the structured application record. Call once with the final values."""
            try:
                pct = _number(percentage, "percentage", 0, 100)
                income = _number(family_income, "family_income", 0, 10**9)
                deps = _number(dependents, "dependents", 0, 50)
                aid = _number(existing_aid_pct, "existing_aid_pct", 0, 100)
                level = course_level.strip().lower()
                if level and level not in LEVELS:
                    raise ValueError(f"course_level must be one of {', '.join(LEVELS)}")
                if income is not None and 0 < income < 1000:
                    raise ValueError("family_income must be in whole rupees, use parse_amount for lakh values")
                flat = " ".join(s.text.split()).lower()
                if pct is not None and f"{pct:g}" not in flat and str(int(pct)) not in flat:
                    raise ValueError(f"percentage {pct:g} does not appear in the application text")
                stmt = " ".join(statement.split())
                if stmt and stmt.lower() not in flat:
                    raise ValueError("statement must be copied word for word from the application")
                items = []
                for line in achievements.splitlines():
                    if line.strip():
                        head, sep, rest = line.partition(":")
                        tag = head.strip().lower()
                        if sep and tag not in ("national", "state", "institute", "other"):
                            raise ValueError(f"achievement level '{tag}' must be national, state, institute or other")
                        items.append({"level": tag if sep else "other", "title": (rest if sep else head).strip()})
            except ValueError as err:
                return f"REJECTED: {err}"
            docs = sorted({d.strip().lower() for d in documents.split(",") if d.strip()})
            has_income = any("income" in d for d in docs)
            has_marks = any(k in d for d in docs for k in ("mark", "grade", "transcript", "result"))
            with s.lock:
                s.record = {
                    "name": name.strip() or None, "course_level": level or None, "percentage": pct,
                    "family_income": int(income) if income is not None else None,
                    "dependents": int(deps) if deps is not None else None,
                    "existing_aid_pct": int(aid) if aid is not None else None,
                    "documents": docs, "documents_complete": has_income and has_marks, "achievements": items, "statement": stmt,
                }
            missing = [k for k in ("percentage", "family_income", "course_level", "statement") if not s.record[k]]
            return json.dumps({"stored": True, "not_stated": missing})

        return [parse_amount, submit_record]

    def incomplete(self):
        return [] if self.state.record else ["submit_record has not been called"]
