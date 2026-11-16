"""Shared behaviour of the Merit Agent and the Need Agent: retrieve a rubric line, apply it, submit a guarded score."""
import json
from typing import Annotated

from .. import rubric
from .base import LLMAgent


class SectionScorer(LLMAgent):
    section = ""

    def ids(self):
        return rubric.line_ids(self.section)

    def common_tools(self):
        s = self.state

        def get_application() -> str:
            """Read the structured application record."""
            return json.dumps(s.record)

        def search_rubric(query: Annotated[str, "The criterion you want the rubric text for"]) -> str:
            """Retrieve rubric lines for this section from the published rubric."""
            return json.dumps(rubric.retrieve(query, [self.section], k=4))

        def calculate_band_score(line_id: Annotated[str, "Criterion line id"]) -> str:
            """Compute what the rubric line gives for this applicant, to double check your own reading."""
            if line_id not in self.ids():
                return f"REJECTED: {line_id} is not a {self.section} criterion"
            score, evidence = rubric.expected_score(rubric.get_line(line_id), s.record)
            return json.dumps({"line_id": line_id, "score": score, "evidence": evidence})

        def submit_score(
            line_id: Annotated[str, "Criterion line id"],
            score: Annotated[int, "Score from 0 to 100 that the rubric line gives"],
            evidence: Annotated[str, "The applicant facts the score is based on"],
            reasoning: Annotated[str, "How the rubric line was applied"],
        ) -> str:
            """Submit the score for a band or points criterion. It is checked against the rubric line."""
            if line_id not in self.ids():
                return f"REJECTED: {line_id} is not a {self.section} criterion"
            line = rubric.get_line(line_id)
            if line["scoring"]["type"] == "statement":
                return "REJECTED: score the personal statement with submit_statement_score"
            expected, shown = rubric.expected_score(line, s.record)
            if expected is None:
                return f"REJECTED: {shown}. Call report_missing for {line_id} instead of guessing"
            if int(score) != expected:
                return f"REJECTED: the rubric says '{line['text']}' and the evidence is {shown}, which scores {expected}, not {score}"
            with s.lock:
                s.scores[line_id] = {"line_id": line_id, "score": expected, "evidence": shown, "reasoning": reasoning, "agent": self.name}
                s.missing.pop(line_id, None)
            return json.dumps({"stored": line_id, "score": expected})

        def report_missing(
            line_id: Annotated[str, "Criterion line id"],
            field: Annotated[str, "The application field that is not stated"],
        ) -> str:
            """Report that a criterion cannot be scored because the application does not state the data."""
            if line_id not in self.ids():
                return f"REJECTED: {line_id} is not a {self.section} criterion"
            score, _ = rubric.expected_score(rubric.get_line(line_id), s.record)
            if score is not None:
                return f"REJECTED: the data for {line_id} is present, score it with submit_score"
            with s.lock:
                s.missing[line_id] = field
            return json.dumps({"reported_missing": line_id})

        return [get_application, search_rubric, calculate_band_score, submit_score, report_missing]

    def tools(self):
        return self.common_tools()

    def incomplete(self):
        done = set(self.state.scores) | set(self.state.missing)
        todo = [i for i in self.ids() if i not in done]
        return [f"{i} has no score and is not reported missing" for i in todo]
