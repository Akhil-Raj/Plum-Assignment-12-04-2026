# Claims Pipeline — Step-by-Step Plan

## The whole system at a glance

The pipeline is built one stage at a time. Each step below adds one stage:

```
Intake  →  Document Check  →  Extraction  →  Cross-Doc Checks  →  Policy Decision  →  Fraud Check  →  Final Decision
(Step 1)    (Step 2)           (Step 3)        (Step 4)             (Step 5)            (Step 6)
```

Every stage reads and writes to one shared object: the **Claim Record**, which carries the input, everything learned so far, and a **trace** (a list of events saying what was checked and what happened).

---

# Step 1: Claim Intake

## What Step 1 delivers

A running service where you can submit a claim (member details + claim type + amount + files), and get back either a claim ID with status `RECEIVED`, or a clear, specific error saying exactly what was wrong with the submission. Nothing about documents' *contents* yet — that's Step 2 and 3.

## Stack

- **Python + FastAPI** — async out of the box, and Pydantic gives us strict, validated data models (the assignment grades data modeling and validated LLM output, so Pydantic everywhere pays off later).
- **SQLite** for storage now (one table for claims, one for trace events, one for uploaded files). Easy to swap for Postgres later; the storage layer hides behind a small interface so nothing else knows it's SQLite.
- Policy comes only from `policy_terms.json`, loaded at startup by a **PolicyStore** component. No policy rule lives in code.
- **A config file** (e.g., `config.yaml`) holds every tunable value that is *ours* rather than the policy's: LLM model name, confidence thresholds, call timeouts, retry counts, file size cap. No magic numbers in code; policy rules stay in `policy_terms.json`, system knobs stay in config.

## Components in Step 1

### 1. Data models
- `ClaimSubmission` — what the member sends: `member_id`, `policy_id`, `claim_category`, `treatment_date`, `claimed_amount`, list of documents. Optional fields the test cases use: `ytd_claims_amount`, `claims_history`, `hospital_name`, `simulate_component_failure`.
- `UploadedDocument` — file ID, file name, file bytes (or a stored path), declared type if the member states one.
- `ClaimRecord` — `claim_id`, `status` (starts at `RECEIVED`), `claimed_amount` (explicit top-level field — later stages like the decision engine and co-pay math read it constantly), the full submission, timestamps, and the trace.
- `TraceEvent` — `stage`, `check_name`, `result` (PASS / FAIL / WARN / SKIPPED), `detail` (human-readable sentence), `timestamp`. This one model is what makes every later decision explainable, so it's born in Step 1.

**Why both WARN and SKIPPED:** WARN means "this check ran and found something concerning" (e.g., a field read with low confidence). SKIPPED means "this check never ran at all" (the component crashed or timed out and the pipeline moved on). TC011 grades exactly this — the output must show that a component failed and was skipped, and confidence must drop differently for a check that never happened vs. a check that passed with a caveat.

### 2. PolicyStore
- Loads and validates `policy_terms.json` at startup. Fails loudly at boot if the file is broken — never mid-claim.
- Exposes simple lookups: `get_member(id)`, `get_document_requirements(category)`, `get_category_rules(category)`, etc.
- Later stages all read policy through this one component.

### 3. Intake API — `POST /claims`
Accepts a multipart request (JSON details + files). Runs **cheap, fast checks only** — things we can know without opening any document:

| Check | On failure, the error says |
|---|---|
| Member exists in roster | "Member ID EMP099 is not on this policy's member list." |
| Policy ID matches the loaded policy | Which ID was sent vs. which exists |
| Claim category is one the policy defines | The bad category and the list of valid ones |
| Amount is a positive number | What was received |
| Treatment date is valid and not in the future | The date received |
| At least one file; file types are jpg/png/pdf; size under a cap | Which file failed and why |
| Submission window (treatment date within 30 days, from policy `submission_rules`) | The deadline date that was missed |
| Minimum claim amount (₹500, from policy) | The minimum and the claimed amount |

Each check writes a trace event whether it passes or fails. On failure we return HTTP 422 with a structured error: `{error_code, message, what_to_do_next}`. The "specific, actionable message" requirement (graded at 10%) starts here and continues in Step 2.

**Why validate policy ID and claim category even though the UI uses dropdowns:** the dropdown only protects requests that come through our UI. The API is also called directly — the eval report runs the 12 test cases from `test_cases.json` as raw JSON payloads, and anyone with `curl` can send any value. Clients validate for convenience; the server validates for correctness. Both checks are simple dictionary lookups against PolicyStore, so they cost nothing.

Design choice to note: the last two checks are policy rules, but they're rules about the *submission itself*, so checking them at the front door gives the member instant feedback instead of after a full pipeline run. The values still come from PolicyStore, not code.

### 4. Form metadata API — `GET /policy/meta`
Returns the values the submission UI needs for its dropdowns: valid member IDs (with names), the policy ID, and the list of claim categories — all read from PolicyStore. In the UI (built in a later step), member ID is picked first from a dropdown, and policy ID + claim category are dropdowns too, since their possible values are fixed by the policy file. Server-side validation in `POST /claims` stays regardless — the API can be called without the UI.

### 5. Pipeline runner (skeleton only)
A small orchestrator that runs stages in order, where each stage is a function: `(ClaimRecord) -> ClaimRecord`. In Step 1 it has only the intake stage, but it already has the failure rule baked in: **if a stage throws, the runner catches it, writes a `SKIPPED` trace event with the error, lowers a pipeline-health score on the record, and moves on** (this is what TC011 tests later, and the per-field confidence assumption from `Assumptions.md` plugs into this same health score).

### 6. Read-back API — `GET /claims/{id}`
Returns the claim record with its full trace. The ops-review UI will sit on top of this later.

### 7. Tests
- Unit tests for every intake check (good input, each bad input).
- A test that PolicyStore refuses a broken policy file.
- An API test: submit a valid claim → get `RECEIVED` + trace; submit each bad claim → get the right specific error.
- A test that `GET /policy/meta` returns the member list, policy ID, and categories from the policy file.

## Component contracts (deliverable #3 starts now)

**Intake**
- In: multipart `ClaimSubmission` + files
- Out: `ClaimRecord` with status `RECEIVED` and trace events, or a 422 error `{error_code, message, what_to_do_next}`
- Errors it can raise: `MEMBER_NOT_FOUND`, `POLICY_MISMATCH`, `UNKNOWN_CATEGORY`, `INVALID_AMOUNT`, `INVALID_DATE`, `NO_DOCUMENTS`, `BAD_FILE`, `SUBMISSION_TOO_LATE`, `BELOW_MINIMUM_AMOUNT`

**PolicyStore**
- In: path to `policy_terms.json`
- Out: typed lookup methods
- Errors: `POLICY_FILE_INVALID` (boot-time only)

**Form metadata**
- In: none (`GET /policy/meta`)
- Out: `{policy_id, members: [{member_id, name}], claim_categories: [...]}`
- Errors: none beyond service availability (data is already validated at boot)

## What Step 1 deliberately leaves out

- Checking whether the *right document types* were uploaded (TC001) — that needs to look inside files, so it's Step 2's document-check agent.
- Any LLM call, any extraction, any decision logic.
- UI — comes after the API shape is stable, but the `GET /policy/meta` endpoint is built now so the dropdown-based form needs no API changes later.

---

# Step 2: Document Check (the early gate)

## What Step 2 delivers

Before any extraction or decision happens, the system looks at each uploaded file and answers two questions: **is this the right kind of document for this claim type, and can it be read at all?** If something is wrong, the claim stops right there with a message that names the exact problem and the exact fix. This is the stage that TC001 (wrong document) and TC002 (unreadable document) test, and it carries 10% of the grade on its own.

TC003 (documents belong to different patients) is **not** in this step — comparing patient names needs field extraction first, so it lands in Step 4 (Cross-Doc Checks), after Step 3 builds extraction.

## Components in Step 2

### 1. Document Classifier agent (the first LLM call in the system)

For each uploaded file, one call to a vision-capable Claude model (e.g., `claude-sonnet-4-6` — good vision, fast, cheap; this is a classification task, not deep reasoning; the model name comes from the config file). The model returns strict JSON, validated by a Pydantic model:

- `detected_type` — one of: `PRESCRIPTION`, `HOSPITAL_BILL`, `PHARMACY_BILL`, `LAB_REPORT`, `DIAGNOSTIC_REPORT`, `DENTAL_REPORT`, `DISCHARGE_SUMMARY`, `UNKNOWN`
- `confidence` — 0 to 1, how sure the model is about the type
- `quality` — `GOOD` / `POOR` / `UNREADABLE`
- `evidence` — one sentence on why ("has Rx symbol, medicine list with dosages, doctor signature block")
- `notes` — anything odd worth surfacing later (regional language content, stamps over text, visible corrections) — these feed the fraud check in Step 6

**Why a strict output schema even though documents vary wildly:** the documents vary, but the question we ask about them never does — what type, how readable, how confident. Downstream code has to branch on those answers deterministically (`if detected_type not in required_types...`), and code cannot branch on free-form prose; letting the model improvise the structure would just force us to write a second parser for whatever it invents. The document messiness is absorbed inside the schema by the free-text `evidence` and `notes` fields, where the model can say anything. The assignment also grades "is output structured and validated" directly.

Engineering rules for the call:
- Structured output enforced; if the model returns JSON that fails Pydantic validation, retry once; if the retry also fails, treat it as a classifier failure and use the fallback chain below.
- Hard timeout per call (from config).
- All documents are classified **concurrently** (async), since the calls are independent.
- **Fallback chain when the classifier fails** (timeout, network error, or bad JSON twice): use the member's declared type from the upload (if given) with a `WARN` trace event and reduced confidence → otherwise `UNKNOWN`. The pipeline never dies because the classifier did (assignment requirement #6).
- A classifier failure is a different *cause* than a low-confidence answer (low confidence = the model answered but was unsure; failure = no usable answer at all), but it adds no new *outcomes*: after the fallback chain, the claim continues with a `WARN`, or stops via the normal missing-document path if a required document's type can't be established.

The prompt only asks "what kind of document is this and is it readable" — it does **not** extract fields. Keeping classification and extraction as separate calls keeps each prompt small and testable, and means a cheap call gates the expensive one.

### 2. Document source adapter (real files vs. test stubs)

`test_cases.json` doesn't ship real images — documents arrive as JSON stubs with `actual_type`, sometimes `quality` and `content`. So documents enter the pipeline through one interface with two implementations:

- **Real-file source:** the file bytes go to the Document Classifier (and later to the extractor in Step 3).
- **Stub source:** the given `actual_type` / `quality` / `content` are used directly, as if a perfect classifier/extractor had run. No LLM call.

Everything after this adapter is identical in both modes. This makes the 12-case eval report deterministic, free, and runnable in CI, while the same pipeline handles real uploads from the UI. This is a deliberate design decision and will be called out in the architecture document.

### 3. Requirement Checker (pure code, no LLM)

Takes the classified document list and compares it against the policy's `document_requirements` for the claim category (via PolicyStore). The verdict logic is deterministic:

| Situation | Outcome | Message style |
|---|---|---|
| All required types present and readable | PASS — status becomes `DOCUMENTS_VERIFIED`, pipeline continues | — |
| A required type is missing / wrong type uploaded (TC001) | STOP — status `NEEDS_RESUBMISSION` | Names what was uploaded and what is needed: "You uploaded 2 prescriptions. A CONSULTATION claim needs 1 prescription and 1 hospital bill. Please upload the hospital bill for this visit." |
| A required document is `UNREADABLE` (TC002) | STOP — status `NEEDS_RESUBMISSION`, claim is **not** rejected | Names the exact file: "We couldn't read 'blurry_bill.jpg' (the pharmacy bill). Please re-upload a clearer photo of just that document. The prescription you sent is fine." |
| Type detected with low confidence (below the config threshold, default 0.6) | CONTINUE with `WARN` trace event; claim confidence is down-weighted (per `Assumptions.md`) | — |
| Classifier failed entirely for a file (fallback chain already applied) | CONTINUE using declared type with `WARN`, or STOP with `MISSING_DOCUMENT` if the type can't be established for a required doc | Tells the member which file we couldn't process |

`NEEDS_RESUBMISSION` is a stop, not a rejection — this matches "do not reject the claim outright" in TC002. To get the claim paid, the member submits a fresh claim with the corrected documents; the stopped claim stays stored for audit. (A flow where the member re-uploads into the same claim is deliberately not built — it adds claim-state complexity for no grading value. This trade-off goes in the architecture document.)

### 4. Stop response shape

When the gate stops a claim, the API response carries no decision (decision stays `null`) and lists every problem at once — the member fixes everything in one round trip instead of discovering issues one by one:

```
{
  "claim_id": "...",
  "status": "NEEDS_RESUBMISSION",
  "problems": [
    { "file_id": "F002", "file_name": "another_prescription.jpg",
      "error_code": "WRONG_DOCUMENT_TYPE",
      "message": "...names uploaded type and required type...",
      "what_to_do_next": "...exact action..." }
  ]
}
```

### 5. Confidence bookkeeping starts here

Each classification carries a confidence score. The claim record now keeps a running `confidence` value: starts at 1.0, gets pulled down by `WARN` (low-confidence reads) and `SKIPPED` (failed components) events. The exact aggregation formula gets tuned in Step 5 when decisions are produced; in this step we only record the inputs honestly. This is where the per-field confidence assumption from `Assumptions.md` takes root.

### 6. Mock document generator (test tooling)

A small script (PIL or HTML-to-image, per `sample_documents_guide.md`) that renders sample prescriptions, bills, and lab reports — including a blurry one and a wrong-type one — so the real-file LLM path can be tested by hand and in the demo video. Not part of the pipeline itself.

### 7. Tests

The Requirement Checker tests use a **fake classifier** — a stand-in object that returns whatever classification the test tells it to, so no LLM or network is involved and each scenario is easy to set up:

- Fake classifier says a file is a `PRESCRIPTION` but the claim needs a `HOSPITAL_BILL` → the claim stops, and the message contains both the words "prescription" (what was uploaded) and "hospital bill" (what is needed). This is TC001.
- Fake classifier says a file is `UNREADABLE` → the claim stops, and the message names that exact file and asks to re-upload only it, while confirming the other documents are fine. This is TC002.
- Fake classifier returns correct, readable types for everything → status becomes `DOCUMENTS_VERIFIED`.
- Fake classifier says both files are prescriptions when the claim needs one prescription and one bill → the response lists the problem clearly (right count of documents, wrong mix).
- Classifier-failure test: the fake classifier raises an error instead of answering → the stage falls back to the member's declared type and writes a `WARN` trace event; if no declared type exists for a required document, the claim stops with a message naming the file we couldn't process. In no case does the pipeline itself crash.
- Bad-JSON test: the fake LLM returns invalid JSON twice (first call + one retry) → the stage treats it as a classifier failure and applies the same fallback chain as above (declared type with `WARN` → `UNKNOWN`).
- Stub-source test: a document stub from `test_cases.json` flows through with no LLM call.
- TC001 and TC002 run end-to-end as automated tests from this step onward.

## Component contracts

**Document Classifier**
- In: one document (file bytes + file name, or a stub)
- Out: `DocumentClassification {file_id, detected_type, confidence, quality, evidence, notes}`
- Errors: two codes, split by who detected the problem. `CLASSIFIER_CALL_FAILED` — the provider call itself failed; we do not rename what the SDK already names, so the trace event carries the provider's own error (e.g., `APITimeoutError`, `RateLimitError`) and message verbatim in its detail. `CLASSIFIER_BAD_OUTPUT` — detected by our code: the call succeeded but the content failed schema validation after one retry; the provider has no error for this, so it needs our own code. Both are caught inside the stage and trigger the fallback chain; they never escape to the caller. The two codes exist for control flow (one catch point for the fallback chain) — the error identity and detail stay the provider's wherever the provider has one.

**Document Check stage**
- In: `ClaimRecord` (status `RECEIVED`)
- Out: same `ClaimRecord` with classifications attached, trace events written, and status moved to `DOCUMENTS_VERIFIED` or `NEEDS_RESUBMISSION` (with a `problems` list)
- Errors: none escape — every failure mode degrades into a trace event and, at worst, a `NEEDS_RESUBMISSION` with an explanation

## What Step 2 deliberately leaves out

- Field extraction (patient name, amounts, dates) — Step 3.
- Patient-name mismatch across documents (TC003) — Step 4, because it needs extracted names.
- Fraud signals — the classifier's `notes` (corrections, duplicate stamps) are recorded now but only acted on in Step 6.

---

# Step 3: Extraction (the model reads each document in its own words)

## What Step 3 delivers

Every document that passed the Step 2 gate gets one LLM read that captures everything on it — names, amounts, dates, diagnoses, line items with individual confidences — **in whatever structure the model finds natural for that document**. The output is stored as-is. Later stages consume it with LLM calls: Step 4 uses an LLM call to compare documents (same patient? consistent dates? do line items match the total?), and Step 5 uses a small LLM call to pull out the specific values a calculation needs — then plain code does the actual math.

**Design decision :** extraction output is fully flexible — no enforced schema, not even a per-field shape. Real documents vary too much for a fixed field list, and forcing one risks losing or distorting what's actually on the page. The accepted trade-offs, recorded here for the architecture document: (a) a few extra LLM calls per claim downstream, since comparisons and value-pulls become model calls; (b) the 12-case eval invokes LLMs, so runs cost a little and traces can vary slightly between runs; (c) confidence has no fixed per-field slot — it's carried in the model's own notes plus one overall number per document.

The `Assumptions.md` assumption still holds, expressed in the model's own format: the prompt orders the model to attempt every field — regional-language and obscured ones included — to never invent values, and to state, in its own words, what it read with low confidence and why. Those notes flow into the trace verbatim.

## Components in Step 3

### 1. Document Reader agent (one LLM call per document)

One vision call per document (concurrent across documents), with the Step 2 `detected_type` and the field lists from `sample_documents_guide.md` given as **guidance** — "here is what claims processing usually needs from a prescription" — not as a required output shape. The prompt:

- Read everything on the document; structure the output however fits this document best.
- Attempt every field, including regional-language and partially obscured ones with their confidence values.
- For anything read with difficulty, say so and explain why (handwriting, stamp, Hindi text, blur) — in your own words, attached wherever it fits your structure.
- Finish with one overall number: `extraction_confidence` (0 to 1) — "how well could you read the information that matters for a claim decision" This number comes from the model's own judgment, not from any formula of ours.

**The only thing we require** is a two-part envelope: `{extraction_confidence: <number>, content_with_individual_confidences: <anything — any JSON shape or prose the model chooses>}`. Validation touches the envelope only, never the content.

Engineering rules:
- If the envelope is missing or malformed, retry once; then `EXTRACTOR_BAD_OUTPUT` and the document is marked read-failed.
- Provider call failures surface as `EXTRACTOR_CALL_FAILED`, carrying the provider's own error verbatim.
- Hard timeout, model name, and retry count from config.
- **A failed read never stops the pipeline.** The document attaches with empty content and a `SKIPPED` trace event; Step 5's decision engine deals with the gap (typically by routing to `MANUAL_REVIEW`). This is the TC011 path.
- Multi-page documents: all pages go into one call as multiple images (page cap in config), so the model can aggregate across pages, per the documents guide.

### 2. Stub mode (extends the Step 2 adapter)

For test-case documents that carry a `content` block, that block is stored verbatim with individual confidences as one, and the read result with `extraction_confidence: 1.0` — no LLM call here. The downstream LLM calls in Steps 4 and 5 still run on this stub content (that's part of the accepted trade-off above).

### 3. Output and confidence bookkeeping

- Each document gets a `DocumentRead {file_id, doc_type, extraction_confidence, content_with_individual_confidences}` attached to the claim record, content untouched.
- A document whose `extraction_confidence` is below the threshold (config, default 0.6) writes a `WARN` trace event quoting the model's own notes about what it struggled with. The claim's running confidence is pulled down, continuing the bookkeeping started in Step 2.
- Claim status moves `DOCUMENTS_VERIFIED` → `EXTRACTED` (read failures still reach `EXTRACTED`, with the gaps visible in trace and confidence).

### 4. Tests

Same fake-LLM approach as Step 2 — the fake reader returns whatever the test specifies:

- Storage test: whatever blob the fake reader returns is stored byte-for-byte; nothing reshapes it.
- Low-confidence test: fake reader returns `extraction_confidence: 0.4` with struggle notes in its content → a `WARN` trace event quotes those notes, and the claim's running confidence drops.
- Envelope test: fake reader returns output with no `extraction_confidence` twice (first call + one retry) → `EXTRACTOR_BAD_OUTPUT`, document attaches read-failed with a `SKIPPED` event, pipeline continues.
- Provider-failure test: fake reader raises a provider error for one of two documents → that one attaches read-failed, the other reads normally, status still reaches `EXTRACTED`. Nothing crashes.
- Stub test: TC004's `content` blocks are stored verbatim with individual confidences as 1.0 and extraction confidence also as 1.0 with zero LLM calls in this stage.

## Component contracts

**Document Reader**
- In: one classified document (file bytes + `detected_type`, or a stub with `content`)
- Out: `DocumentRead {file_id, doc_type, extraction_confidence, content_with_individual_confidences}` — `content_with_individual_confidences` is whatever structure the model chose; only the envelope is guaranteed
- Errors: `EXTRACTOR_CALL_FAILED` (provider call failed; provider's own error kept verbatim in the trace detail), `EXTRACTOR_BAD_OUTPUT` (envelope missing/malformed after one retry). Both are caught inside the stage; the document comes back read-failed, never an exception to the caller.

**Extraction stage**
- In: `ClaimRecord` (status `DOCUMENTS_VERIFIED`)
- Out: same `ClaimRecord` with `DocumentRead`s attached, trace events written, status `EXTRACTED`
- Errors: none escape — per-document failures degrade into trace events and confidence reduction

## What Step 3 deliberately leaves out

- **Any checking at all.** This stage only reads. Consistency checks — both within one document (do line items sum to the stated total?) and across documents (same patient? dates line up? bill total vs. claimed amount?) — all live in Step 4, done by an LLM call over the flexible content (this covers TC003).
- Any policy reasoning or decision — Step 5.
- Acting on fraud-ish signals (altered totals, odd registration numbers) — the model's notes are stored now, consumed in Step 6.
- OCR preprocessing (deskewing, contrast fixes for phone photos) — the vision model handles messy images directly; if real-world quality demands preprocessing later, it slots in front of the reader without changing any contract. Documented as a trade-off.

---

# Step 4: Consistency Checks (do the documents agree — with each other and with the claim?)

## What Step 4 delivers

One LLM call that looks at all the document reads together — plus the claim details and the member's roster entry — and answers a fixed list of consistency questions. This is the second and last gate before decision-making: a claim whose documents belong to different patients stops here with both names shown to the member (TC003). Everything else found here (a total that doesn't add up, a date that looks off) becomes a `WARN` that travels forward into the decision and fraud stages rather than stopping the claim.

This stage is where the "fully flexible extraction" decision pays its way: the Step 3 content has no fixed shape, so comparing it is a language task, and an LLM call is the tool that can do it — judging that "Rajesh Kumar" and a transliterated "राजेश कुमार" are the same person, that "Arjun Mehta" is not, and that "R. Kumar" is only a partial match that needs human eyes.

## Components in Step 4

### 1. Consistency Checker agent (one LLM call per claim)

A text-only call (no images — it reads the stored `DocumentRead` contents), which gets:
- every document's `content_with_individual_confidences` from Step 3,
- the claim details: treatment date, claimed amount, claim category, hospital name if stated,
- from PolicyStore: the member's name and the names of their registered dependents (a family-floater policy means the patient may legitimately be a spouse or child, not the employee).

It answers a **fixed list of questions we define** (the checks are ours; only the documents are flexible):

| # | Check | If it fails |
|---|---|---|
| 1 | Same patient on every document? And is that patient the member or one of their dependents? | Clearly different names: **STOP** — the only hard stop in this stage (TC003). Abbreviation-only match: claim continues but is **forced to MANUAL_REVIEW** (see below) |
| 2 | Do document dates line up with the stated treatment date and with each other (prescription on/before the bill)? | `WARN`, carried into Steps 5–6 |
| 3 | Does the bill total support the claimed amount? | `WARN` + the discrepancy recorded for Step 5 (the decision engine works from documented amounts, not the claimed number) |
| 4 | Within each bill: do the line items sum to the stated total? | `WARN`, flagged to Step 6 (the documents guide lists altered amounts as a fraud signal) |
| 5 | Does the doctor on the prescription match the doctor referenced on bills/reports? | `WARN` only — referrals make this legitimately loose |
| 6 | Anything else odd when reading the documents side by side? | free-text observations, stored for Step 6 |

**All documents go into this one call together — the checks are claim-level questions, not per-document tests.** Each check involves only the documents that carry the relevant fact: a prescription has no bill total, so it takes part in the patient/date/doctor checks but not the amount checks. Check 3 in particular *requires* aggregation: when a claim has several bills (pharmacy separate from consultation, per the documents guide), it's their combined total that must support the claimed amount — no single document can answer that. Check 4 runs once per bill that has line items, still inside the same call.

**Which checks to run is decided deterministically by code, before the call; the LLM then performs the chosen checks.** Step 2 already classified every document, so applicability is a simple lookup: a claim with no prescription (e.g., DENTAL) never gets the doctor-consistency question. The model's response must contain a verdict for **exactly** the checks asked — nothing missing, nothing extra; a missing verdict is bad output and goes through the normal retry path, so an omission can never be mistaken for "didn't apply." Checks that weren't asked get a trace event from code ("doctor-consistency not checked: no prescription in this claim"), keeping the trace complete for the ops view.

The prompt instructs the model to use the per-field confidences from Step 3 when judging: a name that was read at confidence 0.4 should make a mismatch verdict *less* certain, not more — uncertainty in reading must not turn into a confident accusation.

**Patient-name comparison is a three-way judgment** (the assignment doesn't define name matching, so this is our documented rule):
- Full-name match — including spelling variants and transliterations of the same full name ("Rajesh Kumar" / "राजेश कुमार") → `PASS`.
- Clearly different person ("Rajesh Kumar" vs. "Arjun Mehta") → `FAIL` → hard stop (TC003).
- Abbreviation-only match ("R. Kumar" vs. "Rajesh Kumar") → `MANUAL_REVIEW`: the initial is *consistent* with the member but doesn't *confirm* identity — "R. Kumar" could be a different person entirely. The claim is not bounced (the documents may be perfectly genuine) and not silently passed (the one identity gate must not be satisfied by an initial); it goes to a human.

Doctor-name comparison (check 5) stays loose — it is WARN-only either way, so an initials match there is fine.

**Output is a strict verdict schema** — `{check_id, result: PASS/FAIL/WARN/MANUAL_REVIEW, confidence, explanation, evidence}` per check, Pydantic-validated. This isn't a contradiction of the flexible-extraction decision: the *documents* are flexible, but each verdict is an answer to our fixed question, and the pipeline must branch on it (stop or continue), write it as a trace event, and show it to the ops team. `evidence` carries the specifics the messages need — for check 1, the exact name found on each document.

Engineering rules (same discipline as before):
- One retry on schema failure → `CONSISTENCY_BAD_OUTPUT`; provider failures → `CONSISTENCY_CALL_FAILED` with the provider's error verbatim. Both caught inside the stage.
- **If the checker fails entirely, the pipeline continues**: a `SKIPPED` trace event, claim confidence drops, and a note that manual review is recommended because consistency was never verified. This stage is also a natural target for TC011's `simulate_component_failure` flag — the claim can still reach a decision without it, which is exactly what that test wants to see.
- Timeout, model, retry count from config.

### 2. Verdict handling (pure code)

Deterministic mapping from verdicts to outcomes — no judgment here, just routing:
- Check 1 `FAIL` (confident) → status `NEEDS_RESUBMISSION`, decision stays `null`, problems list reuses the Step 2 stop shape: `{error_code: PATIENT_MISMATCH, message: "The prescription is for Rajesh Kumar, but the hospital bill is for Arjun Mehta. All documents in one claim must be for the same patient...", what_to_do_next: ...}` — the names come from the verdict's `evidence`, satisfying TC003's "specific names found on each document".
- Check 1 `FAIL` with low confidence (below config threshold) → don't stop on a shaky read: `WARN` + route the claim toward manual review in Step 5. A blurry name shouldn't bounce a legitimate claim.
- Check 1 `MANUAL_REVIEW` (abbreviation-only match) → pipeline continues, a `manual_review_required` flag is set on the claim record with the reason ("patient identity matched on initials only: 'R. Kumar' vs. member 'Rajesh Kumar'"), and a `WARN` trace event is written. Step 5 must honor this flag: whatever the policy outcome computes, the final decision becomes `MANUAL_REVIEW`, with the policy outcome attached for the reviewer.
- Any check `WARN` → trace event + recorded on the claim for Steps 5–6; pipeline continues.
- All pass → status `EXTRACTED` → `CHECKED`.

### 3. Stub mode

Nothing new needed: the checker runs on stub content like on real content (the accepted trade-off from Step 3). One detail: stub documents expose whatever fields the test case provides — TC003's documents carry `patient_name_on_doc` instead of a `content` block, so the adapter stores any such fields as the document's content for the checker to read.

### 4. Tests

Fake checker (returns whatever verdicts the test specifies) for all plumbing; the real prompt is exercised by the live eval run:

- TC003 path: fake checker fails check 1 with evidence naming "Rajesh Kumar" and "Arjun Mehta" → claim stops, decision is `null`, and the member-facing message contains both names.
- Low-confidence mismatch: same verdict but confidence 0.3 → no stop; `WARN` written and the claim continues, marked toward manual review.
- Abbreviation-match: fake checker returns `MANUAL_REVIEW` on check 1 with evidence "R. Kumar" / "Rajesh Kumar" → claim continues, `manual_review_required` flag is set with the reason, `WARN` trace written. (Once Step 5 exists, the end-to-end version asserts the final decision is `MANUAL_REVIEW`.)
- All-pass: status reaches `CHECKED`, six `PASS` trace events exist.
- Sum-mismatch: check 4 returns `WARN` → pipeline continues, the warning is stored where Step 6 will look for it.
- Checker failure: fake checker raises twice → `SKIPPED` trace event, claim confidence drops, status still reaches `CHECKED` with a manual-review note. Nothing crashes.
- Dependent-patient case: documents name the member's spouse (a registered dependent) → check 1 passes; the claim is not stopped.
- Applicability test (pure code, no LLM): a DENTAL claim with only a hospital bill → the check selection excludes doctor-consistency, the checker is asked only the applicable checks, and a trace event says why the others weren't run.
- Completeness test: fake checker answers only 3 of 4 asked checks → treated as bad output, retried once, then the failure path.

## Component contracts

**Consistency Checker**
- In: all `DocumentRead`s of a claim + claim details + member roster entry (name + dependents)
- Out: list of `CheckVerdict {check_id, result, confidence, explanation, evidence}` — one per defined check
- Errors: `CONSISTENCY_CALL_FAILED` (provider error kept verbatim), `CONSISTENCY_BAD_OUTPUT` (verdict schema invalid after one retry). Caught inside the stage; the stage degrades, never throws.

**Consistency stage**
- In: `ClaimRecord` (status `EXTRACTED`)
- Out: same `ClaimRecord` with verdicts attached and trace events written; status `CHECKED`, or `NEEDS_RESUBMISSION` with a problems list (patient mismatch only)
- Errors: none escape — checker failure degrades to `SKIPPED` + confidence drop + manual-review note

## What Step 4 deliberately leaves out

- Policy rules — coverage, sub-limits, co-pay, waiting periods, exclusions, pre-auth — all Step 5.
- Acting on fraud signals: the warnings collected here (sum mismatches, odd observations) are stored, but scoring them and deciding on `MANUAL_REVIEW` is Step 6.
- Claims-history patterns (TC009's same-day claims) — that's fraud logic, Step 6.
- Verifying doctor registration numbers against state formats — recorded by the reader in Step 3 if it noticed something odd; judged in Step 6 if at all. Not a consistency question between documents.

---

# Step 5: Policy Decision (the rules engine)

## What Step 5 delivers

The stage that actually decides: `APPROVED`, `PARTIAL`, `REJECTED`, or `MANUAL_REVIEW`, with the approved amount, the reasons, and a confidence score. It covers seven test cases directly: TC004 (clean approval with co-pay), TC005 (waiting period), TC006 (partial — excluded line item), TC007 (missing pre-auth), TC008 (per-claim limit), TC010 (network discount before co-pay), TC012 (excluded condition).

The design splits into two halves, honoring the decisions made in review:
- **Decision Prep (one LLM call):** turns the flexible document content into the machine-form values the rules need. This is the "value-pull" call promised in Step 3.
- **Rules Engine (pure code, no LLM):** applies the policy in a fixed, documented order and does all the math. Every rule application writes a trace event with the numbers in it — this is what makes "reconstruct exactly why any claim got any decision" possible, and it's fully unit-testable without any LLM.

## Components in Step 5

### 1. Decision Prep agent (one LLM call per claim)

Input: all `DocumentRead` contents, the claim details, and the *relevant policy lists* from PolicyStore (the category's covered/excluded procedures, the exclusion conditions, the waiting-period condition names, the high-value tests needing pre-auth, the network hospital list). Output — strict schema, because code must compute on it:

- `line_items` — each with `description`, `amount` (as a number), and a **policy mapping**: covered / excluded (with which policy entry it matched, e.g., "Teeth Whitening → excluded_procedures: Teeth Whitening") / requires pre-auth (e.g., "MRI Lumbar Spine → high_value_tests: MRI")
- `documented_total` — combined bill total, as a number
- `diagnosis_mapping` — the diagnosis mapped to policy concepts: an exclusion condition ("Morbid Obesity — BMI 37" → "Obesity and weight loss programs") and/or a waiting-period key ("Type 2 Diabetes Mellitus" → "diabetes"), or none
- `hospital_network_match` — whether the hospital matches a network-list entry ("Apollo Hospitals, Bengaluru" should match "Apollo Hospitals"), and which
- `treatment_date_iso`, plus each mapping with its own `confidence`

Why this is an LLM call and not string matching: the mappings are semantic. "Bariatric Consultation" must hit "Obesity and weight loss programs", "T2DM" must hit "diabetes", and a misspelled "Apolo Hospital" must hit "Apollo Hospitals" — lookups can't do that reliably; a model with the policy lists in front of it can, and reports confidence per mapping.

Engineering rules (same as every agent): `PREP_CALL_FAILED` / `PREP_BAD_OUTPUT` after one retry, provider errors verbatim, timeout and model from config. **If prep fails entirely, the decision is `MANUAL_REVIEW`** — "could not reliably read the values needed for an automatic decision" — with a `SKIPPED` trace event and reduced confidence. Never a crash, never a guess.

### 2. Rules Engine (pure code — the policy, applied in order)

Every value it uses comes from PolicyStore or the prep output; nothing hardcoded. The evaluation order is fixed and documented, because order changes outcomes:

1. **Membership timing** — member's `join_date` vs. treatment date; the initial 30-day waiting period.
2. **Exclusions** — claim level first: if the diagnosis maps to an excluded condition → `REJECTED (EXCLUDED_CONDITION)` (TC012). Exclusions outrank waiting periods deliberately: an excluded condition is permanently not covered, so that's the truthful headline reason even when a waiting period also applies (obesity has both).
3. **Waiting periods** — diagnosis mapped to a condition key → compare treatment date against `join_date + waiting_days`. Rejection must state the exact date the member becomes eligible (TC005: joined 2024-09-01 + 90 days for diabetes → eligible from 2024-11-30).
4. **Pre-authorization** — any line item mapped to a pre-auth test, with amount over the policy threshold and no pre-auth on record → `REJECTED (PRE_AUTH_MISSING)`, and the message tells the member how to resubmit with pre-auth (TC007).
5. **Per-claim limit** — claimed amount over `per_claim_limit` → `REJECTED (PER_CLAIM_EXCEEDED)`, stating both numbers (TC008: "your claim of ₹7,500 exceeds the per-claim limit of ₹5,000"). The policy treats this as a rejection, not a cap — the test case confirms.
6. **Line-item filtering** — excluded line items drop out with a per-item reason; covered items stay. If some items drop and some stay → the decision is `PARTIAL` with an itemized breakdown (TC006: root canal ₹8,000 approved, teeth whitening ₹4,000 rejected as cosmetic).
7. **The payable base** — the smaller of the claimed amount and the documented covered total (a member can't be paid more than the documents support; if they claim less, pay the claimed amount).
8. **Money math, in fixed order** (TC010 exists to catch this): apply category sub-limit cap → apply **network discount first** (if `hospital_network_match`, e.g., 20%) → apply **co-pay second** (e.g., 10%) → check annual OPD limit headroom using `ytd_claims_amount`. TC010: ₹4,500 → 20% discount → ₹3,600 → 10% co-pay → **₹3,240**. TC004: ₹1,500 → 10% co-pay → **₹1,350**. Every arithmetic step writes a trace event showing before/after numbers, and the breakdown appears in the decision output.
9. **Manual-review overrides, applied last** — if Step 4 set `manual_review_required` (abbreviation-only identity match), or a document was read-failed in Step 3, or any component was `SKIPPED` along the way (TC011): the computed policy outcome is *attached* for the reviewer, but the final decision becomes `MANUAL_REVIEW` (for the Step 4 flag) or keeps the computed decision with a lowered confidence and an explicit "manual review recommended" note (for degraded-pipeline cases, per TC011's expectation of `APPROVED` + lower confidence + recommendation).

### 3. Decision output (the shape the eval report shows)

`Decision {decision, approved_amount, currency, reasons[], line_item_breakdown[], money_breakdown[], confidence, manual_review_recommended, eligibility_date?, what_to_do_next?}` — plus the full trace already on the claim record. Status moves `CHECKED` → `DECIDED`.

Confidence: the claim's running confidence (started at 1.0 in Step 2, reduced by every `WARN` and `SKIPPED` along the way, including prep-mapping confidences below the config threshold) becomes the decision's confidence score. A clean claim stays high (TC004 expects > 0.85, TC012 > 0.90); a degraded run lands visibly lower (TC011). The exact deduction sizes live in config, not code.

### 4. Tests

The Rules Engine is pure code, so each scenario is a direct unit test with a hand-built prep output — no LLM, no network:

- TC004 math: ₹1,500 consultation, 10% co-pay → `APPROVED`, ₹1,350, trace shows the co-pay step.
- TC005 waiting period: joined 2024-09-01, diabetes, treated 2024-10-15 → `REJECTED (WAITING_PERIOD)`, message contains "2024-11-30".
- TC006 partial: covered ₹8,000 + excluded ₹4,000 → `PARTIAL`, ₹8,000, itemized breakdown with a per-item rejection reason.
- TC007 pre-auth: MRI ₹15,000, no pre-auth → `REJECTED (PRE_AUTH_MISSING)`, message says how to resubmit.
- TC008 limit: ₹7,500 vs. ₹5,000 limit → `REJECTED (PER_CLAIM_EXCEEDED)`, message contains both numbers.
- TC010 order: asserts ₹3,240 — and a deliberate wrong-order check (co-pay before discount gives ₹3,150) to prove the order is enforced.
- TC012 exclusion: obesity diagnosis → `REJECTED (EXCLUDED_CONDITION)`; also asserts exclusions outrank waiting periods.
- Step 4 flag honored: `manual_review_required` set → final decision `MANUAL_REVIEW`, computed outcome attached.
- Degraded pipeline: a `SKIPPED` event on the record → decision stands, confidence lower than the clean-run baseline, "manual review recommended" note present (TC011 shape).
- Prep failure: fake prep raises twice → decision is `MANUAL_REVIEW`, not an exception.
- Payable base: claimed ₹2,000 vs. documented ₹1,500 → math runs on ₹1,500; claimed ₹1,200 vs. documented ₹1,500 → math runs on ₹1,200.

## Component contracts

**Decision Prep**
- In: all `DocumentRead`s + claim details + relevant policy lists from PolicyStore
- Out: `PrepResult {line_items[{description, amount, mapping, confidence}], documented_total, diagnosis_mapping, hospital_network_match, treatment_date_iso}`
- Errors: `PREP_CALL_FAILED` (provider error verbatim), `PREP_BAD_OUTPUT` (schema invalid after one retry). Caught inside the stage → decision becomes `MANUAL_REVIEW`.

**Rules Engine**
- In: `PrepResult` + `ClaimRecord` (for flags, confidence, ytd amounts) + PolicyStore
- Out: `Decision` (shape above) + trace events for every rule applied
- Errors: none — it's deterministic code over validated inputs; missing inputs route to `MANUAL_REVIEW` by rule, not by exception

**Decision stage**
- In: `ClaimRecord` (status `CHECKED`)
- Out: same `ClaimRecord` with `Decision` attached, status `DECIDED`
- Errors: none escape

## What Step 5 deliberately leaves out

- Fraud signals and history patterns — same-day claim counts (TC009), monthly limits, the ₹25,000 auto-review threshold, and the fraud score all live in Step 6, which runs after the decision and can override it to `MANUAL_REVIEW`.
- The UI for reviewing decisions — after Step 6, once the output shape is final.

---

# Step 6: Fraud Check (the last gate, and the end of the pipeline)

## What Step 6 delivers

The stage that asks: should a human look at this before money moves? It runs *after* the policy decision, collects everything suspicious the pipeline noticed along the way, applies the policy's `fraud_thresholds`, and can override the decision to `MANUAL_REVIEW` — never to `REJECTED`. Calling fraud is a human's job; the system's job is to route and to explain. This covers TC009 (four same-day claims → manual review, with the specific signals named in the output).

Like Step 5, it splits into deterministic and semantic halves:
- **Threshold checks (pure code):** counting and comparing — claims per day, claims per month, claim value — against `fraud_thresholds` in the policy file.
- **Fraud Assessor (one LLM call):** weighs the *soft* signals that accumulated through the pipeline, which are free-text notes and therefore a language task.

## Components in Step 6

### 1. Threshold checks (pure code)

All limits from PolicyStore, nothing hardcoded:

| Check | Policy source | TC009 example |
|---|---|---|
| Claims from this member today (from `claims_history`) vs. limit | `same_day_claims_limit: 2` | 3 prior + this one = 4 on 2024-10-30 → trip |
| Claims this month vs. limit | `monthly_claims_limit: 6` | — |
| Claim value above the auto-review line | `auto_manual_review_above: 25000` | — |

Each check writes a trace event with the actual numbers ("4 claims on 2024-10-30; the policy allows 2 per day"). Any trip → manual review override, no LLM involved — counting is not a judgment call.

### 2. Fraud Assessor agent (one LLM call per claim)

Input: the claim summary, the claims history, and every soft signal the pipeline stored on the way here —
- Step 2 classifier `notes` (duplicate "ORIGINAL" stamps, visible corrections — the documents guide flags both),
- Step 3 reader notes (amounts crossed out and rewritten → the guide's `DOCUMENT_ALTERATION` signal, odd registration numbers),
- Step 4 warnings (line items not summing to the total, date inconsistencies, side-by-side oddities like two bills with the same bill number).

Output — small strict schema, because code must compare it against the policy threshold: `{fraud_score: 0..1, signals: [{name, severity, explanation}]}`. The score is the model's judgment; what happens at each score is the policy's (`fraud_score_manual_review_threshold: 0.80`).

Engineering rules (same as every agent): `ASSESSOR_CALL_FAILED` / `ASSESSOR_BAD_OUTPUT` after one retry, provider errors verbatim, timeout and model from config. **If the assessor fails, the threshold checks have already run** — hard limits are still enforced; the claim continues with a `SKIPPED` event, a confidence drop, and a note that fraud signals were not fully assessed.

### 3. Override logic (pure code)

- Any threshold trip, **or** `fraud_score ≥ 0.80` → final decision becomes `MANUAL_REVIEW`. The computed policy outcome from Step 5 stays attached for the reviewer, and the output lists the specific signals that triggered the flag — TC009 grades exactly this specificity.
- Signals below threshold → trace events only; the decision stands, confidence may dip slightly (config).
- **A claim that Step 5 already `REJECTED` is not overridden** — there's no payment to protect. Its fraud signals are still recorded in the trace for pattern intelligence.
- Fraud never auto-rejects. The worst this stage can do to a member is ask a human to look.

### 4. Pipeline completion

Status moves `DECIDED` → `FINALIZED`. The claim record now carries: the submission, classifications, document reads, consistency verdicts, the prep result, the decision with its money breakdown, fraud signals, and the full trace from intake to here — everything the eval report and the ops UI need, with no further processing.

### 5. Tests

Threshold checks and override logic are pure code — direct unit tests; the assessor uses the fake-LLM approach:

- TC009 path: history with 3 same-day claims → `MANUAL_REVIEW` (not `REJECTED`), output names the signal with both numbers (4 claims, limit 2).
- Monthly-limit trip → `MANUAL_REVIEW` with the specific count.
- High-value claim above ₹25,000 → `MANUAL_REVIEW` even with a clean history.
- Fake assessor returns score 0.85 → `MANUAL_REVIEW`, signals from the assessor appear in the output.
- Fake assessor returns score 0.30 → decision unchanged, signals present in trace only.
- Assessor failure (raises twice) → threshold checks still enforced, claim reaches `FINALIZED` with a `SKIPPED` event and lower confidence. Nothing crashes.
- Already-rejected claim with tripped thresholds → stays `REJECTED`, signals recorded in trace.

## Component contracts

**Fraud Assessor**
- In: claim summary + claims history + collected soft signals (free text from Steps 2–4)
- Out: `FraudAssessment {fraud_score, signals[{name, severity, explanation}]}`
- Errors: `ASSESSOR_CALL_FAILED` (provider error verbatim), `ASSESSOR_BAD_OUTPUT` (schema invalid after one retry). Caught inside the stage; thresholds remain enforced regardless.

**Fraud stage**
- In: `ClaimRecord` (status `DECIDED`)
- Out: same `ClaimRecord`, decision possibly overridden to `MANUAL_REVIEW` with signals listed, status `FINALIZED`
- Errors: none escape

## What Step 6 deliberately leaves out

- Cross-member fraud patterns (the same provider billing many members, networks of colluding claims) — needs a claims database spanning members and time, out of scope for this assignment; noted in the architecture document as the natural next step at scale.
- Automated rejection on fraud — deliberately never built; routing to humans is the designed ceiling.

---

# After Step 6: what remains to ship

The pipeline is complete at Step 6. The remaining work is packaging, not processing — each its own small step: the **submission + ops-review UI** (dropdowns from `GET /policy/meta`, decision view with the full trace), the **eval runner** that feeds all 12 test cases through and produces the eval report (deliverable #4), and the **architecture document + component contracts** (deliverables #2 and #3 — mostly assembled from the contract sections of this plan), plus the demo video.
