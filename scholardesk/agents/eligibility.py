"""Eligibility Agent. Checks the hard criteria and can reject outright with the failed rule.

Tools
  get_application     reads the structured record from the shared state
  search_rubric       retrieval over the eligibility lines of the published rubric
  check_rule          tests one hard rule against the record (pass, fail or unknown)
  submit_eligibility  records the verdict, which must agree with the rule checks
"""
import json
from typing import Annotated

from .. import rubric
from .base import LLMAgent


class EligibilityAgent(LLMAgent):
    name = "Eligibility Agent"
    plugin = "eligibility"
    instructions = (
        "Retrieve the eligibility rules with search_rubric, read the record with get_application, then call check_rule for every "
        "eligibility rule. Finally call submit_eligibility. A failed rule makes the applicant ineligible. A rule whose data is not "
        "stated is unknown, which means unverified, never a pass. Explain which rule ids drove the verdict."
    )

    def tools(self):
        s = self.state

        def get_application() -> str:
            """Read the structured application record."""
            return json.dumps(s.record)

        def search_rubric(query: Annotated[str, "What to look for in the eligibility rules"]) -> str:
            """Retrieve eligibility rules from the published rubric."""
            return json.dumps(rubric.retrieve(query, ["eligibility"], k=6))

        def check_rule(line_id: Annotated[str, "Rule id such as E1"]) -> str:
            """Test one hard eligibility rule against the record."""
            if line_id not in rubric.line_ids("eligibility"):
                return f"REJECTED: {line_id} is not an eligibility rule"
            result = rubric.check_rule(rubric.get_line(line_id), s.record)
            with s.lock:
                s.checks[line_id] = result
            return json.dumps(result)

        def submit_eligibility(
            status: Annotated[str, "eligible, ineligible or unverified"],
            reasoning: Annotated[str, "Which rule ids decided it and why"],
        ) -> str:
            """Record the eligibility verdict. It must agree with the rule checks."""
            ids = rubric.line_ids("eligibility")
            unchecked = [i for i in ids if i not in s.checks]
            if unchecked:
                return f"REJECTED: call check_rule for {', '.join(unchecked)} first"
            failed = sorted(i for i, c in s.checks.items() if c["status"] == "fail")
            unknown = sorted(i for i, c in s.checks.items() if c["status"] == "unknown")
            expected = "ineligible" if failed else "unverified" if unknown else "eligible"
            if status.strip().lower() != expected:
                return f"REJECTED: the rule checks give '{expected}' (failed {failed or 'none'}, unknown {unknown or 'none'})"
            with s.lock:
                s.eligibility = {"status": expected, "failed": failed, "unknown": unknown, "reasoning": reasoning}
            return json.dumps({"stored": True, "status": expected})

        return [get_application, search_rubric, check_rule, submit_eligibility]

    def incomplete(self):
        return [] if self.state.eligibility else ["submit_eligibility has not been called"]
