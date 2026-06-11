# Claims Pipeline

Health insurance claims processing system (Plum AI Engineer assignment).

The pipeline is built one stage at a time. Each stage reads and writes one shared
object — the **Claim Record** — which carries the submission, everything learned so
far, and a **trace**: a list of events saying what was checked and what happened.

```
Intake  →  Document Check  →  Extraction  →  Cross-Doc Checks  →  Policy Decision  →  Fraud Check
(done)     (done)             (done)         (done)               (done)              (done)
```

**The pipeline is complete, with a UI and an eval runner.** Remaining: the
architecture + contracts documents.

**Build progress**

- [x] **Step 1 — Claim Intake**: submit a claim (member details + claim type + amount
  + files) and get back a claim ID with status `RECEIVED`, or a specific, actionable
  error saying exactly what was wrong. Pipeline runner exists with the failure rule
  baked in (a crashing component is skipped with a `SKIPPED` trace event and a
  confidence drop — the pipeline never dies). Policy comes only from
  `policy_terms.json`, loaded by the PolicyStore at boot; system knobs live in
  `app/config.py`; no policy rule lives in code.
- [x] **Step 2 — Document Check** (the early gate; first LLM call): every uploaded
  file is classified — real files by a vision model, test-case stubs taken at face
  value with zero LLM calls — and checked against the policy's document requirements
  before anything else runs. Wrong document (TC001) or unreadable required document
  (TC002) stops the claim as `NEEDS_RESUBMISSION` (decision stays `null`) with a
  message naming the exact problem and fix. Classifier failures fall back to the
  member-declared type with a `WARN`, then `UNKNOWN` — the pipeline never dies
  because the classifier did. All prompts live in `app/prompts.py`.
- [x] **Step 3 — Extraction**: every document that passed the gate gets one LLM read
  capturing everything on it — names, amounts, dates, line items, per-field
  confidences — **in whatever structure the model finds natural** (deliberately no
  enforced schema; only a two-part envelope `{extraction_confidence, content}` is
  validated, never the content). Stub documents store their `content` verbatim at
  confidence 1.0 with zero LLM calls. Low-confidence reads WARN quoting the model's
  own notes; a failed read attaches the document read-failed with a `SKIPPED` event
  and the claim keeps moving. Status: `DOCUMENTS_VERIFIED → EXTRACTED`.
- [x] **Step 4 — Cross-document consistency checks**: one text-only LLM call over
  all document reads + claim details + the member's roster entry (family floater:
  the patient may be a registered dependent). Code decides *which* of the six fixed
  checks apply (a claim with no prescription is never asked doctor-consistency);
  the model answers exactly those, as strict verdicts. Patient mismatch (TC003) is
  the only hard stop — both names surfaced to the member; an initials-only match
  ("R. Kumar") flags the claim for forced manual review; everything else becomes a
  WARN that travels into Steps 5–6. Checker failure degrades with a SKIPPED event
  (this stage is `simulate_component_failure`'s target — TC011). Status:
  `EXTRACTED → CHECKED`.
- [x] **Step 5 — Policy decision**: two halves. **Decision Prep** (one LLM call)
  maps document content onto the policy's exact terms — "Bariatric Consultation" →
  "Obesity and weight loss programs", "T2DM" → the `diabetes` waiting-period key, a
  misspelled "Apolo Hospital" → "Apollo Hospitals" — with a confidence per mapping.
  The **Rules Engine** (pure code, no LLM) then applies the policy in a fixed,
  documented order: membership timing → exclusions (which outrank waiting periods)
  → condition waiting periods (rejections state the exact eligibility date) →
  pre-auth → line-item filtering → payable base = min(claimed, documented covered)
  → per-claim ceiling → **network discount before co-pay** → annual OPD headroom.
  Every arithmetic step writes a trace event with before/after numbers. Identity
  doubt from Step 4 forces `MANUAL_REVIEW` with the computed outcome attached; a
  degraded pipeline keeps its decision plus a "manual review recommended" note
  (TC011). Prep failure → `MANUAL_REVIEW`, never a crash. Status:
  `CHECKED → DECIDED`.
- [x] **Step 6 — Fraud check**: the last gate, after the decision — should a human
  look before money moves? **Threshold checks are pure code** (claims per day /
  per month, claim value vs the auto-review line — counting is not a judgment
  call), each tracing its actual numbers; TC009's 4-same-day-claims pattern routes
  to `MANUAL_REVIEW` with the signal named in the output. The **Fraud Assessor**
  (one LLM call) weighs the soft signals collected through the pipeline — only
  invoked when there is something to weigh; its routing threshold is fetched from
  the policy at call time, never hardcoded. Overrides attach the computed policy
  outcome for the reviewer; an already-`REJECTED` claim is never overridden (no
  payment to protect); **fraud never auto-rejects**. Status:
  `DECIDED → FINALIZED`.
- [x] **UI** — a dependency-free single page at `/ui/` (the root redirects there):
  a submission form whose member/category/document-type dropdowns come from
  `GET /policy/meta`, per-file declared-type selection, a one-click runner for the
  12 bundled test scenarios, and an ops review view showing every claim's
  decision, money/line-item breakdowns, fraud signals, documents with extracted
  content, and the full color-coded trace from intake to FINALIZED.
- [x] **Eval runner** (`scripts/run_eval.py`) — feeds all 12 test cases through
  the same in-process service the API uses and writes `eval_report.md`: a summary
  table plus, per case, expected-vs-actual fields, an automated check **with
  quoted evidence** for every `system_must` requirement, an auto-generated
  "why it didn't match" section for failures, the full decision output, and the
  full trace. Exit code 1 on any failure (CI-friendly).
- [ ] Architecture document, component contracts

## Eval report

```bash
ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_eval.py
```

writes `eval_report.md` at the project root. Stub documents keep the classifier
and reader LLM-free (deterministic); the consistency checker, decision prep, and
fraud assessor are live LLM calls. Run keyless and the semantic stages degrade by
design — the report still generates, stamped with a warning, with the
deterministic cases (TC001, TC002, TC009) passing.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

(Python 3.11+.) An Anthropic API key is needed **only for classifying real file
uploads** — export `ANTHROPIC_API_KEY` before starting the server. Everything else
— the whole test suite and all stub-document submissions (the test-case format) —
runs without a key. Without a key, real uploads degrade by design: the classifier
fails, the stage falls back to the declared document types with `WARN` trace events,
and the claim keeps moving.

## Run

```bash
.venv/bin/python -m uvicorn app.main:app --reload
```

Then open **http://localhost:8000/** — the UI has two tabs:

- **Submit a claim**: dropdowns driven by the policy file, file upload with
  optional per-file declared types (the classifier's fallback), a
  simulate-component-failure toggle for the resilience demo, and a runner for the
  12 bundled test scenarios.
- **Review claims**: the ops view — every claim with its decision, approved
  amount, money and line-item breakdowns, fraud signals, extracted document
  content, and the full trace, color-coded by PASS / FAIL / WARN / SKIPPED.

## Test

```bash
.venv/bin/python -m pytest -q
```

## Try it

Form metadata (dropdown data for the UI, straight from the policy file):

```bash
curl -s localhost:8000/policy/meta | python3 -m json.tool
```

Submit a valid claim (JSON endpoint; documents here are test-style stubs — real file
uploads go to `POST /claims` as multipart):

```bash
curl -s -X POST localhost:8000/claims/json -H 'Content-Type: application/json' -d '{
  "member_id": "EMP001",
  "policy_id": "PLUM_GHI_2024",
  "claim_category": "CONSULTATION",
  "treatment_date": "2024-11-01",
  "claimed_amount": 1500,
  "submission_date": "2024-11-05",
  "documents": [
    {"file_id": "F001", "file_name": "rx.jpg", "actual_type": "PRESCRIPTION"},
    {"file_id": "F002", "file_name": "bill.jpg", "actual_type": "HOSPITAL_BILL"}
  ]
}' | python3 -m json.tool
```

→ `"status": "EXTRACTED"` plus a trace event for every intake check, every document
classification, and every document read.

Trip the early gate (TC001's scenario — two prescriptions where a hospital bill is
required):

```bash
curl -s -X POST localhost:8000/claims/json -H 'Content-Type: application/json' -d '{
  "member_id": "EMP001",
  "policy_id": "PLUM_GHI_2024",
  "claim_category": "CONSULTATION",
  "treatment_date": "2024-11-01",
  "claimed_amount": 1500,
  "submission_date": "2024-11-01",
  "documents": [
    {"file_id": "F001", "file_name": "dr_sharma_prescription.jpg", "actual_type": "PRESCRIPTION"},
    {"file_id": "F002", "file_name": "another_prescription.jpg", "actual_type": "PRESCRIPTION"}
  ]
}' | python3 -m json.tool
```

→ `"status": "NEEDS_RESUBMISSION"`, decision `null`, and a problem that names both
sides: *"You uploaded 2 prescriptions. A CONSULTATION claim needs a prescription and
a hospital bill. Please upload the hospital bill for this visit."*

To exercise the real vision path, generate mock documents and upload them:

```bash
.venv/bin/python scripts/make_mock_docs.py
export ANTHROPIC_API_KEY=sk-ant-...
curl -s -X POST localhost:8000/claims \
  -F member_id=EMP001 -F policy_id=PLUM_GHI_2024 -F claim_category=CONSULTATION \
  -F treatment_date=2026-06-01 -F claimed_amount=1500 \
  -F files=@mock_documents/prescription.jpg -F files=@mock_documents/hospital_bill.jpg \
  | python3 -m json.tool
```

Submit a bad claim and get a specific error (HTTP 422, all problems at once):

```bash
curl -s -X POST localhost:8000/claims/json -H 'Content-Type: application/json' -d '{
  "member_id": "EMP099",
  "policy_id": "PLUM_GHI_2024",
  "claim_category": "MASSAGE",
  "treatment_date": "2024-11-01",
  "claimed_amount": 300,
  "submission_date": "2024-11-05",
  "documents": [{"file_id": "F001", "file_name": "rx.jpg"}]
}' | python3 -m json.tool
```

Read a claim back with its full trace:

```bash
curl -s localhost:8000/claims/<claim_id> | python3 -m json.tool
```

Notes:

- `submission_date` is optional (defaults to today). It exists so the dated test
  cases in `test_cases.json` evaluate deterministically — e.g. the 30-day submission
  window is checked against it.
- Intake checks run server-side even though the UI will use dropdowns: the API can be
  called directly, so clients validate for convenience, the server for correctness.

## Layout

```
app/
  models.py            # ClaimSubmission, ClaimRecord, TraceEvent, ... (Pydantic everywhere)
  config.py            # all system knobs, typed (single source of tunables)
  errors.py            # error taxonomy: *_CALL_FAILED (provider error kept verbatim) vs *_BAD_OUTPUT
  prompts.py           # every LLM prompt, in one place, for easy tuning
  llm.py               # Anthropic SDK wrapper: schema-validated calls, retry + failure discipline
  sources.py           # document source adapter: real uploads vs test-case stubs
  policy_store.py      # loads + validates policy_terms.json at boot; all policy reads go through it
  storage.py           # SQLite behind a small repository interface
  service.py           # the one claim-processing entry point (API + eval share it)
  evaluation.py        # eval harness: per-case field + system_must checks, report rendering
  api.py               # POST /claims, POST /claims/json, GET /claims[/{id}], GET /policy/meta
  main.py              # app factory + agent wiring
  agents/
    classifier.py      # Document Classifier (vision): type + readability, strict schema
    reader.py          # Document Reader (vision): flexible content, envelope-only validation
    consistency.py     # Consistency Checker (text): fixed checks, strict verdicts, completeness-enforced
    prep.py            # Decision Prep (text): semantic mapping onto exact policy terms
    fraud_assessor.py  # Fraud Assessor (text): weighs soft signals; threshold fetched from policy
  pipeline/
    intake.py          # the front-door checks (each writes a PASS/FAIL trace event)
    runner.py          # stage orchestrator with the skip-on-failure rule
    document_check.py  # the early gate: concurrent classification + requirement check
    extraction.py      # concurrent reads; stub passthrough; read failures degrade
    consistency_checks.py  # check selection (code) + verdict routing (code); TC003 gate
    policy_decision.py # prep wiring, low-confidence mapping warns, prep-failure fallback
    rules_engine.py    # the policy applied in fixed order, pure code, every step traced
    fraud_check.py     # threshold checks (code) + assessor + override routing; end of pipeline
ui/                    # dependency-free single page: submission form + ops review (full trace)
scripts/
  run_eval.py          # CLI: run the 12 cases, write eval_report.md
  make_mock_docs.py    # renders sample Indian medical documents (incl. a blurry one)
policy_terms.json      # the policy (single source of truth for every rule)
tests/                 # 126 tests: every stage, every gate, every graded scenario, eval harness, API, UI
```
