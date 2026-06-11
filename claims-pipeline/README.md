# Claims Pipeline

Health insurance claims processing system (Plum AI Engineer assignment).

The pipeline is built one stage at a time. Each stage reads and writes one shared
object — the **Claim Record** — which carries the submission, everything learned so
far, and a **trace**: a list of events saying what was checked and what happened.

```
Intake  →  Document Check  →  Extraction  →  Cross-Doc Checks  →  Policy Decision  →  Fraud Check
(done)     (done)             (next)
```

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
- [ ] Step 3 — Extraction
- [ ] Step 4 — Cross-document consistency checks
- [ ] Step 5 — Policy decision (rules engine)
- [ ] Step 6 — Fraud check
- [ ] UI, eval report over the 12 test cases, architecture document

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

→ `"status": "DOCUMENTS_VERIFIED"` plus a trace event for every intake check and
every document classification.

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
  api.py               # POST /claims, POST /claims/json, GET /claims[/{id}], GET /policy/meta
  main.py              # app factory + agent wiring
  agents/
    classifier.py      # Document Classifier (vision): type + readability, strict schema
  pipeline/
    intake.py          # the front-door checks (each writes a PASS/FAIL trace event)
    runner.py          # stage orchestrator with the skip-on-failure rule
    document_check.py  # the early gate: concurrent classification + requirement check
scripts/
  make_mock_docs.py    # renders sample Indian medical documents (incl. a blurry one)
policy_terms.json      # the policy (single source of truth for every rule)
tests/                 # 54 tests: intake, policy store, runner, document gate, API
```
