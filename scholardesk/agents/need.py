"""Need Agent. Scores the financial need criteria.

Tools
  get_application, search_rubric, calculate_band_score, submit_score, report_missing  (shared scorer tools)
"""
from .scorer import SectionScorer


class NeedAgent(SectionScorer):
    name = "Need Agent"
    plugin = "need"
    section = "need"
    instructions = (
        "Score every financial need criterion. Use search_rubric to read the rubric text for each criterion, then get_application, "
        "apply the rubric text and call submit_score, optionally confirming with calculate_band_score. If a criterion needs data "
        "the application does not state, call report_missing for it and never assume a value."
    )
