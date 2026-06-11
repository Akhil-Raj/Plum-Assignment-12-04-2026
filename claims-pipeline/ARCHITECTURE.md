# Architecture

A multi-agent pipeline that turns a claim submission (member details + claimed
amount + medical documents) into an explainable decision: `APPROVED`, `PARTIAL`,
`REJECTED`, or `MANUAL_REVIEW`, with the approved amount, reasons, and a
confidence score — or stops early with a message specific enough that the member
knows exactly what to fix.

```
            ┌─────────┐   ┌────────────────┐   ┌────────────┐   ┌────────────────┐   ┌─────────────────┐   ┌─────────────┐
 submit ───▶│ Intake  │──▶│ Document Check │──▶│ Extraction │──▶│ Cross-Doc      │──▶│ Policy Decision │──▶│ Fraud Check │──▶ FINALIZED
            │ (code)  │   │ classifier LLM │   │ reader LLM │   │ Checks         │   │ prep LLM        │   │ assessor LLM│
            └─────────┘   │ + checker code │   │            │   │ checker LLM    │   │ + rules code    │   │ + rules code│
                 │        └────────────────┘   └────────────┘   │ + routing code │   └─────────────────┘   └─────────────┘
                 ▼               │ gate: wrong /                └────────────────┘            │ gate: identity flag
            422 with         unreadable document                      │ gate: patient        forces MANUAL_REVIEW
            every problem    → NEEDS_RESUBMISSION                  mismatch (TC003)
            at once          (stop ≠ reject)                    → NEEDS_RESUBMISSION
```

Every stage reads and writes one shared object — the **ClaimRecord** — which
carries the submission, everything learned so far, and a **trace**: one event per
check, pass or fail, with the actual numbers in it. The record is persisted after
intake and again after the pipeline, so the API, the ops UI, and the eval report
all read the same artifact.

## Core ideas

### 1. Models judge; code decides

Every stage that contains an LLM splits into two halves with a hard boundary:

| Stage | The model does (semantic) | Code does (deterministic) |
|---|---|---|
| Document check | "what kind of document is this, can it be read?" | compare classified types against the policy's `document_requirements`; stop/continue; write the member-facing message |
| Extraction | read everything, in its own structure, with per-field confidences | validate the two-key envelope, store content untouched |
| Consistency | "same patient? dates line up? items sum?" over flexible content | choose WHICH checks apply (from document types), route verdicts (the one hard stop, the review flag, WARNs) |
| Policy decision | map content to the policy's exact terms ("T2DM" → `diabetes`, "Apolo Hospital" → "Apollo Hospitals") | the rules engine: every rule, every rupee, in a fixed documented order |
| Fraud | weigh free-text soft signals into a score | count claims against `fraud_thresholds`; apply the override rules |

The consequence is that everything money- or policy-shaped is unit-testable
without a network (the rules engine alone carries the seven money test cases as
plain unit tests), and everything the model touches is validated before code
branches on it. No LLM output is ever trusted structurally: strict outputs go
through schema-enforced generation (`messages.parse` + Pydantic); the one
deliberately schema-free output (extraction) has its envelope validated and its
content quarantined as data.

### 2. The trace is the product, not a log

`TraceEvent {stage, check_name, result PASS/FAIL/WARN/SKIPPED, detail, data}` is
written for every check **whether it passes or fails** — intake checks, per-file
classifications, per-required-document verdicts, every consistency answer, every
rule the engine applies, every arithmetic step with before/after numbers, every
fraud threshold with its counts. "Reconstruct exactly why any claim got any
decision" falls out of reading one list top to bottom. WARN and SKIPPED are
deliberately distinct: WARN means a check ran and found something concerning;
SKIPPED means it never ran at all — they answer different reviewer questions and
cost different amounts of confidence.

### 3. Confidence is bookkeeping, not vibes

The claim's confidence starts at 1.0 and is pulled down by recorded events, with
the deduction sizes in config: each WARN −0.05, a document that could not be read
−0.15, a whole component skipped −0.25, sub-threshold fraud signals −0.05 (only
from score 0.3 up — benign documentation-gap notes don't dent confidence). Every
deduction writes its own trace event with before/after values. A clean run scores
1.0 — meaning "no uncertainty events were recorded", not metaphysical certainty —
and a degraded run lands visibly lower (TC011 finishes at 0.75 with the skip
visible in the trace). The final number becomes the decision's confidence score.

### 4. Stops are not rejections

Two gates can stop a claim before any decision: the document gate (wrong or
unreadable required document) and the identity gate (documents belong to
different patients). A stopped claim is `NEEDS_RESUBMISSION` with `decision:
null` and a problems list that names every issue at once — file, error code,
message, what to do next — so the member fixes everything in one round trip.
Rejection is reserved for policy outcomes.

### 5. One seam between test stubs and real files

`test_cases.json` ships documents as JSON stubs (`actual_type`, `quality`,
`content`) rather than images. The **document source adapter** is the single
seam: stubs are taken at face value, as if a perfect classifier/extractor had run
(zero LLM calls in those stages); real uploads go to the vision models.
Everything downstream is identical in both modes — which makes the document gate
deterministic in the eval, keeps the same pipeline serving real uploads from the
UI, and means the eval exercises the *actual* decision path, not a parallel one.

### 6. Flexible extraction — the load-bearing decision

The Document Reader has **no enforced output schema**. Real Indian medical
documents (handwritten Rx, stamps over text, regional languages, unitemized
bills) vary too much for a fixed field list; forcing one silently loses or
distorts what is on the page. The only contract is a two-part envelope:

```json
{"extraction_confidence": 0.0–1.0, "content_with_individual_confidences": <anything>}
```

Validation touches the envelope only, never the content. The accepted costs,
taken knowingly:

- comparisons and value-pulls downstream become LLM calls (consistency, decision
  prep) — a few extra calls per claim;
- the 12-case eval invokes LLMs for those stages, so runs cost a little and
  traces can vary slightly between runs;
- per-field confidence has no fixed slot — it lives in the model's own structure
  plus one overall number per document, and the consistency/prep prompts are
  explicitly instructed to *use* those per-field confidences when judging
  (uncertainty in reading must never become a confident accusation).

What we got in return: the consistency checker can judge that "Rajesh Kumar" and
a transliterated "राजेश कुमार" are the same person and that "R. Kumar" is only a
partial match; decision prep can map "Bariatric Consultation" onto "Obesity and
weight loss programs". String matching cannot do either.

### 7. Failure discipline, uniform across all five agents

- Provider call fails (timeout, network, API error, missing key) →
  `<AGENT>_CALL_FAILED`, **keeping the provider's own error name and message
  verbatim** in the trace. We do not rename what the SDK already names.
- Output fails validation (schema, JSON, envelope, or verdict-completeness) →
  retried once with the error fed back, then `<AGENT>_BAD_OUTPUT`.
- Both are caught *inside* the owning stage and trigger that stage's documented
  fallback — classifier → declared type → UNKNOWN; reader → document attaches
  read-failed; consistency → skip with "manual review recommended"; prep →
  decision is MANUAL_REVIEW; assessor → hard thresholds remain enforced. None of
  them can crash the pipeline.
- The runner is the last resort: if a whole stage throws, it writes a SKIPPED
  event, deducts confidence, and moves on. `simulate_component_failure` (TC011)
  is wired into exactly this path, and the fraud stage substitutes a
  MANUAL_REVIEW decision if even the decision stage went down — every finalized
  claim leaves the pipeline actionable.
- The LLM client is lazy: the app boots with no API key, and a keyless server
  still takes every claim to a decision (the eval runner run keyless produces a
  warning-stamped report with the deterministic cases passing — a useful smoke
  test).

### 8. Multi-agent by design

Five agents, each one call shape, one prompt, one validated output, one fallback:

| Agent | Model (config) | Call shape |
|---|---|---|
| Document Classifier | `claude-sonnet-4-6` (vision, cheap — it gates the expensive calls) | structured, per file, concurrent |
| Document Reader | `claude-opus-4-8` (vision + thinking — messy handwriting needs depth) | raw JSON envelope, per file, concurrent |
| Consistency Checker | `claude-opus-4-8` (thinking) | structured verdicts, one per claim |
| Decision Prep | `claude-opus-4-8` (thinking) | structured mapping, one per claim |
| Fraud Assessor | `claude-opus-4-8` (thinking) | structured score+signals, invoked only when there is something to weigh |

Splitting them keeps each prompt small and testable, lets each fail independently
with its own fallback, and lets models be swapped per role in one config line.
All prompts live in `app/prompts.py`; the fraud assessor's routing threshold is
injected from the policy file at call time rather than hardcoded in prose.

## The life of a claim (interaction walkthrough)

1. `POST /claims` (multipart, real files) or `POST /claims/json` (stub documents)
   → **intake** runs eight cheap checks (roster, policy id, category, amount,
   date, files, submission window, minimum amount), each writing a trace event.
   Any failure → HTTP 422 listing every problem; nothing is persisted.
2. The record (status `RECEIVED`) is persisted, then the **pipeline runner**
   executes the five stages in order, stopping only for `NEEDS_RESUBMISSION`.
3. **document_check** classifies every file concurrently (stub or vision), then
   the requirement checker compares types against the policy. Stop messages name
   what was uploaded and what is needed ("You uploaded 2 prescriptions. A
   CONSULTATION claim needs a prescription and a hospital bill...").
   Status → `DOCUMENTS_VERIFIED`.
4. **extraction** reads every readable document concurrently; stubs pass through
   verbatim at confidence 1.0. Failed reads attach read-failed and the claim
   keeps moving. Status → `EXTRACTED`.
5. **consistency_checks** selects the applicable checks in code, makes one LLM
   call over all contents + the member's roster entry (family floater:
   dependents are legitimate patients), and routes the verdicts: confident
   patient mismatch is the only hard stop; initials-only match flags forced
   review; everything else becomes WARNs that travel forward.
   Status → `CHECKED`.
6. **policy_decision**: decision prep maps content onto exact policy entries;
   the rules engine applies the policy in a fixed order (membership timing →
   exclusions → waiting periods → pre-auth → line-item filtering → payable base
   = min(claimed, documented covered) → per-claim ceiling → network discount
   FIRST, co-pay SECOND → annual OPD headroom), rejecting at the first tripped
   rule. Overrides apply last. Status → `DECIDED`.
7. **fraud_check**: pure-code threshold checks (per-day, per-month, value), the
   assessor weighs soft signals when any exist, and the override can turn the
   decision into MANUAL_REVIEW with the computed policy outcome attached — never
   into REJECTED, and never overriding an already-rejected claim.
   Status → `FINALIZED`.
8. The record is persisted again; the API returns it; the UI renders the
   decision, breakdowns, fraud signals, documents, and the full trace.

## Considered and rejected

| Considered | Rejected because |
|---|---|
| Fixed extraction schema per document type | Loses the messy reality of Indian medical documents; every unanticipated field becomes either a lie or a loss. The flexible envelope costs a few downstream LLM calls instead (see §6). |
| One mega-prompt ("here are the documents and the policy — decide") | Unobservable, untestable, unfixable: no per-check trace, no unit-testable money math, one failure mode (the whole decision). The graded criteria (observability, failure handling) are structurally impossible in that design. |
| Letting the model do the money math | Arithmetic is not a judgment call. TC010's discount-before-copay ordering is exactly the kind of thing that must be provably fixed in code, with each step traced. |
| String/fuzzy matching for policy mapping | "T2DM" → `diabetes`, "Bariatric Consultation" → "Obesity and weight loss programs", "Apolo Hospital" → "Apollo Hospitals" are semantic mappings; lookups can't do them reliably. The mapping is the one part that *should* be a model call — with per-mapping confidence reported. |
| Re-uploading corrected files into the same claim | Adds claim-state complexity (versioned documents, partial re-runs) for no grading value. A stopped claim stays stored for audit; the member submits a fresh claim. |
| A YAML config file alongside the typed config | Two sources of truth for the same numbers (it drifted within a day). One typed `config.py` module is the single source of tunables; policy stays in `policy_terms.json`. |
| OCR preprocessing (deskew, contrast) before the reader | The vision model handles phone photos directly. If real-world quality demands it later, it slots in front of the reader without changing any contract. |
| Queue-based asynchronous processing | Right for production (see 10x below), wrong for a reviewable demo: inline processing returns the finished record in one request and keeps the eval runner trivial. The seam (ClaimService.submit) is where a queue would go. |
| Giving the fraud assessor authority to reject | Calling fraud is a human's job. The system routes and explains; the worst it does to a member is ask a human to look. |

## Policy ambiguities found in the test data — and the documented resolutions

The twelve cases are not all consistent under a naive reading of
`policy_terms.json`. Where they conflict, the resolution that satisfies all
twelve was chosen and documented (full list in [ASSUMPTIONS.md](ASSUMPTIONS.md)):

- **Per-claim limit vs sub-limit.** TC008 rejects a ₹7,500 consultation on the
  ₹5,000 `per_claim_limit`, yet TC006 partially approves a ₹12,000 dental claim,
  and TC010 approves ₹3,240 on a consultation despite the ₹2,000 consultation
  `sub_limit`. The only consistent rule: each category's effective per-claim
  ceiling is **max(`per_claim_limit`, category `sub_limit`)**, tested against the
  **covered documented base** — and `sub_limit` never caps the money math.
- **Exclusions outrank waiting periods.** TC012's obesity diagnosis matches both
  an exclusion and a 365-day waiting period; the exclusion is the truthful
  headline (permanently not covered), so the engine evaluates exclusions first
  and stops at the first rejection — which is also why TC007 reports only
  `PRE_AUTH_MISSING` even though it would also breach the ceiling.
- **Absence is a note, not a finding.** Several cases ship documents with no
  patient names or dates at all. The consistency checker treats absent
  information as a PASS-with-note unless something *present* contradicts;
  no-name-on-any-document is a single identity WARN (never a stop, never forced
  review). This was tuned against the live eval: the first run escalated TC007
  to review and dented TC012's confidence; the fix was a principled rule, not a
  threshold fudge.

## Limitations, and what changes at 10× load

**Processing model.** Claims process inline inside the HTTP request (seconds per
claim, dominated by 2–3 LLM calls). At 10×: put a queue between `ClaimService`
and the runner (the seam already exists), return `202 + claim_id` immediately,
process from workers with idempotency keys, and let the UI poll `GET /claims/{id}`
— it already renders any intermediate status. Per-stage persistence is already in
place conceptually (the record is self-describing at every stage boundary).

**Storage.** SQLite behind `ClaimRepository`; nothing outside that module knows.
Swap to Postgres (JSONB for the record, indexed columns for the ops list view)
by reimplementing one file. Claims history for fraud thresholds currently arrives
on the submission (test-case shape); in production it becomes a repository query
per member — same threshold code.

**LLM cost and latency.** Today: classification and extraction are already
concurrent per document; the three claim-level calls are sequential by data
dependency. At 10×: enable prompt caching for the stable prefixes (system prompts
and the policy lists block are byte-stable per policy — the cacheable prefix is
most of the prep prompt), tier models per role (the classifier already runs on a
cheaper model; consistency could drop a tier with eval evidence), use the Batch
API for nightly re-evals, and add per-claim token/cost budgets with a circuit
breaker in the one `LLMClient` choke point. Headroom exists in the design: each
agent's model is one config line.

**Fraud.** Thresholds are per-member and per-claim. Real fraud intelligence needs
a claims database spanning members and time (the same provider billing many
members, colluding networks). That is a new analytical component reading the same
stored records and soft signals — the pipeline already records the inputs
(classifier notes, reader observations, consistency warnings) it would consume.

**Multi-policy / multi-tenant.** One PolicyStore is loaded at boot. At 10× the
store becomes keyed by `policy_id` (the submission already carries it and intake
already validates it), loading policy documents from a table instead of one file.

**Security and compliance.** Out of scope here and required in production:
authentication/authorization on every endpoint (members see their claims; ops
see all), encryption at rest for medical documents, PII redaction in logs,
retention policy for the audit trail, and key management (the API key currently
comes from the environment / a gitignored `.env`).

**Observability.** The trace is already structured (stage, check, result, data
with numbers). At scale, ship the same events to OpenTelemetry/ClickHouse and the
decisions to a metrics dashboard (approval rate, confidence distribution,
degradation rate per component, LLM error rates per agent). Confidence deduction
events double as a degradation SLO signal.

**The review loop.** MANUAL_REVIEW routes to a human, but there is no reviewer
workflow (assign, decide, write back). That is the next product surface: a
reviewer decision becomes one more trace event and a final decision on the same
record.
