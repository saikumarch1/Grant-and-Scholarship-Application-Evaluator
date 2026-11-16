# Grant & Scholarship Application Evaluator

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Semantic Kernel](https://img.shields.io/badge/framework-Semantic%20Kernel-purple.svg)](https://github.com/microsoft/semantic-kernel)
[![Model](https://img.shields.io/badge/model-Gemini%203.5%20Flash-orange.svg)](https://ai.google.dev/)
[![Architecture](https://img.shields.io/badge/architecture-Fan--Out%20%2F%20Fan--In-green.svg)](#architecture-and-coordination)
[![Tests](https://img.shields.io/badge/tests-23%20passed-brightgreen.svg)](#test-suite)

An autonomous, production-ready multi-agent evaluation system that processes raw scholarship and grant applications, evaluates them against a grounded 12-line published rubric, and renders defensible, auditable award decisions.

Built on **Microsoft Semantic Kernel**, the system coordinates specialized AI agents using a **Fan-Out / Fan-In** architecture over shared memory, guarded with deterministic tool verification, RAG retrieval, and an append-only cryptographic audit trail.

---

## Architecture and Coordination

The system follows a **Fan-Out / Fan-In rubric scoring architecture**:

1. **Intake Phase (Sequential):** The Intake Agent structures the raw application (regardless of formatting or wording) into a validated shared state record.
2. **Scoring Phase (Fan-Out / Concurrent):** The Eligibility, Merit, and Need agents fan out simultaneously as concurrent `asyncio` tasks, retrieving governing clauses from the published rubric via RAG.
3. **Deciding Phase (Fan-In):** The Panel Manager collects all signals, verifies scores against criteria, computes the weighted score via deterministic code, questions workers when necessary, and issues the final decision (`award`, `waitlist`, or `reject`).
4. **Audit Phase (Consistency Verification):** An independent Consistency Agent cross-checks pairs of scored applications to guarantee uniform and unbiased rubric application.

```
                              ┌─────────────────┐
                              │  Intake Agent   │
                              └────────┬────────┘
                                       │
                         [ Fan-Out Concurrent Evaluation ]
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
     ┌────────┴────────┐      ┌────────┴────────┐      ┌────────┴────────┐
     │Eligibility Agent│      │   Merit Agent   │      │   Need Agent    │
     └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
              │                        │                        │
              └────────────────────────┼────────────────────────┘
                                       │
                            [ Fan-In Adjudication ]
                                       │
                              ┌────────┴────────┐
                              │  Panel Manager  │
                              └────────┬────────┘
                                       │
                              ┌────────┴────────┐
                              │Consistency Agent│
                              └─────────────────┘
```

---

## Agent Roster & Tool Capabilities

Every agent is an isolated module in `scholardesk/agents/` equipped with its own Semantic Kernel kernel, dedicated instructions, and custom Python tool functions.

| Agent | Module | Primary Role | Plain Python Tools |
|---|---|---|---|
| **Intake Agent** | `agents/intake.py` | Ingests raw application text and extracts a validated structured record | `parse_amount`, `submit_record` |
| **Eligibility Agent** | `agents/eligibility.py` | Enforces hard disqualifier rules (E1–E4); can reject outright | `get_application`, `search_rubric`, `check_rule`, `submit_eligibility` |
| **Merit Agent** | `agents/merit.py` | Evaluates academic score, achievements, and personal statement (M1–M3) | `get_application`, `search_rubric`, `calculate_band_score`, `submit_score`, `submit_statement_score`, `report_missing` |
| **Need Agent** | `agents/need.py` | Evaluates family income, dependents, and existing financial aid (N1–N3) | `get_application`, `search_rubric`, `calculate_band_score`, `submit_score`, `report_missing` |
| **Panel Manager** | `agents/panel.py` | Central coordinator: reads blackboard, questions workers, calculates weighted score, issues decision | `read_board`, `search_rubric`, `compute_final_score`, `ask_worker`, `submit_decision` |
| **Consistency Agent** | `agents/consistency.py` | Audits two evaluated applications to guarantee uniform rubric application | `read_application`, `replay_score`, `compare_applications`, `submit_consistency` |

---

## Grounded RAG, Shared Memory & Audit Trail

* **Grounded Retrieval (RAG):** Evaluators retrieve authoritative clauses from a 12-line published rubric (`data/rubric.json`) using TF-IDF search (`rubric.py`). Every awarded score and disqualification must cite the exact rule identifier (`E1`–`E4`, `M1`–`M3`, `N1`–`N3`, `W1`, `D1`).
* **Shared State (Blackboard):** Agents coordinate through a thread-safe `ApplicationState` record. The Intake Agent populates applicant data, specialist agents write scores and evidence, and the Panel Manager reads the consolidated state.
* **Append-Only Cryptographic Run Log:** Every event, tool call, and decision is recorded in `runs/run_log.jsonl`. Each entry contains a timestamp, run ID, agent name, and a SHA-256 hash incorporating the hash of the preceding entry (`prev`). Any manual tampering with past entries invalidates the hash chain.

---

## Model Integrity & Honest Failure Handling

* **Guardrails on Tool Replies:** Tool functions enforce strict validation. If an agent submits a score that disagrees with the retrieved rubric bracket or quotes an unstated percentage, the tool responds with `REJECTED: <reason>`, forcing the model to correct its evaluation.
* **Deterministic Computations:** All score sums, percentage multiplications, and weighting formulas ($55\%\text{ Merit} + 45\%\text{ Need}$) are computed in deterministic Python code, preventing LLM arithmetic hallucination.
* **Honest Failure Handling:** When essential data is omitted from an application (such as unstated family income):
  * The Eligibility Agent flags rule `E2` as `unknown`.
  * The Need Agent executes `report_missing(line_id='N1')` rather than assuming or hallucinating income.
  * The Panel Manager places the application on `waitlist`, explicitly citing missing criteria.

---

## Project Structure

```
.
├── data/
│   ├── rubric.json             # 12-line published evaluation rubric
│   └── samples.json            # Demonstration profiles & edge-case test applicants
├── docs/
│   └── architecture.svg        # System architecture diagram
├── frontend/
│   ├── index.html              # Interactive single-page dashboard
│   ├── app.js                  # SSE live event streaming & UI controller
│   └── styles.css              # Custom responsive stylesheet
├── notebooks/
│   └── demo.ipynb              # Runnable demonstration notebook printing multi-agent traces
├── runs/
│   └── run_log.jsonl           # Append-only hashed event ledger
├── scholardesk/
│   ├── agents/                 # Individual agent implementations
│   │   ├── base.py             # LLMAgent base class with automatic retry & tool filtering
│   │   ├── intake.py           # Intake Agent
│   │   ├── eligibility.py      # Eligibility Agent
│   │   ├── merit.py            # Merit Agent
│   │   ├── need.py             # Need Agent
│   │   ├── panel.py            # Panel Manager
│   │   └── consistency.py      # Consistency Agent
│   ├── config.py               # Runtime configuration
│   ├── events.py               # Real-time event bus
│   ├── llm.py                  # Semantic Kernel service with thought-signature preservation
│   ├── orchestrator.py         # ScholarDesk Fan-Out / Fan-In coordinator
│   ├── rubric.py               # TF-IDF RAG retrieval over published rubric
│   ├── runlog.py               # Tamper-evident SHA-256 hash log
│   └── state.py                # Thread-safe ApplicationState blackboard
├── tests/
│   ├── conftest.py             # Pytest fixtures & fake LLM test harness
│   ├── fake_llm.py             # Stand-in OpenAI-compatible test server
│   ├── test_pipeline.py        # End-to-end agent pipeline tests
│   └── test_server.py          # FastAPI REST & SSE stream integration tests
├── .env.example                # Template for environment configuration
├── pytest.ini                  # Pytest configuration
├── requirements.txt            # Python dependencies
└── server.py                   # FastAPI application entry point
```

---

## Quickstart

### 1. Environment Setup

```bash
# Clone the repository
git clone https://github.com/saikumarch1/Grant-and-Scholarship-Application-Evaluator.git
cd Grant-and-Scholarship-Application-Evaluator

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate       # On Linux/macOS
venv\Scripts\activate          # On Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Key

Copy `.env.example` to `.env` and provide your Google Gemini or OpenAI API key:

```env
LLM_API_KEY=your_gemini_api_key_here
LLM_MODEL=gemini-3.5-flash-lite
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

### 3. Launch Web Application

```bash
uvicorn server:app --reload
```
Open **`http://localhost:8000`** in your browser to view the interactive dashboard.

### 4. Run Demonstration Notebook

Launch Jupyter and open `notebooks/demo.ipynb` to step through multi-agent traces across award, rejection, and missing-data scenarios:

```bash
jupyter notebook notebooks/demo.ipynb
```

---

## Test Suite

The project includes 23 automated tests covering individual agents, tool guards, concurrency, panel questioning, consistency audits, and live event streaming.

Tests run against a deterministic local test server (`tests/fake_llm.py`), requiring no active API key:

```bash
pytest -q
```

Expected output:
```text
.......................                                                  [100%]
23 passed in 13.38s
```

---

## License

This project is licensed under the MIT License.
