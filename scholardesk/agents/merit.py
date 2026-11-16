"""Merit Agent. Scores the academic, achievement and personal statement criteria.

Tools
  get_application, search_rubric, calculate_band_score, submit_score, report_missing  (shared scorer tools)
  submit_statement_score  records the model's judgement of the statement, bounded by the rubric caps
"""
import json
from typing import Annotated

from .. import rubric
from .scorer import SectionScorer


class MeritAgent(SectionScorer):
    name = "Merit Agent"
    plugin = "merit"
    section = "merit"
    instructions = (
        "Score every merit criterion. Use search_rubric to read the rubric text for each criterion, then get_application. For the "
        "band and points criteria apply the rubric text and call submit_score, optionally confirming with calculate_band_score. "
        "For the personal statement read it yourself and judge four parts using the rubric text: goals clarity, specific "
        "evidence, length and community impact. Then call submit_statement_score. If a criterion needs data the application "
        "does not state, call report_missing for it."
    )

    def tools(self):
        s = self.state

        def submit_statement_score(
            goals: Annotated[int, "0 to 30: how clear and concrete the applicant's goals are"],
            evidence: Annotated[int, "0 to 30: specific verifiable evidence such as results, numbers or projects"],
            length: Annotated[int, "0 to 20 by the rubric length rule, based on the word count"],
            community: Annotated[int, "0 to 20: demonstrated impact on or commitment to community"],
            reasoning: Annotated[str, "Brief justification quoting what in the statement earned each part"],
        ) -> str:
            """Submit the judged personal statement score (criterion M3). Length must follow the rubric rule."""
            text = (s.record or {}).get("statement") or ""
            if not text:
                return "REJECTED: there is no statement. Call report_missing for M3"
            parts = {"goals": goals, "evidence": evidence, "length": length, "community": community}
            for key, value in parts.items():
                if not 0 <= int(value) <= rubric.STATEMENT_CAPS[key]:
                    return f"REJECTED: {key} must be between 0 and {rubric.STATEMENT_CAPS[key]}"
            if int(length) != rubric.statement_length_points(text):
                return f"REJECTED: the statement has {len(text.split())} words, which earns {rubric.statement_length_points(text)} length points under M3"
            total = sum(int(v) for v in parts.values())
            with s.lock:
                s.scores["M3"] = {"line_id": "M3", "score": total, "evidence": f"{len(text.split())} words", "agent": self.name,
                                  "reasoning": reasoning, "components": {k: int(v) for k, v in parts.items()}}
                s.missing.pop("M3", None)
            return json.dumps({"stored": "M3", "score": total})

        return self.common_tools() + [submit_statement_score]
