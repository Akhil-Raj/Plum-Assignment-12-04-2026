# Component Contracts

The interface of every significant component: what it accepts, what it produces,
what errors it can raise, and what it guarantees — precise enough to reimplement
any single component without reading its code. Shared data models are defined
once in `app/models.py` and referenced here by name.

Conventions used throughout:

- **Agent error discipline.** Every LLM-backed agent raises exactly two errors:
  `<AGENT>_CALL_FAILED` (the provider call itself failed; the provider's own
  error class and message are preserved verbatim in the text) and
  `<AGENT>_BAD_OUTPUT` (the call succeeded but the content failed validation
  after `config.llm.bad_output_retries` retries). Both are raised as
  `AgentCallFailed` / `AgentBadOutput` (subclasses of `AgentError`) and are
  **always caught by the owning stage** — they never escape to the runner.
- **Stages never throw.** Every pipeline stage degrades internally per its
  contract; the runner's catch-all is a last resort for unexpected bugs.
- **Money** is INR, formatted in member-facing text as `₹1,234` (`format_inr`).

---

## Data models (shared vocabulary)

| Model | Fields (essentials) |
|---|---|
| `ClaimSubmission` | `member_id`, `policy_id`, `claim_category`, `treatment_date`, `claimed_amount`, `documents[]`, optional `ytd_claims_amount`, `claims_history[]`, `hospital_name`, `simulate_component_failure`, `submission_date` (defaults to today; settable for deterministic evaluation) |
| `UploadedDocument` | `file_id`, `file_name?`, `declared_type?`, `stored_path?` (real files) — or stub fields `actual_type?`, `quality?`, `content?` + arbitrary extra fields (kept) |
| `ClaimRecord` | `claim_id`, `status`, `claimed_amount`, `currency`, `submission`, `trace[]`, `classifications[]`, `reads[]`, `verdicts[]`, `soft_signals[]`, `problems[]`, `manual_review_required` + `manual_review_reasons[]`, `skipped_components[]`, `prep?`, `decision?`, `fraud?`, `confidence` |
| `TraceEvent` | `stage`, `check_name`, `result ∈ {PASS, FAIL, WARN, SKIPPED}`, `detail` (human sentence), `data?` (numbers), `timestamp` |
| `Problem` | `error_code`, `message`, `what_to_do_next`, `file_id?`, `file_name?` |
| `DocumentClassification` | `file_id`, `detected_type ∈ DocType`, `confidence 0–1`, `quality ∈ {GOOD, POOR, UNREADABLE}`, `evidence`, `notes?`, `source ∈ {llm, stub, declared_fallback, unknown_fallback}` |
| `DocumentRead` | `file_id`, `doc_type`, `extraction_confidence 0–1`, `content` (any JSON, never reshaped), `read_failed`, `failure_reason?` |
| `CheckVerdict` | `check_id`, `result ∈ {PASS, FAIL, WARN, MANUAL_REVIEW}`, `confidence 0–1`, `explanation`, `evidence` |
| `PrepResult` | `line_items[] {description, amount, coverage ∈ {COVERED, EXCLUDED, REQUIRES_PRE_AUTH}, matched_policy_entry?, confidence}`, `documented_total?`, `diagnosis {raw_diagnosis?, excluded_condition?, waiting_period_key?, confidence}`, `hospital {hospital_name_found?, matched_network_hospital?, confidence}`, `treatment_date_iso?`, `pre_auth_reference_found`, `notes?` |
| `Decision` | `decision ∈ {APPROVED, PARTIAL, REJECTED, MANUAL_REVIEW}`, `approved_amount`, `currency`, `reasons[] {code, detail}`, `rejection_reasons[]` (machine codes), `line_item_breakdown[]`, `money_breakdown[] {step, description, amount_before, amount_after}`, `confidence`, `manual_review_recommended`, `manual_review_notes[]`, `eligibility_date?`, `what_to_do_next?`, `computed_policy_outcome?` (a nested `Decision`) |
| `FraudAssessment` | `fraud_score 0–1`, `signals[] {name, severity ∈ {LOW, MEDIUM, HIGH}, explanation}`, `source ∈ {llm, skipped}` |

Status lifecycle:
`RECEIVED → DOCUMENTS_VERIFIED → EXTRACTED → CHECKED → DECIDED → FINALIZED`,
with `NEEDS_RESUBMISSION` as the terminal stop from either gate.

---

## PolicyStore (`app/policy_store.py`)

- **In:** path to `policy_terms.json`.
- **Out:** typed lookups — `policy_id`, `policy_name`, `currency`, `members()`,
  `get_member(id) -> Member|None`, `get_dependents(id) -> list[Member]`,
  `claim_categories()` (upper-case), `is_valid_category(s)`,
  `get_document_requirements(category) -> {required[], optional[]}`,
  `get_category_rules(category) -> dict`, plus raw chunks: `coverage`,
  `submission_rules`, `waiting_periods`, `exclusions`, `pre_authorization`,
  `network_hospitals`, `fraud_thresholds`.
- **Errors:** `PolicyFileInvalid` — missing file, invalid JSON, missing required
  top-level keys, invalid member roster, or a `document_requirements` category
  with no matching `opd_categories` entry. **Boot-time only**; never mid-claim.
- **Guarantees:** every policy value used anywhere in the system is read through
  this component; no policy rule lives in code.

## Config (`app/config.py`)

- **In:** none (typed defaults; a gitignored `.env` is loaded with
  real-environment-wins semantics).
- **Out:** `AppConfig` — model names and max_tokens per agent, timeouts, retry
  counts, warn thresholds, confidence deduction sizes, file caps, paths, the
  simulated-failure stage name. `resolve(path)` anchors relative paths at the
  project root.
- **Errors:** none at load (values are typed defaults).
- **Guarantees:** the single source of every tunable that is *ours*; no magic
  numbers in logic code.

## LLM client (`app/llm.py`)

- **In:** `LLMConfig`. Two calls:
  - `structured_call(agent, model, max_tokens, system, messages, schema, thinking)
    -> schema instance` — generation constrained to the Pydantic schema via
    `messages.parse`.
  - `raw_json_call(agent, model, max_tokens, system, messages, thinking,
    validate?) -> Any` — response parsed as JSON (code fences stripped);
    `validate(parsed) -> error|None` failures are fed back to the model for the
    retry.
- **Errors:** `AgentCallFailed(agent, provider_error)` for any provider failure
  (including a missing API key — the client is constructed lazily so the app
  boots keyless); `AgentBadOutput(agent, detail)` after
  `1 + bad_output_retries` attempts.
- **Guarantees:** the only module that talks to the Anthropic SDK; per-call hard
  timeout and SDK retry counts from config.

## Document source adapter (`app/sources.py`)

- **In:** an `UploadedDocument`.
- **Out:** `stub_classification(doc) -> DocumentClassification` (declared
  type/quality at confidence 1.0, source `stub`); `stub_read(doc, doc_type) ->
  DocumentRead` (the stub's `content` verbatim — or its loose extra fields, e.g.
  TC003's `patient_name_on_doc` — at extraction confidence 1.0);
  `file_content_block(doc) -> dict` (base64 image block, or a native document
  block for PDFs so multi-page bills go in one call).
- **Errors:** `FileNotFoundError` if a real document has no stored file (caught
  by the calling stage's fallback).
- **Guarantees:** the single seam between test stubs and real uploads;
  everything downstream is identical in both modes.

## Intake (`app/pipeline/intake.py`)

- **In:** `ClaimSubmission`, PolicyStore, AppConfig.
- **Out:** `(trace_events[], problems[])` — eight checks, each writing a
  PASS/FAIL event: member in roster, policy id matches, category valid, amount
  positive, treatment date not in the future, ≥1 document with allowed
  type/size, submission window (`submission_rules.deadline_days_from_treatment`),
  minimum amount (`submission_rules.minimum_claim_amount`). **All failures are
  collected**, not first-only.
- **Error codes:** `MEMBER_NOT_FOUND`, `POLICY_MISMATCH`, `UNKNOWN_CATEGORY`,
  `INVALID_AMOUNT`, `INVALID_DATE`, `NO_DOCUMENTS`, `BAD_FILE`,
  `SUBMISSION_TOO_LATE`, `BELOW_MINIMUM_AMOUNT` (each as a `Problem` with a
  specific message and next step).
- **Guarantees:** cheap and LLM-free; messages name the exact value that failed
  and the policy value it failed against.

## Pipeline runner (`app/pipeline/runner.py`)

- **In:** ordered `(name, async stage_fn)` list; a `ClaimRecord`.
- **Out:** the same record after all stages; stops early only on
  `NEEDS_RESUBMISSION`.
- **Errors:** none escape. A stage that throws gets a SKIPPED trace event with
  the error verbatim, its name appended to `skipped_components`, and a
  `skipped_component_deduction` to confidence; the pipeline continues.
- **Guarantees:** `simulate_component_failure` injects a failure into the
  configured stage (`config.pipeline.simulated_failure_stage`) through exactly
  this path, so TC011 exercises the real resilience mechanism.

## Document Classifier agent (`app/agents/classifier.py`)

- **In:** one real `UploadedDocument` (bytes on disk).
- **Out:** `DocumentClassification` (strict schema: type from the 8-value
  taxonomy, confidence, quality, one-sentence evidence, fraud-relevant notes).
  It answers *what type and how readable* only — never extracts fields.
- **Errors:** `CLASSIFIER_CALL_FAILED` / `CLASSIFIER_BAD_OUTPUT` (caught by the
  stage).

## Document Check stage (`app/pipeline/document_check.py`)

- **In:** record with status `RECEIVED`.
- **Out:** classifications attached (stub or vision, concurrent across files;
  results applied in submission order so the trace is deterministic); per-file
  and per-required-type trace events; status `DOCUMENTS_VERIFIED`, or
  `NEEDS_RESUBMISSION` with problems.
- **Fallback chain** when the classifier fails for a file: member-declared type
  (source `declared_fallback`, WARN + deduction) → `UNKNOWN`
  (`unknown_fallback`); if a required document's type cannot be established, the
  claim stops naming the file we couldn't process.
- **Problem codes:** `WRONG_DOCUMENT_TYPE` (surplus/duplicates present),
  `MISSING_DOCUMENT`, `UNREADABLE_DOCUMENT` (stop messages name the uploaded and
  required types, or the exact unreadable file, and confirm which documents are
  fine).
- **Other behavior:** low-confidence classifications (< 
  `thresholds.classification_confidence_warn`) WARN + deduct; classifier `notes`
  are appended to `soft_signals` for the fraud stage; unreadable *optional*
  documents and unrelated types WARN but never stop.

## Document Reader agent (`app/agents/reader.py`)

- **In:** one real `UploadedDocument` + its detected type (guidance only).
- **Out:** `DocumentRead` — `extraction_confidence` from the model's own
  judgment plus `content` in whatever structure the model chose, stored
  untouched. `validate_envelope(parsed)` is the only validation (two keys,
  numeric 0–1 confidence).
- **Errors:** `EXTRACTOR_CALL_FAILED` / `EXTRACTOR_BAD_OUTPUT` (caught by the
  stage).
- **Prompt obligations:** attempt every field including regional-language and
  obscured ones, attach per-field confidences in its own structure, never invent
  values, explain difficulties in its own words, flag fraud-relevant oddities.

## Extraction stage (`app/pipeline/extraction.py`)

- **In:** record with status `DOCUMENTS_VERIFIED` (tolerates missing upstream
  data: if classifications are absent it degrades to declared/actual types).
- **Out:** `DocumentRead` per document (concurrent; stubs verbatim at 1.0;
  UNREADABLE-classified files skipped with a SKIPPED note — nothing to extract);
  status `EXTRACTED` **always**.
- **Failure behavior:** a failed read attaches the document read-failed with
  empty content, a SKIPPED trace event, and `read_failed_deduction`; reads below
  `thresholds.extraction_confidence_warn` WARN **quoting the model's own
  struggle notes** + deduct.

## Consistency Checker agent (`app/agents/consistency.py`)

- **In:** readable `DocumentRead`s, the submission, the member + dependents,
  the asked checks `[(check_id, question)]`, labels of unreadable documents.
- **Out:** exactly one `CheckVerdict` per asked check, in asked order.
  **Completeness is enforced**: missing/extra/duplicate check_ids are retried
  once with the error fed back, then `CONSISTENCY_BAD_OUTPUT` — an omission can
  never read as "didn't apply".
- **Errors:** `CONSISTENCY_CALL_FAILED` / `CONSISTENCY_BAD_OUTPUT` (caught by
  the stage).
- **Judgment rules in the prompt:** three-way patient-name rule (full-name or
  transliteration match → PASS; clearly different person → FAIL; initials-only →
  MANUAL_REVIEW); per-field extraction confidences must lower verdict
  confidence; absence is a note, not a finding (no name on ANY document → a
  single identity WARN, never FAIL/MANUAL_REVIEW).

## Consistency stage (`app/pipeline/consistency_checks.py`)

- **In:** record with status `EXTRACTED`.
- **Out:** verdicts attached + routed; status `CHECKED`, or
  `NEEDS_RESUBMISSION` with a `PATIENT_MISMATCH` problem (the only hard stop —
  message carries both names from the verdict's evidence).
- **Check selection (code, before the call):** `patient_identity`,
  `date_consistency`, `side_by_side` always; `amount_consistency` and
  `line_item_sums` only with a readable bill; `doctor_consistency` only with a
  prescription plus a doctor-referencing document. Unasked checks get a
  `check_not_applicable` trace event saying why.
- **Routing:** patient FAIL at confidence ≥
  `thresholds.name_mismatch_stop_confidence` → stop; patient FAIL below it →
  WARN + `manual_review_required` (a blurry name must not bounce a real claim);
  patient MANUAL_REVIEW → `manual_review_required` with the verdict's evidence;
  any other FAIL is defensively downgraded to WARN; WARNs append to
  `soft_signals` and deduct.
- **Degradation:** checker failure or nothing readable → SKIPPED event,
  `skipped_components += ["consistency_checks"]`, deduction, status still
  `CHECKED`. This stage is the `simulate_component_failure` target.

## Decision Prep agent (`app/agents/prep.py`)

- **In:** readable reads, submission, PolicyStore (it builds the policy block:
  category mapping fields, exclusions, waiting-period keys, network hospitals,
  pre-auth list).
- **Out:** `PrepResult` (strict schema; structured outputs). Mapping rules: by
  meaning, never stretched; exact policy entry named per mapping; one synthetic
  line item for unitemized bills ("Billed services (no itemization)").
- **Errors:** `PREP_CALL_FAILED` / `PREP_BAD_OUTPUT` (caught by the stage).

## Rules Engine (`app/pipeline/rules_engine.py`) — pure code

- **In:** `ClaimRecord` + `PrepResult` + PolicyStore + AppConfig.
- **Out:** a `Decision`, plus a trace event for every rule and every arithmetic
  step (with before/after numbers). Deterministic; fully unit-testable.
- **Fixed order, rejecting at the first tripped rule:** membership timing
  (cover start; initial waiting period) → claim-level exclusions (outrank
  waiting periods) → condition waiting periods (rejection states the exact
  eligibility date `join_date + waiting_days`) → pre-authorization (category
  `pre_auth_threshold`; satisfied by `pre_auth_reference_found`) → line-item
  filtering (excluded items drop with per-item reasons; all-excluded rejects) →
  payable base = min(claimed, documented covered total) → per-claim ceiling =
  **max(`per_claim_limit`, category `sub_limit`)** tested against the base →
  network discount FIRST → co-pay SECOND (on the discounted amount) → annual
  OPD headroom (cap, or reject when exhausted).
- **Overrides, applied last:** `manual_review_required` forces MANUAL_REVIEW
  with `computed_policy_outcome` attached; a degraded pipeline
  (`skipped_components` / read-failed documents) keeps its decision and sets
  `manual_review_recommended` + notes.
- **Rejection codes:** `MEMBERSHIP_NOT_ACTIVE`, `WAITING_PERIOD`,
  `EXCLUDED_CONDITION`, `PRE_AUTH_MISSING`, `PER_CLAIM_EXCEEDED`,
  `ALL_ITEMS_EXCLUDED`, `ANNUAL_LIMIT_EXHAUSTED`.
- **Errors:** none — missing inputs route to MANUAL_REVIEW by rule
  (`NO_DOCUMENTED_AMOUNTS`), not by exception.

## Policy Decision stage (`app/pipeline/policy_decision.py`)

- **In:** record with status `CHECKED` (tolerates earlier statuses after
  upstream skips).
- **Out:** `prep` attached, low-confidence load-bearing mappings WARNed and
  deducted (`thresholds.prep_mapping_confidence_warn`), `decision` attached,
  status `DECIDED` **always**.
- **Degradation:** prep failure or nothing readable → SKIPPED event +
  deduction + decision `MANUAL_REVIEW` with reason `PREP_FAILED` ("could not
  reliably read the values needed for an automatic decision"). Never a crash,
  never a guess.

## Fraud Assessor agent (`app/agents/fraud_assessor.py`)

- **In:** submission (incl. claims history), collected `soft_signals`, readable
  reads, a one-line summary of the computed decision, and the policy's
  manual-review threshold (injected into the prompt at call time).
- **Out:** `FraudAssessment` (strict schema: score 0–1 + named signals with
  severity and actionable explanations).
- **Errors:** `ASSESSOR_CALL_FAILED` / `ASSESSOR_BAD_OUTPUT` (caught by the
  stage).

## Fraud Check stage (`app/pipeline/fraud_check.py`)

- **In:** record with status `DECIDED` (if the decision is missing entirely, a
  MANUAL_REVIEW decision is substituted so the claim leaves the pipeline
  actionable).
- **Out:** threshold trace events with the actual counts; the assessor invoked
  **only when** soft signals or claims history exist; final decision possibly
  overridden; `record.decision.confidence` synced to the final claim
  confidence; status `FINALIZED` **always**.
- **Threshold checks (pure code, from `fraud_thresholds`):** same-day count
  (history entries on the treatment date + this claim) vs
  `same_day_claims_limit`; monthly count vs `monthly_claims_limit`; claimed
  amount vs `auto_manual_review_above`.
- **Override rules:** any threshold trip OR `fraud_score ≥
  fraud_score_manual_review_threshold` → MANUAL_REVIEW with the computed policy
  outcome attached and each triggering signal as a reason
  (`SAME_DAY_CLAIMS`, `MONTHLY_CLAIMS`, `HIGH_VALUE_CLAIM`, `FRAUD_SCORE`);
  sub-threshold signals → trace + a small confidence dip only from
  `fraud_signal_dip_min_score` up; an already-REJECTED claim is **never
  overridden**; fraud **never** auto-rejects.
- **Degradation:** assessor failure → SKIPPED + deduction; hard thresholds
  remain enforced; `fraud.source = "skipped"`.

## ClaimService (`app/service.py`)

- **In:** `ClaimSubmission`.
- **Out:** the finalized `ClaimRecord` (persisted as `RECEIVED` before the
  pipeline and again after).
- **Errors:** `IntakeRejected(problems[])` — the only exception the API layer
  handles; nothing is persisted for failed intake.
- **Guarantees:** the single processing entry point; the HTTP API and the eval
  runner share it, so the eval exercises exactly the production path.

## Storage (`app/storage.py`)

- **In/Out:** `save(record)` (upsert; full record as canonical JSON plus
  denormalized columns for the list view), `get(claim_id) -> ClaimRecord|None`,
  `list_claims() -> summaries`.
- **Errors:** none beyond `sqlite3` operational errors (single connection,
  thread-locked).
- **Guarantees:** nothing outside this module knows the backend is SQLite.

## HTTP API (`app/api.py`)

| Endpoint | In | Out | Errors |
|---|---|---|---|
| `POST /claims` | multipart form + files (optional per-file `declared_types` JSON array) | finalized `ClaimRecord` | 422 `{status: REJECTED_AT_INTAKE, errors[]}` with every problem at once |
| `POST /claims/json` | `ClaimSubmission` JSON (stub documents allowed) | finalized `ClaimRecord` | 422 as above; malformed bodies translated to `MALFORMED_REQUEST` problems |
| `GET /claims` | — | `{claims: [summaries]}` | — |
| `GET /claims/{id}` | — | full `ClaimRecord` | 404 |
| `GET /policy/meta` | — | dropdown data: members, categories, document types, requirements, submission rules | — |
| `GET /test-cases` | — | the 12 bundled scenarios (UI demo runner) | 404 if the file is absent |
| `GET /healthz` | — | `{status: ok}` | — |

A stopped claim (`NEEDS_RESUBMISSION`) is **HTTP 200** — the submission was
valid and processed; the result is a resubmission request, not a client error.

## Eval harness (`app/evaluation.py` + `scripts/run_eval.py`)

- **In:** `test_cases.json` (each case's `input`, with `submission_date` pinned
  to the treatment date).
- **Out:** `eval_report.md` — summary table; per case: expected-vs-actual field
  rows, an automated check with quoted evidence per `system_must` requirement,
  an auto-generated mismatch explanation on failure, the full decision JSON, and
  the full trace. Exit code 0 only on 12/12.
- **Errors:** a case that raises is reported as a failed case; the runner
  itself does not crash. Keyless runs complete with the degradation warning
  stamped into the report.
- **Guarantees:** runs in-process through `ClaimService` (the production path)
  against its own database (`data/eval_claims.db`).
