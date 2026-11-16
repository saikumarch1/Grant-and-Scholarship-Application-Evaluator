"""The published rubric: retrieval (RAG) plus the deterministic checks that guard agent submissions."""
import json
import math
import re
from collections import Counter
from functools import lru_cache

from .config import ROOT

STATEMENT_CAPS = {"goals": 30, "evidence": 30, "length": 20, "community": 20}


@lru_cache(maxsize=1)
def lines() -> list:
    return json.loads((ROOT / "data" / "rubric.json").read_text(encoding="utf-8"))


def get_line(line_id: str) -> dict:
    return next(l for l in lines() if l["id"] == line_id)


def line_ids(section: str) -> list:
    return [l["id"] for l in lines() if l["section"] == section]


def _tokens(text: str):
    return re.findall(r"[a-z]{3,}", text.lower())


class _Index:
    def __init__(self):
        self.docs = [Counter(_tokens(f"{l['criterion']} {l['text']} {l['keywords']}")) for l in lines()]
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}

    def _vec(self, counts):
        return {w: c * self.idf.get(w, 0) for w, c in counts.items()}

    def search(self, query, sections, k):
        q = self._vec(Counter(_tokens(query)))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1
        ranked = []
        for line, doc in zip(lines(), self.docs):
            if sections and line["section"] not in sections:
                continue
            d = self._vec(doc)
            dn = math.sqrt(sum(v * v for v in d.values())) or 1
            ranked.append((sum(q[w] * d.get(w, 0) for w in q) / (qn * dn), line))
        return sorted(ranked, key=lambda r: r[0], reverse=True)[:k]


@lru_cache(maxsize=1)
def _index() -> _Index:
    return _Index()


def retrieve(query: str, sections=None, k: int = 4) -> list:
    """TF IDF retrieval over the rubric lines, optionally limited to some sections."""
    return [
        {"id": l["id"], "section": l["section"], "criterion": l["criterion"], "text": l["text"], "relevance": round(s, 3)}
        for s, l in _index().search(query, sections, k)
    ]


def inr(n) -> str:
    """Indian digit grouping, for example 320000 becomes 3,20,000."""
    digits = str(int(n))
    if len(digits) <= 3:
        return digits
    head, tail, groups = digits[:-3], digits[-3:], []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(([head] if head else []) + groups + [tail])


def evidence_text(field, value):
    return {"percentage": f"{value}%", "family_income": f"Rs {inr(value) if isinstance(value, (int, float)) else value}",
            "dependents": f"{value} dependents", "existing_aid_pct": f"{value}% of fees"}.get(field, str(value))


def expected_score(line: dict, record: dict):
    """What the rubric line gives for this record. Returns (score or None, evidence). None means data is missing."""
    spec = line.get("scoring", {})
    kind = spec.get("type")
    if kind in ("bands", "bands_desc"):
        value = record.get(spec["field"])
        if value is None:
            return None, f"{spec['field'].replace('_', ' ')} not stated"
        for threshold, score in spec["bands"]:
            if (value >= threshold) if kind == "bands" else (value <= threshold):
                return score, evidence_text(spec["field"], value)
        return spec["else"], evidence_text(spec["field"], value)
    if kind == "points":
        items = record.get("achievements") or []
        total = sum(spec["points"].get(a["level"], 0) for a in items)
        names = ", ".join(a["level"] for a in items) or "none"
        return min(total, spec["cap"]), f"{len(items)} achievements ({names})"
    return None, "judgement criterion"


def statement_length_points(text: str) -> int:
    words = len(text.split())
    return 20 if 50 <= words <= 250 else 10 if 25 <= words < 50 or 250 < words <= 400 else 0


def check_rule(line: dict, record: dict) -> dict:
    """Test one hard eligibility rule. Status is pass, fail or unknown."""
    rule = line["rule"]
    value = record.get(rule["field"])
    if value is None or value == "":
        return {"line_id": line["id"], "status": "unknown", "evidence": "not stated"}
    if rule["field"] == "documents_complete":
        shown = "complete" if value else "incomplete"
    else:
        shown = evidence_text(rule["field"], value)
    ok = {">=": value >= rule["value"], "<=": value <= rule["value"], "==": value == rule["value"]}[rule["op"]]
    return {"line_id": line["id"], "status": "pass" if ok else "fail", "evidence": shown}


def weighted(scores: dict, missing: dict) -> dict:
    """Apply criterion weights and the W1 section weights. The arithmetic never goes through a model."""
    weights = get_line("W1")["weights"]
    out = {"weights": weights, "thresholds": get_line("D1")["thresholds"], "complete": True, "rows": {}}
    for section in ("merit", "need"):
        ids = line_ids(section)
        if any(i not in scores for i in ids):
            out["complete"], out[section] = False, None
            continue
        out[section] = round(sum(scores[i]["score"] * get_line(i)["weight"] for i in ids), 2)
        for i in ids:
            out["rows"][i] = round(scores[i]["score"] * get_line(i)["weight"] * weights[section], 2)
    out["total"] = round(out["merit"] * weights["merit"] + out["need"] * weights["need"], 2) if out["complete"] else None
    return out


def expected_decision(status: str, failed: list, pending: list, total, thresholds: dict):
    """The decision the rubric requires. The Panel Manager must agree with this."""
    if status == "ineligible":
        return "reject", f"hard rule {', '.join(failed)} failed"
    if pending:
        return "waitlist", f"data missing for {', '.join(pending)}, so the application is held"
    if total >= thresholds["award"]:
        return "award", f"score {total} meets the award threshold of {thresholds['award']}"
    if total >= thresholds["waitlist"]:
        return "waitlist", f"score {total} meets the waitlist threshold of {thresholds['waitlist']}"
    return "reject", f"score {total} is below the waitlist threshold of {thresholds['waitlist']}"
