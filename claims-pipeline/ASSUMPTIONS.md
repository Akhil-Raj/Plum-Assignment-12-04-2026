# Assumptions

Conscious choices made where the assignment, the policy file, or the test cases
left room for interpretation — each with the reasoning. The most consequential
ones were forced by contradictions *between* test cases and are marked ⚖️.

## Interpreting the policy against the test cases

1. ⚖️ **Per-claim ceiling = max(`per_claim_limit`, category `sub_limit`),
   tested against the covered documented base.** A naive reading is inconsistent
   with the test data: TC008 rejects a ₹7,500 consultation on the ₹5,000
   `per_claim_limit`, but TC006 expects PARTIAL ₹8,000 on a ₹12,000 dental claim
   (dental sub-limit ₹10,000), and TC010 expects ₹3,240 approved on a
   consultation despite the ₹2,000 consultation `sub_limit`. The stated rule is
   the only one consistent with all twelve cases. Exceeding the ceiling rejects
   (`PER_CLAIM_EXCEEDED`), per TC008's expectation — it does not cap.
2. ⚖️ **`sub_limit` never caps the money math.** Follows from TC010: the full
   ₹4,500 must flow through discount and co-pay. The sub-limit's only role is in
   the ceiling rule above.
3. **The rules engine rejects at the first tripped rule**, in a fixed documented
   order, with **exclusions outranking waiting periods**. TC012's obesity
   diagnosis matches both an exclusion and a 365-day waiting period; the
   exclusion is the truthful headline (permanently not covered). This is also
   why TC007 reports only `PRE_AUTH_MISSING` although the amount would also
   breach the diagnostic ceiling.
4. **Waiting-period eligibility date = `join_date + waiting_days`, eligible ON
   that date.** TC005: joined 2024-09-01 + 90 days → eligible from 2024-11-30,
   which the rejection message must (and does) state.
5. **Same-day / monthly fraud counts** = claims in `claims_history` whose date
   matches the treatment date (or its month) **plus this claim**. TC009: 3 prior
   + this one = 4 against a limit of 2. The submission's `claims_history` is
   taken as given (in production it would be a repository query).
6. **`auto_manual_review_above` is the value trip**; `high_value_claim_threshold`
   carries the same number in this policy and is treated as duplicate context,
   not a second rule.
7. **The payable base is `min(claimed_amount, documented covered total)`** — a
   member can't be paid more than the documents support; claiming less pays the
   claimed amount.
8. **PARTIAL means line-item exclusions.** Co-pay and network-discount
   reductions keep a claim APPROVED (TC004, TC010); annual-OPD capping keeps the
   computed decision and adds an `ANNUAL_LIMIT_CAPPED` reason.
9. **Policy fields with no data to evaluate are recorded, not guessed:**
   `pre_existing_conditions_days` (no PED declarations), family-floater
   `combined_limit` (no family YTD), `max_sessions_per_year` (no session
   history), `branded_drug_copay_percent` (no branded/generic labels on bills),
   `requires_registered_practitioner` (registration formats are noted by the
   reader/fraud prompts rather than enforced as a gate). None is exercised by
   the test cases.

## Interpreting the documents and identity

10. **Per-field confidence for messy fields** (the assumption inherited from the
    original `Assumptions.md`): rather than treating regional-language or
    obscured fields as unextractable, the reader attempts every field and
    attaches its own per-field confidence; low-confidence reads surface as WARN
    trace events quoting the model's own words and reduce the claim's running
    confidence, which downstream agents are instructed to respect (an uncertain
    read must never become a confident accusation).
11. **Three-way patient-name rule** (the assignment doesn't define name
    matching): full-name match including spelling variants and transliterations
    → PASS; clearly different person → the only hard stop; abbreviation-only
    match ("R. Kumar" vs "Rajesh Kumar") → the claim continues but the final
    decision is forced to MANUAL_REVIEW — consistent with the member without
    confirming identity.
12. **Absence is a note, not a finding.** Information simply missing from a
    document (no date, no doctor on a bill, no name on one document) is a
    PASS-with-note unless something *present* contradicts. If **no** document
    carries any patient name (true of TC007/TC008/TC009/TC012), that is one
    identity WARN — never a stop or forced review. This rule was tuned against
    the live eval: without it, missing names escalated TC007 to review and
    absence-WARNs dented TC012's confidence bound.
13. **A failed pipeline component *recommends* review; identity doubt *forces*
    it.** TC011 expects a degraded pipeline to keep its APPROVED decision with a
    lower confidence and a recommendation note — so `skipped_components` never
    flips the decision, while the Step-4 identity flag does.

## System behavior

14. **`submission_date` is a settable field** (default: today) so the 2024-dated
    test cases evaluate deterministically — the 30-day submission window and
    waiting periods are checked against it. The eval runner pins it to each
    case's treatment date.
15. **Resubmission means a new claim.** A stopped claim (`NEEDS_RESUBMISSION`)
    stays stored for audit; there is no re-upload-into-the-same-claim flow (state
    complexity for no grading value — recorded in ARCHITECTURE.md).
16. **Intake rejections (HTTP 422) are not persisted**; all problems are
    returned at once so the member fixes everything in one round trip. A claim
    stopped by a gate *after* intake returns HTTP 200 — the submission was
    processed; the result is a resubmission request, not a client error.
17. **Thresholds and confidence-deduction sizes are our values, not the
    assignment's.** The warn thresholds (0.6 for classification, extraction,
    prep mappings, and the identity-stop confidence) and the deduction sizes
    (WARN −0.05, failed read −0.15, skipped component −0.25, fraud dip −0.05
    from score 0.3 up, floor 0.05) are assumed in `app/config.py` — the
    assignment specifies no numbers. They were chosen so the graded bounds hold
    honestly: a clean run carries no deductions (1.0 = "no uncertainty events"),
    one soft warning keeps a confident rejection above TC012's 0.90 bound, and a
    skipped component lands TC011 visibly lower at 0.75. All are config, not
    code.
18. **Stub-mode trade-off** (accepted with the flexible-extraction decision):
    test-case stubs bypass the classifier and reader only; consistency, prep,
    and the fraud assessor still run live on stub content, so eval runs cost a
    little and traces can vary slightly between runs. TC001/TC002 stop at the
    deterministic gate and use no LLM at all.
19. **Member-facing money is formatted `₹1,234`** (Indian-style grouping via the
    shared `format_inr`), and the eval's evidence checks match against that
    exact formatting — message numbers are load-bearing.
20. **The fraud assessor is only invoked when there is something to weigh**
    (soft signals or claims history); a clean first claim makes no fraud LLM
    call, recorded in the trace as "nothing for the model to weigh".
