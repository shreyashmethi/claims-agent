# Agentic Claims Processing Manager

A multi-agent system that assesses manufacturing claims end to end: it reads the claim and its documents, plans its own investigation, cross-checks systems of record, asks the claimant for missing evidence, detects duplicates and recurring failures, recommends a decision with evidence, routes it to the right human approvers, and executes the approved settlement, replacement, carrier recovery and corrective actions.

LLM agents decide what to look at and what it means. A deterministic rules engine computes every eligibility result and every rupee. Humans approve every financial commitment, policy exception and rejection.

Design document: *Agentic Claims Processing Manager — Architecture* (the doc this code implements). This repository holds the agents, orchestration, tools, rules, mock services and data, the API, a web console for claims officers and approvers, a CLI with a live trace, tests and evaluation.

## Quick start

Web console (needs Python 3.11+ and Node 18+):

```bash
make setup                   # creates .venv, installs Python and Node dependencies
make console                 # seeds data, builds the console, serves both on http://localhost:8000

```

Use a fresh virtual environment (what `make setup` does) rather than a conda base environment: the project needs recent LangGraph and LangChain releases, and `langgraph-checkpoint-sqlite` is a separate package from `langgraph`. `make check` lists anything missing.

Open http://localhost:8000, click **Start all 6 claims**, and work the approvals queue. API docs are at `/api/docs`. Or run `docker compose up --build` and open the same address.

Command line:

```bash
source .venv/bin/activate    # after make setup
python cli.py seed           # synthetic data, policies, PDFs with planted conflicts
python cli.py run all        # every scenario, all five claim types, with a live trace
python -m pytest -q          # 25 tests
python cli.py eval           # 30-claim golden set

```

Runs with no API key: `LLM_PROVIDER=offline` (the default) uses a deterministic planner and heuristics behind the same agent contracts and tools. To run the LLM agents, install an adapter and set the provider:

```bash
pip install -r requirements-llm.txt
export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=...   # or openai / google_genai / ollama
python cli.py seed && python cli.py run s2

```

## Troubleshooting


| Symptom                                                                                | Fix                                                                                                                                                                             |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `No module named 'langgraph.checkpoint.sqlite'` or `make check` lists missing packages | The environment is missing the project's requirements. Run `make setup`, or `pip install -r requirements-dev.txt` inside a virtual environment. Avoid a conda base environment. |
| `npm: command not found` during `make console`                                         | The console is prebuilt in `frontend/dist`, so the server still runs. Install Node 18+ only if you change the frontend.                                                         |
| `Address already in use` on port 8000                                                  | Stop the other server, or run `DEMO_STEP_DELAY_MS=300 .venv/bin/python -m uvicorn app.api.server:server --port 8001`.                                                           |
| Claims show as already processed                                                       | Click **Reset demo data** in the console, or run `python cli.py seed`.                                                                                                          |
| `CryptographyDeprecationWarning: ARC4 has been moved`                                  | Harmless; comes from pypdf with newer `cryptography` builds and is now silenced.                                                                                                |


## What the demo covers

All three scenarios and all five claim types run end to end on seeded data.


| Claim                 | Type                   | Scenario       | What happens                                                                                                                                                                                                                                              | Outcome                                                                 |
| --------------------- | ---------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| CLM-1001              | Warranty               | 1. End-to-end  | Clean checks; labour 8 h vs 6 h standard capped by WAR-6.2                                                                                                                                                                                                | Partial, INR 20,600, Claims Officer, credit note                        |
| CLM-2001              | Transit damage         | 2. Conflicting | Serial typo, invoice 12% above dispatch value, no POD remark, injected instruction in the form. Agent requests evidence, reassesses, corrects the serial on photo evidence, accepts carrier acknowledgement as a policy exception, caps at dispatch value | Partial, INR 3,03,500, Manager then Finance, payment + carrier recovery |
| CLM-2001 `--no-reply` | Transit damage         | 2. Unresolved  | Claimant never answers two requests                                                                                                                                                                                                                       | Escalated with open findings                                            |
| CLM-3001              | Dealer reimbursement   | 3. Duplicate   | Invoice already reimbursed in July, resubmitted with a new date. Supervisor checks duplicates first and skips the full investigation                                                                                                                      | Rejected, Claims Manager                                                |
| CLM-3002              | Warranty               | 3. Recurring   | Third fan failure on LD-509: valid repeat (each failure has its own service record). Batch FB-2026-03 failed on 11 other units across 4 dealers; dealer has a duplicate on record                                                                         | Approved, INR 9,200, plus two CAPA tickets approved by the Quality Lead |
| CLM-4001              | Parts replacement      | 1              | Warehouse packed the wrong sensor                                                                                                                                                                                                                         | Replacement order for 4 x SN-75                                         |
| CLM-5001              | Employee reimbursement | 1              | Hotel and per diem above grade caps                                                                                                                                                                                                                       | Partial, INR 32,700, reporting manager then Finance                     |


```bash
python cli.py run s1 | s2 | s3 | parts | employee | all
python cli.py run s2 --no-reply          # escalation path
python cli.py run s2 --approve interactive   # you approve, modify, reject or send back
python cli.py trace CLM-2001 [--json]    # replay a stored trace
python cli.py status | outbox | actions

```

Pre-generated output is in `docs/traces/`: full and summary runs, the escalation run, per-claim JSON traces, claimant letters, executed actions and the evaluation table.

## Web console

![Approval decision on the conflicting transit claim](https://claude.ai/chat/docs/screenshots/3_approval_decision.png)

A single-page React + TypeScript app (`frontend/`) served by FastAPI at `/`, with the API under `/api`.

- **Approvals queue.** Every claim waiting for a person, with the recommendation, the amount and the role the matrix is waiting for. Claims not yet started can be started from here, one at a time or all together.
- **Claim view.** A lifecycle strip shows where the claim stands, including rounds with the claimant and escalation. When a person is needed, an amber band carries the decision:
  - **Approvers** see the recommendation and its reasons, then approve, change the amount, reject or send back with a question. Names and reasons are recorded, and changes become precedents.
  - **Claimant replies** (the demo plays the claimant): send the scenario's prepared reply, upload PDFs, or record no response.
  - The **quality lead** approves or declines corrective-action tickets separately.
- **How the agents worked this claim.** The trace grouped into delegation steps. Each step shows whether the case manager chose it among allowed options or the process required it, plus the rules that fired, findings, guardrails, status changes and expandable tool calls. While agents are working it updates live.
- **Side panels.** Decision (calculation table, reasons, approval chain, actions carried out, eligibility rules, duplicate analysis), Findings, Evidence (the hashed ledger; every evidence chip anywhere in the page opens its raw tool result here), Documents (extracted fields, line items, corrections, injection warnings, original PDF) and Messages (what the claimant was sent).
- **New claim.** Generate a fresh claim with documents for ten situations (clean warranty, labour over standard, out of coverage, late transit notice, duplicate dealer invoice, missing receipt and more), or upload PDFs.

`DEMO_STEP_DELAY_MS` (set to 300 by `make console` and Docker) paces the supervisor so a run can be watched; the CLI and tests run at full speed. For frontend development, run the server with `make console` and `make web` for Vite on :5173 with hot reload (it proxies `/api`).

More screens are in `docs/screenshots/`: the queue, a claim waiting on the claimant, a closed claim with its timeline and decision, the documents panel with the serial correction, and the duplicate rejection.

## Architecture

```
Claimant / API ──> LangGraph orchestration ─────────────────────────────────────────────┐
                   Case Manager (supervisor) ⇄ Intake, Completeness, Policy,           │
                   Investigation, Duplicate & Pattern, Adjudication, Settlement         │
                   Verifier (code) · interrupts: await_claimant, await_approval         │
                                 │                          │                          │
                         Read tools (evidence)       Rules engine            Write tools (signed token)
                                 │                          │                          │
                   Mock enterprise services · claims history · policy store · trace store

```

### Agents


| Agent               | Code                          | Decides                                                                     | Tools                                                                 |
| ------------------- | ----------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Case Manager        | `app/agents/case_manager.py`  | Next step each turn, within hard guards                                     | delegation                                                            |
| Intake              | `app/agents/intake.py`        | Extraction, classification, early duplicate signal                          | `read_document`, `classify_claim`, `lookup_*`, `search_claims`        |
| Completeness        | `app/agents/completeness.py`  | Missing documents and fields, request wording                               | `required_docs`, `check_doc_quality`                                  |
| Policy              | `app/agents/policy.py`        | Explains rule results, finds clauses, identifies waivable rules             | `rules_evaluate`, `search_policy`                                     |
| Investigation       | `app/agents/investigation.py` | Which checks to run, follow-ups, corrections, findings                      | `run_check`, records APIs, `record_correction`                        |
| Duplicate & Pattern | `app/agents/duplicate.py`     | Duplicate vs valid repeat, recurring and batch failures                     | `search_claims`, `similar_claims`, `failure_stats`, `service_history` |
| Adjudication        | `app/agents/adjudication.py`  | Approve / partial / reject / escalate with evidence                         | `rules_calculate_settlement`, `get_precedents`                        |
| Verifier            | `app/agents/verifier.py`      | (code) amount = calculator, reasons cite real evidence, decision consistent | none                                                                  |
| Settlement & Comms  | `app/agents/settlement.py`    | Executes approved actions, writes the claimant letter                       | payment, credit note, replacement, carrier claim, CAPA, messages      |


Each LLM agent is a tool-using ReAct loop whose final answer is a `submit` call validated against a Pydantic contract (`app/schemas.py`); an invalid or ungrounded submission is returned to the model as a tool error so it can repair it. If the provider fails, the agent falls back to its deterministic path and the trace records it.

### How it adapts

The supervisor re-plans after every step against a *facts version* that changes when documents arrive or a field is corrected, so stale analyses re-run and unaffected ones do not. Examples visible in the traces:

- CLM-3001: Intake sees the invoice number in history, so the supervisor runs duplicate analysis before policy or investigation, and goes straight to adjudication once the duplicate is confirmed.
- CLM-2001: Investigation finds three conflicts and states what evidence would resolve each; the supervisor asks the claimant; on reply, Investigation records a serial correction backed by the plate photo, which makes the earlier policy result stale, so Policy re-runs and TRN-2.2 now passes.
- Approver *send back* re-opens the case with the approver's question; agents re-run and the case is re-routed.

### Orchestration and state

`app/graph/build_graph.py`: one `case_manager` node with conditional `Command` routing, specialist nodes that return to it, a `verifier` node, and two interrupt nodes. `CaseState` (`app/graph/state.py`) holds the claim snapshot, plan, evidence ledger, findings, eligibility, duplicate assessment, calculation, recommendation, approvals, actions and information requests. A SQLite checkpointer persists every case, so a claim paused for a claimant or an approver resumes exactly where it stopped, across restarts.

The claim lifecycle (`app/graph/lifecycle.py`) only moves along guarded edges: `RECEIVED → CLASSIFIED → UNDER_REVIEW ⇄ PENDING_INFO → RECOMMENDED → PENDING_APPROVAL → APPROVED | PARTIALLY_APPROVED | REJECTED → SETTLING → SETTLED → CLOSED`, with `ESCALATED` reachable from any review state.

### Rules engine

`app/rules/engine.py` + `app/rules/params.yaml`. Eligibility rules per claim type (WAR-*, PRT-*, TRN-*, DLR-*, EMP-*), the admissible-amount calculator with a line-by-line breakdown, and the approval authority matrix. Every result carries the policy clause it implements. Policies live in `data/policies/*.md`, chunked by clause ID and retrieved with BM25 (`app/tools/policy_store.py`). Parameters are illustrative values for the mock policies.

### Guardrails


| Risk                                 | Control                                                                            | Where                              |
| ------------------------------------ | ---------------------------------------------------------------------------------- | ---------------------------------- |
| Model invents or miscalculates money | Amount must equal the calculator; verifier blocks otherwise                        | `verifier.py`                      |
| Ungrounded reasoning                 | Every finding and reason must cite evidence IDs that exist in the ledger           | ReAct validation, verifier         |
| Unauthorised payments                | Write tools require an HMAC token bound to claim, action and amount                | `approvals.py`, `tools/actions.py` |
| Agent overreach                      | Tool registry enforces per-agent permissions                                       | `tools/registry.py`                |
| Prompt injection in documents        | Document text passed only as quoted data; injection scan flags it for the approver | `documents.py`, `intake.py`        |
| Runaway loops                        | Turn budget, two claimant rounds, two verifier retries, then escalate              | `case_manager.py`                  |
| Supervisor picks an invalid step     | Choice restricted to an allowed set; invalid choice overridden and logged          | `case_manager.py`                  |
| Double execution on retry            | Idempotency key per action                                                         | `tools/actions.py`                 |
| Status tampering                     | Lifecycle transitions are guarded functions                                        | `graph/lifecycle.py`               |
| Leaking internal findings            | Claimant letters are built from structured data, not internal reasons              | `settlement.py`                    |


### Human in the loop

Approvers act in matrix order (`required_approvals` in the rules engine): Claims Officer up to INR 25,000 with no exception and confidence ≥ 0.85; Claims Manager up to INR 2,00,000, for every rejection and every policy exception; Finance Controller above that; reporting manager (then Finance above 25,000) for employee claims; Quality Lead for CAPA tickets. Each approver can **approve**, **modify** (new amount + reason), **reject**, or **send back** with a question. Modifications and rejections are stored as precedents (feedback memory) and shown to the Adjudication agent on similar future cases, as context only. Approval signs one token per authorised action; the Quality Lead can decline a CAPA ticket without blocking the claim's settlement.

### Memory


| Layer         | Holds                                   | Where                                               |
| ------------- | --------------------------------------- | --------------------------------------------------- |
| Working       | Case state, evidence ledger, findings   | LangGraph checkpointer                              |
| Case record   | Final claim, outcome, actions, outbox   | `claims`, `actions_log`, `outbox` tables            |
| Institutional | Past claims, outcomes, failure patterns | `claims` history, `failure_stats`, `similar_claims` |
| Feedback      | Approver overrides with reasons         | `overrides` table, `get_precedents`                 |


CLM-3002 shows institutional memory at work: the duplicate rejected in CLM-3001 minutes earlier becomes a dealer-history pattern and triggers a dealer audit ticket.

## API

Standalone: `uvicorn app.api.main:app --reload` (docs at `/docs`). With the console: `app.api.server:server` mounts the same API under `/api`.


| Method    | Path                                              | Purpose                                                                                                                                                      |
| --------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| POST      | `/claims`                                         | Submit a claim (multipart: `claimant_id`, `files[]`), starts processing                                                                                      |
| POST      | `/claims/{id}/process`                            | Start a seeded claim                                                                                                                                         |
| GET       | `/claims`, `/claims/{id}`                         | List; full case view (plan, findings, eligibility, recommendation, approvals, actions)                                                                       |
| GET       | `/claims/{id}/evidence[?evidence_id=]`            | Evidence ledger                                                                                                                                              |
| GET       | `/claims/{id}/documents[/{doc_id}]`               | Document list; original PDF                                                                                                                                  |
| GET       | `/claims/{id}/trace`, `/claims/{id}/trace/stream` | Trace, or live server-sent events                                                                                                                            |
| POST      | `/claims/{id}/info-response[/upload]`             | Claimant reply: scripted, uploaded files, or no response                                                                                                     |
| GET       | `/approvals/pending`                              | Approval queue with recommendation, calculation and findings                                                                                                 |
| POST      | `/claims/{id}/approval`                           | `{role, decision, amount?, reason?, approver}`; role must match the waiting role                                                                             |
| GET       | `/outbox`, `/actions`, `/escalations`             | Letters and requests sent, executed actions, escalations                                                                                                     |
| GET, POST | `/admin/samples[/{kind}]`                         | List sample situations; generate and start a fresh claim                                                                                                     |
| POST      | `/admin/seed`                                     | Reset demo data                                                                                                                                              |
| GET       | `/mock/...`                                       | Mock enterprise services: equipment, parties, warranty, service history, invoices, parts, dispatches, shipments, claims search, failure stats, policy search |


## LLM configuration

`models.yaml` maps roles (`supervisor`, `extractor`, `investigator`, `reasoner`, `writer`) to models per provider, so a cheaper model can extract while a stronger one plans. Override with `LLM_PROVIDER` and `LLM_MODEL_<ROLE>`. Model names in the file are examples; set them to models your account can use. Structured output uses JSON-schema mode on providers that support it.

## Tests and evaluation

- 25 tests in total.
- `tests/test_units.py`: parsing, injection scan, policy retrieval, tokens, approval matrix, verifier.
- `tests/test_scenarios.py`: every scenario, escalation, approver modify / reject / send back, CAPA decline, token refusal, idempotency, tool permissions, illegal transitions, an approver paying on a rejection.
- `tests/test_llm_path.py`: a scripted chat model drives a full case through every LLM path (supervisor choice, structured extraction and classification, ReAct loops, verifier-driven repair), plus the disallowed-choice guardrail and provider-failure fallback.
- `tests/test_api.py`: API flow, role checks, upload of a new claim.
- `python cli.py eval`: 6 scenario claims + 24 templated claims across all five types (clean, over-standard, out of coverage, over hours, late notice, wrong part, out of window, duplicate invoice, missing receipt). Current result: 30/30 decisions, 30/30 exact amounts, 30/30 expected findings; ~10 agent turns and ~24 tool calls per claim (offline mode).

## Repository layout

```
app/
  agents/      case_manager, intake, completeness, policy, investigation, duplicate, adjudication, verifier, settlement
  graph/       state.py, build_graph.py, lifecycle.py
  tools/       registry.py (permissions, evidence, trace), catalog.py, services.py, checks.py, actions.py, policy_store.py
  rules/       engine.py, facts.py, params.yaml
  llm/         provider.py (provider-agnostic model factory)
  api/         main.py (FastAPI), server.py (API under /api + built console at /)
  seed.py      synthetic data and planted scenarios;  golden.py  evaluation set
  runner.py    start / resume / inspect claim graphs;  trace.py  append-only trace
data/policies/ mock policy documents
docs/traces/   sample runs, JSON traces, letters, eval table
frontend/      React + TypeScript console (Vite): App, ClaimView, Timeline, Panels
docs/screenshots/  console screens
cli.py         CLI with live trace

```

## Notes and limits

- All data, policies, rates and thresholds are synthetic. The simulation date is fixed at 2026-10-07 (`CLAIMS_SIM_DATE`) so runs are reproducible.
- Documents are generated PDFs with a text layer; photos are represented by PDFs that carry what an OCR or vision step would read (e.g. a serial-plate reading). In LLM mode, extraction goes through the extractor model and is cross-checked against the layout parser.
- Differences from the design doc: policy retrieval is BM25 only (an embedding index can sit behind `policy_store.search`), and narrative similarity uses token overlap rather than embeddings, to keep the demo dependency-free. Approver identity is taken from the request body; production would assert role via SSO.
- Mock write services record actions in SQLite; nothing leaves the machine.

