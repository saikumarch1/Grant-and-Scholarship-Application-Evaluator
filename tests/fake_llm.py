"""A stand in model server for tests. It speaks the OpenAI chat completions protocol and plays the role of the LLM:
it reads the conversation, decides which tool to call next, and sometimes makes a mistake on purpose so the guards are exercised.
It is test infrastructure only. The real system talks to a real model through the same protocol."""
import asyncio
import json
import os
import re

from fastapi import FastAPI, Request

from scholardesk import rubric


def create_app(delay: float = None, mistake: bool = False, ask: bool = True) -> FastAPI:
    app = FastAPI()
    pause = float(os.getenv("FAKE_DELAY", "0.15")) if delay is None else delay
    counter = {"n": 0}

    def text_of(m):
        c = m.get("content")
        return c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))

    def history(messages):
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        names = {}
        out = []
        for m in messages[last_user + 1:]:
            if m["role"] == "assistant":
                for tc in m.get("tool_calls") or []:
                    names[tc["id"]] = (tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"))
            elif m["role"] == "tool":
                name, args = names.get(m["tool_call_id"], ("?", {}))
                out.append((name, args, text_of(m)))
        return text_of(messages[last_user]), out

    def called(h, name):
        return [x for x in h if x[0] == name]

    def parse_application(text):
        t = text.split("raw application:", 1)[-1]
        g = lambda pat, flags=re.I: (re.search(pat, t, flags) or [None, None])[1]
        name = (g(r"(?:Name|Applicant):\s*([A-Za-z ]+?)(?:\.|\n|$)") or "").strip()
        level = "undergraduate" if re.search(r"undergraduate|b\.tech|b\.e|b\.sc|b\.com|b\.a", t, re.I) else ""
        pct = g(r"(\d+(?:\.\d+)?)\s*(?:%|percent)")
        income = re.search(r"income[^\n]*?Rs\.?\s*([\d,]+(?:\.\d+)?\s*(?:lakhs?)?)", t, re.I)
        deps = g(r"Dependents[^:\n]*:\s*(\d+)") or g(r"(\d+)\s*members depend")
        aid = g(r"(\d+)\s*percent of my fees") or ("0" if re.search(r"aid:\s*none", t, re.I) else "")
        docs = [d for d in ("Income certificate", "Marksheet") if d.lower() in t.lower()]
        ach = [f"{m.group(1).lower()}: {m.group(2).strip()}" for m in re.finditer(r"(?:^|[-\s])(National|State|Institute):\s*([^\n]+)", t)]
        ach += [f"{m.group(1).lower()}: achievement" for m in re.finditer(r"\((national|state|institute)\)", t, re.I)]
        stmt = re.search(r"(?:Personal statement|Statement):\s*(.+)$", t, re.I | re.S)
        return dict(name=name, course_level=level, percentage=pct or "", amount_text=income.group(1) if income else "", dependents=deps or "",
                    existing_aid_pct=aid, documents=", ".join(docs), achievements="\n".join(ach), statement=stmt.group(1).strip() if stmt else "")

    def judge(statement):
        low = statement.lower()
        goals = sum(w in low for w in ["i want to", "i aim", "my goal", "i plan to", "i hope to", "aspire", "become"])
        nums = len(re.findall(r"\d+", statement))
        comm = sum(w in low for w in ["community", "village", "help", "teach", "mentor", "society", "others", "volunteer"])
        return dict(goals=30 if goals >= 2 else 15 if goals else 0, evidence=30 if nums >= 2 else 15 if nums else 0,
                    length=rubric.statement_length_points(statement), community=20 if comm >= 2 else 10 if comm else 0)

    def ok(h, name, key, value):
        return any(n == name and a.get(key) == value and not c.startswith("REJECTED") for n, a, c in h)

    def rejected(h, name, key, value):
        return any(n == name and a.get(key) == value and c.startswith("REJECTED") for n, a, c in h)

    def act(agent, user, h, messages):
        if user.startswith("[FOLLOW-UP"):
            return "Confirmed. The scores I recorded follow the rubric lines I cited and the applicant's stated evidence.", None
        if agent == "Intake Agent":
            data = parse_application(user)
            if data["amount_text"] and not called(h, "parse_amount"):
                return None, [("parse_amount", {"text": data["amount_text"]})]
            if not called(h, "submit_record"):
                income = ""
                if called(h, "parse_amount"):
                    income = str(json.loads(called(h, "parse_amount")[0][2]).get("rupees") or "")
                return None, [("submit_record", {**{k: v for k, v in data.items() if k != "amount_text"}, "family_income": income})]
            return "I extracted the structured record and stored it.", None
        if agent == "Eligibility Agent":
            if not called(h, "get_application"):
                return None, [("get_application", {}), ("search_rubric", {"query": "eligibility rules"})]
            if not called(h, "check_rule"):
                return None, [("check_rule", {"line_id": i}) for i in rubric.line_ids("eligibility")]
            if not called(h, "submit_eligibility"):
                res = [json.loads(c) for n, a, c in h if n == "check_rule"]
                failed = [r for r in res if r["status"] == "fail"]
                unknown = [r for r in res if r["status"] == "unknown"]
                status = "ineligible" if failed else "unverified" if unknown else "eligible"
                why = "Failed " + ", ".join(r["line_id"] for r in failed) if failed else "Unknown " + ", ".join(r["line_id"] for r in unknown) if unknown else "All rules pass."
                return None, [("submit_eligibility", {"status": status, "reasoning": why})]
            return "Eligibility verdict recorded from the rule checks.", None
        if agent in ("Merit Agent", "Need Agent"):
            section = "merit" if agent == "Merit Agent" else "need"
            ids = rubric.line_ids(section)
            band_ids = [i for i in ids if rubric.get_line(i)["scoring"]["type"] != "statement"]
            if not called(h, "get_application"):
                return None, [("get_application", {}), ("search_rubric", {"query": f"{section} criteria"})]
            if not called(h, "calculate_band_score"):
                return None, [("calculate_band_score", {"line_id": i}) for i in band_ids]
            record = json.loads(called(h, "get_application")[0][2])
            calls = []
            for i in ids:
                if ok(h, "submit_score", "line_id", i) or ok(h, "report_missing", "line_id", i) or ok(h, "submit_statement_score", "line_id", i):
                    continue
                if i in band_ids:
                    calc = next(json.loads(c) for n, a, c in h if n == "calculate_band_score" and a["line_id"] == i)
                    if calc["score"] is None:
                        calls.append(("report_missing", {"line_id": i, "field": calc["evidence"]}))
                    else:
                        wrong = mistake and i in ("N1", "M1") and not rejected(h, "submit_score", "line_id", i)
                        calls.append(("submit_score", {"line_id": i, "score": calc["score"] + (5 if wrong else 0), "evidence": calc["evidence"], "reasoning": f"Applied rubric line {i} to the stated evidence."}))
                elif not record.get("statement"):
                    calls.append(("report_missing", {"line_id": i, "field": "statement"}))
                elif not any(n == "submit_statement_score" and not c.startswith("REJECTED") for n, a, c in h):
                    calls.append(("submit_statement_score", {**judge(record["statement"]), "reasoning": "Judged goals, evidence, length and community against the M3 text."}))
            return (None, calls) if calls else (f"All {section} criteria are scored or reported missing.", None)
        if agent == "Panel Manager":
            if not called(h, "read_board"):
                return None, [("read_board", {}), ("search_rubric", {"query": "weights and decision thresholds"})]
            if not called(h, "compute_final_score"):
                return None, [("compute_final_score", {})]
            final = json.loads(called(h, "compute_final_score")[0][2])
            if ask and final["total"] is not None and not called(h, "ask_worker"):
                return None, [("ask_worker", {"agent_name": "Merit Agent", "question": "Please confirm the basis for the personal statement score."})]
            if not ok(h, "submit_decision", "decision", final["required_decision"]):
                board = json.loads(called(h, "read_board")[0][2])
                failed = (board["eligibility"] or {}).get("failed", [])
                cited = failed or [s["line_id"] for s in board["scores"]] + ["W1", "D1"]
                return None, [("submit_decision", {"decision": final["required_decision"], "cited_lines": ",".join(cited), "reasoning": "Decision follows the rubric: " + final["because"] + "."})]
            return "Final decision recorded.", None
        if agent == "Consistency Agent":
            ids = re.findall(r"APP-[0-9A-F]{6}", user)[:2]
            scored = rubric.line_ids("merit") + rubric.line_ids("need")
            if not called(h, "read_application"):
                return None, [("read_application", {"app_id": i}) for i in ids]
            if not called(h, "replay_score"):
                return None, [("replay_score", {"app_id": a, "line_id": l}) for a in ids for l in scored]
            if not called(h, "compare_applications"):
                return None, [("compare_applications", {"app_a": ids[0], "app_b": ids[1]})]
            if not called(h, "submit_consistency"):
                replays = [json.loads(c) for n, a, c in h if n == "replay_score"]
                bad = sorted({r["line_id"] for r in replays if not r["match"]})
                rows = json.loads(called(h, "compare_applications")[0][2])
                conflict = [r["line_id"] for r in rows if r["same_evidence_different_score"]]
                top = ", ".join(r["line_id"] for r in rows[:2])
                flagged = sorted(set(bad) | set(conflict))
                return None, [("submit_consistency", {"consistent": not flagged, "flagged_lines": ",".join(flagged), "explanation": f"Largest drivers of the score gap: {top}."})]
            return "Audit complete.", None
        return "Done.", None

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        await asyncio.sleep(pause)
        messages = body["messages"]
        agent = re.search(r"You are the ([A-Za-z ]+?)\.", text_of(messages[0])).group(1)
        user, h = history(messages)
        content, calls = act(agent, user, h, messages)
        counter["n"] += 1
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = [{"id": f"call_{counter['n']}_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]
            msg["content"] = None
        return {"id": f"chatcmpl-{counter['n']}", "object": "chat.completion", "created": 0, "model": body.get("model", "fake"),
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    return app
