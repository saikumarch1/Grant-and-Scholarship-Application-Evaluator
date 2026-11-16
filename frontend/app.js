"use strict";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const chip = (text, kind = "") => `<span class="chip ${kind}">${esc(text)}</span>`;
const EVAL_AGENTS = ["Intake Agent", "Eligibility Agent", "Merit Agent", "Need Agent", "Panel Manager"];
const ROSTER = [
  ["Intake Agent", "Reads a raw application in any wording and records a validated structured record", "parse_amount, submit_record"],
  ["Eligibility Agent", "Checks the hard rules and can reject outright with the failed rule", "get_application, search_rubric, check_rule, submit_eligibility"],
  ["Merit Agent", "Scores academics, achievements and the personal statement", "get_application, search_rubric, calculate_band_score, submit_score, submit_statement_score, report_missing"],
  ["Need Agent", "Scores income, dependents and existing aid", "get_application, search_rubric, calculate_band_score, submit_score, report_missing"],
  ["Panel Manager", "Coordinates the workers, questions them, applies weights and decides", "read_board, search_rubric, compute_final_score, ask_worker, submit_decision"],
  ["Consistency Agent", "Audits two scored applications for consistent rubric use", "read_application, replay_score, compare_applications, submit_consistency"],
];

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) {
    let detail = await res.text();
    try { detail = JSON.parse(detail).detail; } catch (e) { /* plain text */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

/* ---------- live agent floor ---------- */
class Floor {
  constructor(root, names) {
    this.root = root;
    this.names = names;
    this.build();
    setInterval(() => this.tick(), 250);
  }
  build() {
    const card = (name) => {
      const cls = name === "Intake Agent" ? "intake" : name === "Panel Manager" ? "panel" : "";
      return `<article class="agent ${cls}" data-agent="${esc(name)}"><header><h4>${esc(name)}</h4><span class="status">Waiting</span></header>
        <ul class="acts"></ul><p class="note" hidden></p></article>`;
    };
    if (this.names.length === 1) { this.root.innerHTML = card(this.names[0]); }
    else {
      const [intake, ...rest] = this.names;
      this.root.innerHTML = card(intake) + `<div class="lane" data-lane="out">fan out, concurrent</div>` + rest.slice(0, 3).map(card).join("") +
        `<div class="lane" data-lane="in">fan in</div>` + card(rest[3]);
    }
    this.state = Object.fromEntries(this.names.map((n) => [n, { status: "waiting", started: null, secs: null }]));
    this.seen = new Set();
  }
  reset() { this.build(); }
  el(name) { return this.root.querySelector(`[data-agent="${CSS.escape(name)}"]`); }
  setStatus(name, status, text) {
    const el = this.el(name); if (!el) return;
    el.classList.remove("working", "done", "error"); if (status !== "waiting") el.classList.add(status);
    $(".status", el).textContent = text;
  }
  push(name, html, cls = "") {
    const el = this.el(name); if (!el) return null;
    const list = $(".acts", el), li = document.createElement("li");
    li.className = cls; li.innerHTML = html; list.appendChild(li); list.scrollTop = list.scrollHeight; return li;
  }
  handle(ev) {
    const key = ev.run_id + ":" + ev.seq;
    if (this.seen.has(key)) return; this.seen.add(key);
    const name = ev.agent, d = ev.data || {};
    if (ev.type === "fan_out") $('[data-lane="out"]', this.root)?.classList.add("hot");
    if (ev.type === "fan_in") $('[data-lane="in"]', this.root)?.classList.add("hot");
    if (!this.state[name]) return;
    const st = this.state[name];
    if (ev.type === "agent_start") { st.status = "working"; st.started = ev.ts; this.setStatus(name, "working", "Working"); }
    else if (ev.type === "tool_call") {
      const args = Object.entries(d.args || {}).map(([k, v]) => `${k}=${String(v).slice(0, 48)}`).join(", ");
      st.last = this.push(name, `<span class="tool">${esc(d.tool)}</span>(${esc(args.slice(0, 120))})<span class="res">running</span>`);
    } else if (ev.type === "tool_result") {
      const rejected = String(d.result).startsWith("REJECTED");
      if (st.last) { st.last.className = rejected ? "guard" : ""; $(".res", st.last).textContent = (rejected ? "guard: " : "") + String(d.result).slice(0, 170); }
    } else if (ev.type === "agent_message") {
      if (d.peer) this.push(name, `<b>${d.direction === "to" ? "Asks " + esc(d.peer) : esc(d.peer) + " replies"}:</b> ${esc(d.text)}`, "talk");
      else { const note = $(".note", this.el(name)); note.hidden = false; note.textContent = d.text; }
    } else if (ev.type === "retry") this.push(name, `<span class="res">provider busy, retrying (${esc(d.attempt)})</span>`);
    else if (ev.type === "agent_done") { st.status = "done"; st.secs = d.seconds; this.setStatus(name, "done", `Done ${d.seconds}s`); }
    else if (ev.type === "agent_error") { st.status = "error"; this.setStatus(name, "error", "Error"); this.push(name, `<span class="res">${esc(d.error)}</span>`, "guard"); }
  }
  tick() {
    for (const [name, st] of Object.entries(this.state))
      if (st.status === "working" && st.started) this.setStatus(name, "working", `Working ${((Date.now() / 1000) - st.started).toFixed(1)}s`);
  }
}

/* ---------- streaming ---------- */
function follow(runId, floor, onEnd) {
  const source = new EventSource(`/api/runs/${runId}/events`);
  source.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    floor.handle(ev);
    if (ev.type === "run_end" || ev.type === "run_error") { source.close(); onEnd(ev); }
  };
  return source;
}

/* ---------- rendering ---------- */
function gauge(total, t) {
  if (total == null) return "";
  return `<div class="gauge"><i style="left:calc(${total}% - 2px)"></i><u style="left:${t.waitlist}%">${t.waitlist}</u><u style="left:${t.award}%">${t.award}</u></div>`;
}
function renderResult(s) {
  const d = s.decision;
  if (!d) return `<div class="banner bad"><p class="decision">Not completed</p><p class="why">${esc(s.error || "The run ended before a decision was recorded.")}</p></div>`;
  const total = s.total == null ? "Not scored" : `${s.total} of 100`;
  const cited = d.cited.map((c) => chip(c, "line")).join("");
  const checks = s.checks.map((c) => `<tr><td>${chip(c.line_id, "line")}</td><td>${esc(c.criterion)}<div class="sub">${esc(c.rule_text)}</div></td><td>${esc(c.evidence)}</td><td>${chip(c.status, c.status)}</td></tr>`).join("");
  const head = `<tr><th>Line</th><th>Criterion and rubric text</th><th>Evidence</th><th class="num">Score</th><th class="num">Weight</th><th class="num">Points</th></tr>`;
  const rows = (section) => s.rows.filter((r) => r.section === section).map((r) => `<tr><td>${chip(r.line_id, "line")}</td>
      <td>${esc(r.criterion)}<div class="sub">${esc(r.rule_text)}</div>${r.reasoning ? `<div class="ai">${esc(r.agent)}: ${esc(r.reasoning)}</div>` : ""}</td>
      <td>${esc(r.missing ? "not stated: " + r.missing : r.evidence)}${r.components ? `<div class="sub">goals ${r.components.goals}, evidence ${r.components.evidence}, length ${r.components.length}, community ${r.components.community}</div>` : ""}</td>
      <td class="num">${r.score ?? "missing"}</td><td class="num">${Math.round(r.weight * 100)}%</td><td class="num">${r.points ?? "n/a"}</td></tr>`).join("");
  const na = (v) => (v == null ? "n/a" : v);
  const w = s.weights;
  const foot = s.total != null ? `<tr class="total"><td colspan="5">Weighted final score (${na(s.merit)} x ${Math.round(w.merit * 100)}% + ${na(s.need)} x ${Math.round(w.need * 100)}%, line W1)</td><td class="num">${s.total}</td></tr>`
    : `<tr class="total"><td colspan="6">No weighted total: ${s.eligibility?.failed?.length ? "a hard eligibility rule failed, so scores are shown for information only." : "required data is missing, so the application is held."}</td></tr>`;
  return `<div class="banner ${d.decision}"><p class="decision">${esc(d.decision[0].toUpperCase() + d.decision.slice(1))}</p><p class="who">${esc(s.applicant)} &middot; ${esc(s.app_id)}</p>
    <div class="meta"><div><span>Final score</span><b>${total}</b></div><div><span>Merit</span><b>${na(s.merit)}</b></div><div><span>Need</span><b>${na(s.need)}</b></div></div>
    ${gauge(s.total, s.thresholds)}<p class="why">${esc(d.reasoning)}</p><p style="margin:.7rem 0 0">Rubric lines cited: ${cited}</p></div>
    <div class="sheet"><h4>Hard eligibility rules</h4><div class="ai" style="margin:0 0 .4rem">${esc(s.eligibility?.reasoning || "")}</div>
      <table><tr><th>Line</th><th>Rule</th><th>Evidence</th><th>Result</th></tr>${checks}</table>
      <h4>Merit criteria</h4><table>${head}${rows("merit")}<tr class="sum"><td colspan="5">Merit section score</td><td class="num">${na(s.merit)}</td></tr></table>
      <h4>Need criteria</h4><table>${head}${rows("need")}<tr class="sum"><td colspan="5">Need section score</td><td class="num">${na(s.need)}</td></tr>${foot}</table></div>`;
}

/* ---------- state and actions ---------- */
const evalFloor = new Floor($("#floorEval"), EVAL_AGENTS);
const conFloor = new Floor($("#floorCon"), ["Consistency Agent"]);
const runs = []; let activeSource = null;

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  if (name === "board") loadBoard();
  if (name === "consistency") loadScored();
  if (name === "audit") loadAudit();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

async function loadHealth() {
  const h = await api("/api/health"), el = $("#health");
  el.textContent = h.llm_configured ? `Model ${h.model}` : "No API key";
  el.classList.toggle("bad", !h.llm_configured);
  $("#notice").hidden = h.llm_configured;
  $("#notice").textContent = "No API key is configured, so the agents cannot run. Copy .env.example to .env, set LLM_API_KEY, and restart the server.";
  $("#evaluate").disabled = !h.llm_configured; $("#runCon").disabled = !h.llm_configured;
}

$("#evaluate").addEventListener("click", async () => {
  const text = $("#appText").value.trim(), err = $("#formError"); err.hidden = true;
  if (text.length < 20) { err.textContent = "Enter the application text first."; err.hidden = false; return; }
  try {
    const { app_id } = await api("/api/applications", { method: "POST", body: JSON.stringify({ text }) });
    startEvaluation(app_id);
  } catch (e) { err.textContent = e.message; err.hidden = false; }
});

function startEvaluation(appId) {
  if (activeSource) activeSource.close();
  evalFloor.reset(); $("#result").innerHTML = ""; $("#runLabel").textContent = `Run ${appId}`;
  $("#evaluate").disabled = true;
  runs.unshift({ id: appId, kind: "Application" });
  activeSource = follow(appId, evalFloor, async () => {
    $("#result").innerHTML = renderResult(await api(`/api/applications/${appId}`));
    $("#evaluate").disabled = false; loadHealth();
  });
}

async function openApplication(appId) {
  showTab("evaluate");
  if (activeSource) activeSource.close();
  evalFloor.reset(); $("#runLabel").textContent = `Run ${appId}`;
  (await api(`/api/runs/${appId}/trace`)).forEach((ev) => evalFloor.handle(ev));
  const snap = await api(`/api/applications/${appId}`);
  $("#appText").value = snap.text || $("#appText").value;
  $("#result").innerHTML = ["closed", "failed"].includes(snap.status) ? renderResult(snap) : "";
}

async function loadBoard() {
  const list = await api("/api/applications");
  if (!list.length) { $("#board").innerHTML = `<p class="muted">No applications yet. Evaluate one on the first tab.</p>`; return; }
  let rank = 0;
  const rows = list.map((a) => {
    const scored = a.total != null; if (scored) rank += 1;
    return `<tr class="click" data-id="${esc(a.app_id)}"><td class="num">${scored ? rank : "none"}</td><td>${esc(a.applicant)}<div class="sub">${esc(a.app_id)}</div></td>
      <td>${a.decision ? chip(a.decision[0].toUpperCase() + a.decision.slice(1), a.decision) : chip(a.status, a.status)}</td>
      <td class="num">${scored ? a.total : "not scored"}</td><td class="num">${a.merit ?? "n/a"}</td><td class="num">${a.need ?? "n/a"}</td><td>${(a.cited || []).filter((c) => !["W1", "D1"].includes(c)).map((c) => chip(c, "line")).join("")}</td></tr>`;
  }).join("");
  $("#board").innerHTML = `<div class="sheet"><table><tr><th class="num">Rank</th><th>Applicant</th><th>Decision</th><th class="num">Final</th><th class="num">Merit</th><th class="num">Need</th><th>Lines</th></tr>${rows}</table></div>`;
  $("#board").querySelectorAll("tr.click").forEach((tr) => tr.addEventListener("click", () => openApplication(tr.dataset.id)));
}
$("#refreshBoard").addEventListener("click", loadBoard);

async function loadScored() {
  const scored = (await api("/api/applications")).filter((a) => a.total != null);
  const opts = scored.map((a) => `<option value="${esc(a.app_id)}">${esc(a.applicant)} (${esc(a.app_id)}, ${a.total})</option>`).join("");
  $("#conA").innerHTML = opts; $("#conB").innerHTML = opts;
  if (scored.length > 1) $("#conB").selectedIndex = 1;
  if (scored.length < 2) { $("#conError").textContent = "Evaluate at least two applications that receive a weighted score first."; $("#conError").hidden = false; }
  else $("#conError").hidden = true;
}

$("#runCon").addEventListener("click", async () => {
  const err = $("#conError"); err.hidden = true; $("#conResult").innerHTML = ""; conFloor.reset();
  try {
    const job = await api("/api/consistency", { method: "POST", body: JSON.stringify({ app_a: $("#conA").value, app_b: $("#conB").value, inject_error: $("#conInject").checked }) });
    runs.unshift({ id: job.run_id, kind: "Consistency" }); $("#runCon").disabled = true;
    follow(job.run_id, conFloor, async () => {
      const done = await api(`/api/consistency/${job.run_id}`); $("#runCon").disabled = false;
      const r = done.result || {};
      if (r.error) { $("#conResult").innerHTML = `<div class="banner bad"><p class="decision">Not completed</p><p class="why">${esc(r.error)}</p></div>`; return; }
      const flagged = (r.flagged_lines || []).map((l) => chip(l, "fail")).join("") || "none";
      $("#conResult").innerHTML = `<div class="banner ${r.consistent ? "award" : "reject"}"><p class="decision">${r.consistent ? "Consistent" : "Inconsistent"}</p>
        <p class="why">${esc(r.explanation)}</p><p style="margin:.7rem 0 0">Flagged lines: ${flagged}</p></div>
        <div class="sheet"><table><tr><th>Application</th><th>Line</th><th>Replay</th></tr>${r.replays.map((x) => `<tr><td>${esc(x.app_id)}</td><td>${chip(x.line_id, "line")}</td><td>${chip(x.match ? "match" : "mismatch", x.match ? "pass" : "fail")}</td></tr>`).join("")}</table></div>`;
    });
  } catch (e) { err.textContent = e.message; err.hidden = false; }
});

async function loadAudit() {
  const apps = await api("/api/applications");
  const entries = [...runs.filter((r) => r.kind === "Consistency").map((r) => ({ id: r.id, label: `Consistency ${r.id}` })), ...apps.map((a) => ({ id: a.app_id, label: `${a.applicant} (${a.app_id})` }))];
  const sel = $("#auditRun"), current = sel.value;
  sel.innerHTML = entries.map((e) => `<option value="${esc(e.id)}">${esc(e.label)}</option>`).join("");
  if (current) sel.value = current;
  await showTrace();
  const log = await api("/api/log");
  $("#logState").textContent = log.valid ? `Log chain verified, ${log.entries} entries` : `Log chain broken at entry ${log.first_bad}`;
  $("#logRows").innerHTML = `<div class="sheet"><table><tr><th class="num">Seq</th><th>Run</th><th>Agent</th><th>Event</th><th>Hash</th></tr>${log.latest.slice(-15).map((r) => `<tr><td class="num">${r.seq}</td><td>${esc(r.app_id)}</td><td>${esc(r.agent)}</td><td>${esc(r.event)}</td><td>${esc(r.hash.slice(0, 12))}</td></tr>`).join("")}</table></div>`;
}
async function showTrace() {
  const id = $("#auditRun").value; if (!id) { $("#trace").innerHTML = `<p class="muted">No runs yet.</p>`; return; }
  const ev = await api(`/api/runs/${id}/trace`), t0 = ev[0]?.ts || 0;
  $("#trace").innerHTML = `<div class="sheet"><table><tr><th class="num">Seq</th><th class="num">Time</th><th>Agent</th><th>Event</th><th>Detail</th></tr>${ev.map((e) => `<tr><td class="num">${e.seq}</td><td class="num">${(e.ts - t0).toFixed(2)}s</td><td>${esc(e.agent || "")}</td><td>${esc(e.type)}</td><td>${esc(JSON.stringify(e.data).slice(0, 170))}</td></tr>`).join("")}</table></div>`;
}
$("#auditRun").addEventListener("change", showTrace);

async function init() {
  $("#roster").innerHTML = `<div class="sheet"><table><tr><th>Agent</th><th>Role</th><th>Tools (plain Python functions)</th></tr>${ROSTER.map((r) => `<tr><td><b>${esc(r[0])}</b></td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("")}</table></div>`;
  const [rubric, samples] = await Promise.all([api("/api/rubric"), api("/api/samples")]);
  $("#rubricTable").innerHTML = `<div class="sheet"><table><tr><th>Line</th><th>Section</th><th>Criterion</th><th>Rule</th><th class="num">Weight</th></tr>${rubric.map((l) => `<tr><td>${chip(l.id, "line")}</td><td>${esc(l.section)}</td><td>${esc(l.criterion)}</td><td>${esc(l.text)}</td><td class="num">${l.weight ? Math.round(l.weight * 100) + "%" : ""}</td></tr>`).join("")}</table></div>`;
  $("#sample").innerHTML += samples.map((s, i) => `<option value="${i}">${esc(s.label)}</option>`).join("");
  $("#sample").addEventListener("change", (e) => { if (e.target.value !== "") $("#appText").value = samples[+e.target.value].text; });
  $("#btnBlank")?.addEventListener("click", () => {
    $("#appText").value = `Name: Rahul Sharma
Course: B.Tech Computer Engineering (undergraduate)
Latest result: 84%
Annual family income: Rs 3,50,000
Dependents in family: 4
Other financial aid: none
Documents attached: income certificate, marksheet
Achievements:
- National: 2nd place in National Robotics Olympiad
- State: Finalist in State Hackathon
- Institute: Head of College Coding Club
Statement: I aspire to build low-cost medical diagnostics hardware for under-served clinics. During my degree I designed a pulse monitor prototype tested across 3 local health centres with 95% accuracy. I intend to complete my thesis with honors and conduct free STEM training for 50 school students this year.`;
    $("#sample").value = "";
  });
  await loadHealth();
}
init().catch((e) => { $("#health").textContent = "Server unreachable"; $("#health").classList.add("bad"); console.error(e); });
