# Eval Report — 12 Test Cases

**Result: 12/12 PASS** · generated 2026-06-11 15:56 UTC · total runtime 173.1s

Reproduce: `ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_eval.py`

How the cases are fed: each `input` is POSTed through the same in-process service the API uses, with `submission_date` pinned to the treatment date so the 30-day submission window holds for the 2024-dated scenarios. Documents are test-case stubs, so the vision classifier and reader are bypassed by the stub adapter (zero LLM calls there, deterministic); the consistency checker, decision prep, and fraud assessor are live LLM calls. TC001/TC002 stop at the deterministic document gate and use no LLM at all.

Models: classifier `claude-sonnet-4-6`, reader `claude-opus-4-8`, consistency `claude-opus-4-8`, prep `claude-opus-4-8`, fraud assessor `claude-opus-4-8`.

| Case | Name | Expected | Actual | Result |
|---|---|---|---|---|
| TC001 | Wrong Document Uploaded | stop (no decision) | NEEDS_RESUBMISSION (no decision) | ✅ PASS |
| TC002 | Unreadable Document | stop (no decision) | NEEDS_RESUBMISSION (no decision) | ✅ PASS |
| TC003 | Documents Belong to Different Patients | stop (no decision) | NEEDS_RESUBMISSION (no decision) | ✅ PASS |
| TC004 | Clean Consultation — Full Approval | APPROVED ₹1,350 | APPROVED ₹1,350 | ✅ PASS |
| TC005 | Waiting Period — Diabetes | REJECTED | REJECTED | ✅ PASS |
| TC006 | Dental Partial Approval — Cosmetic Exclusion | PARTIAL ₹8,000 | PARTIAL ₹8,000 | ✅ PASS |
| TC007 | MRI Without Pre-Authorization | REJECTED | REJECTED | ✅ PASS |
| TC008 | Per-Claim Limit Exceeded | REJECTED | REJECTED | ✅ PASS |
| TC009 | Fraud Signal — Multiple Same-Day Claims | MANUAL_REVIEW | MANUAL_REVIEW | ✅ PASS |
| TC010 | Network Hospital — Discount Applied | APPROVED ₹3,240 | APPROVED ₹3,240 | ✅ PASS |
| TC011 | Component Failure — Graceful Degradation | APPROVED | APPROVED ₹4,000 | ✅ PASS |
| TC012 | Excluded Treatment | REJECTED | REJECTED | ✅ PASS |

---

## TC001 — Wrong Document Uploaded — ✅ PASS

*Member submits two prescriptions for a consultation claim that requires a prescription and a hospital bill.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | null — stop before any decision | status=NEEDS_RESUBMISSION, decision=None | ✅ |

**System must:**

- ✅ Stop before making any claim decision
  - evidence: `status=NEEDS_RESUBMISSION, decision=None`
- ✅ Tell the member specifically what document type was uploaded and what is needed instead
  - evidence: `WRONG_DOCUMENT_TYPE: You uploaded 2 prescriptions. A CONSULTATION claim needs a prescription and a hospital bill. Please upload the hospital bill for this visit. NEXT: Submit the claim again including the hospital bill …`
- ✅ Not return a generic error — the message must name the uploaded document type and the required document type
  - evidence: `WRONG_DOCUMENT_TYPE: You uploaded 2 prescriptions. A CONSULTATION claim needs a prescription and a hospital bill. Please upload the hospital bill for this visit. NEXT: Submit the claim again including the hospital bill …`

<details><summary><b>Decision output</b></summary>

```json
null
```
</details>

<details><summary><b>Full trace</b> (16 events, final confidence 1.00)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP001 found in roster: Rajesh Kumar.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹1,500 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-11-01 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / file_type:F001
          File 'dr_sharma_prescription.jpg' has allowed type 'jpg'.
[PASS   ] intake / file_type:F002
          File 'another_prescription.jpg' has allowed type 'jpg'.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-12-01).
[PASS   ] intake / minimum_amount
          Claimed amount ₹1,500 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_639EBF38B3EE.
[PASS   ] document_check / classify:F001
          Stub document: 'dr_sharma_prescription.jpg' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F002
          Stub document: 'another_prescription.jpg' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('dr_sharma_prescription.jpg' and 'another_prescription.jpg').
[FAIL   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: missing — nothing uploaded was classified as this type.
[FAIL   ] document_check / requirement_check
          1 document problem(s) found; claim stopped for resubmission before any extraction or decision. No decision was made.
```
</details>

---

## TC002 — Unreadable Document — ✅ PASS

*Member uploads a valid prescription but a blurry, unreadable photo of their pharmacy bill.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | null — stop before any decision | status=NEEDS_RESUBMISSION, decision=None | ✅ |

**System must:**

- ✅ Identify that the pharmacy bill cannot be read
  - evidence: `UNREADABLE_DOCUMENT: We couldn't read 'blurry_bill.jpg' (the pharmacy bill). The prescription you sent is fine. NEXT: Take a clearer, well-lit photo of just the pharmacy bill and submit the claim again — the other docum…`
- ✅ Ask the member to re-upload that specific document
  - evidence: `We couldn't read 'blurry_bill.jpg' (the pharmacy bill). The prescription you sent is fine. NEXT: Take a clearer, well-lit photo of just the pharmacy bill and submit the claim again — the other documents don't need to ch…`
- ✅ Not reject the claim outright
  - evidence: `status=NEEDS_RESUBMISSION, decision=None`

<details><summary><b>Decision output</b></summary>

```json
null
```
</details>

<details><summary><b>Full trace</b> (16 events, final confidence 1.00)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP004 found in roster: Sneha Reddy.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category PHARMACY is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹800 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-25 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / file_type:F003
          File 'prescription.jpg' has allowed type 'jpg'.
[PASS   ] intake / file_type:F004
          File 'blurry_bill.jpg' has allowed type 'jpg'.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-24).
[PASS   ] intake / minimum_amount
          Claimed amount ₹800 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_7A76CE359C14.
[PASS   ] document_check / classify:F003
          Stub document: 'prescription.jpg' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F004
          Stub document: 'blurry_bill.jpg' typed as PHARMACY_BILL (quality UNREADABLE) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('prescription.jpg').
[FAIL   ] document_check / required_document:PHARMACY_BILL
          Required document PHARMACY_BILL: present but unreadable.
[FAIL   ] document_check / requirement_check
          1 document problem(s) found; claim stopped for resubmission before any extraction or decision. No decision was made.
```
</details>

---

## TC003 — Documents Belong to Different Patients — ✅ PASS

*The prescription is for Rajesh Kumar but the hospital bill is for a different patient, Arjun Mehta.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | null — stop before any decision | status=NEEDS_RESUBMISSION, decision=None | ✅ |

**System must:**

- ✅ Detect that the documents belong to different people
  - evidence: `PATIENT_MISMATCH: The prescription names 'Rajesh Kumar' (the member) while the hospital bill names 'Arjun Mehta', a clearly different person not matching the member or any registered dependent (closest dependent is 'Arj…`
- ✅ Surface this to the member with the specific names found on each document
  - evidence: `PATIENT_MISMATCH: The prescription names 'Rajesh Kumar' (the member) while the hospital bill names 'Arjun Mehta', a clearly different person not matching the member or any registered dependent (closest dependent is 'Arj…`
- ✅ Not proceed to a claim decision
  - evidence: `status=NEEDS_RESUBMISSION, decision=None`

<details><summary><b>Decision output</b></summary>

```json
null
```
</details>

<details><summary><b>Full trace</b> (27 events, final confidence 0.95)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP001 found in roster: Rajesh Kumar.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹1,500 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-11-01 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / file_type:F005
          File 'prescription_rajesh.jpg' has allowed type 'jpg'.
[PASS   ] intake / file_type:F006
          File 'bill_arjun.jpg' has allowed type 'jpg'.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-12-01).
[PASS   ] intake / minimum_amount
          Claimed amount ₹1,500 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_CBFF2E0F384A.
[PASS   ] document_check / classify:F005
          Stub document: 'prescription_rajesh.jpg' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F006
          Stub document: 'bill_arjun.jpg' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('prescription_rajesh.jpg').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('bill_arjun.jpg').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F005
          Stub document: content of 'prescription_rajesh.jpg' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F006
          Stub document: content of 'bill_arjun.jpg' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[FAIL   ] consistency_checks / patient_identity
          The prescription names 'Rajesh Kumar' (the member) while the hospital bill names 'Arjun Mehta', a clearly different person not matching the member or any registered dependent (closest dependent is 'Arjun Kumar'). Both names were read with full confidence. [F005 (prescription): 'Rajesh Kumar'; F006 (hospital_bill): 'Arjun Mehta']
[PASS   ] consistency_checks / date_consistency
          Neither document carries a date, so nothing contradicts the stated treatment date of 2024-11-01; the absence is noted but is not a finding. [No dates present on F005 or F006.]
[PASS   ] consistency_checks / amount_consistency
          The hospital bill carries no amount, so the claimed ₹1,500 could not be cross-checked; nothing present contradicts it. [No amount extracted from F006.]
[PASS   ] consistency_checks / line_item_sums
          No bill lists line items, so there is nothing to sum and verify. [F006 has no line items.]
[PASS   ] consistency_checks / doctor_consistency
          Neither the prescription nor the bill records a doctor name, so there is nothing to compare; absence noted. [No doctor named on F005 or F006.]
[WARN   ] consistency_checks / side_by_side
          The documents name two different patients — 'Rajesh Kumar' on the prescription and 'Arjun Mehta' on the bill — which is concerning; note that the bill surname 'Mehta' also differs from the family surname 'Kumar'. [F005 patient 'Rajesh Kumar' vs F006 patient 'Arjun Mehta'.]
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.95: 1 consistency warning(s)
[FAIL   ] consistency_checks / consistency_complete
          Documents belong to different patients; claim stopped for resubmission before any decision. No decision was made.
```
</details>

---

## TC004 — Clean Consultation — Full Approval — ✅ PASS

*Complete, valid consultation claim with correct documents, valid member, covered treatment, within all limits.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | APPROVED | APPROVED | ✅ |
| Approved amount | ₹1,350 | ₹1,350 | ✅ |
| Confidence | above 0.85 | 1.00 | ✅ |

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "APPROVED",
  "approved_amount": 1350.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "APPROVED",
      "detail": "Claim covered under CONSULTATION; ₹1,350 approved."
    },
    {
      "code": "COPAY_APPLIED",
      "detail": "10% co-pay applied on consultation category (₹150 deducted)."
    }
  ],
  "rejection_reasons": [],
  "line_item_breakdown": [
    {
      "description": "Consultation Fee",
      "amount": 1000.0,
      "outcome": "APPROVED",
      "reason": null
    },
    {
      "description": "CBC Test",
      "amount": 300.0,
      "outcome": "APPROVED",
      "reason": null
    },
    {
      "description": "Dengue NS1 Test",
      "amount": 200.0,
      "outcome": "APPROVED",
      "reason": null
    }
  ],
  "money_breakdown": [
    {
      "step": "copay",
      "description": "Co-pay 10% applied on ₹1,500 = ₹150 deducted: ₹1,500 → ₹1,350",
      "amount_before": 1500.0,
      "amount_after": 1350.0
    }
  ],
  "confidence": 1.0,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": null,
  "what_to_do_next": null,
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (44 events, final confidence 1.00)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP001 found in roster: Rajesh Kumar.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹1,500 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-11-01 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-12-01).
[PASS   ] intake / minimum_amount
          Claimed amount ₹1,500 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_AD7050918046.
[PASS   ] document_check / classify:F007
          Stub document: 'F007' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F008
          Stub document: 'F008' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F007').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F008').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F007
          Stub document: content of 'F007' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F008
          Stub document: content of 'F008' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[PASS   ] consistency_checks / patient_identity
          Both documents name 'Rajesh Kumar', who is the member (EMP001, SELF). Full-name match. [F007 prescription: 'Rajesh Kumar'; F008 hospital_bill: 'Rajesh Kumar']
[PASS   ] consistency_checks / date_consistency
          Prescription and bill are both dated 2024-11-01, matching the stated treatment date. [F007 date 2024-11-01; F008 date 2024-11-01; treatment_date 2024-11-01]
[PASS   ] consistency_checks / amount_consistency
          The bill total of ₹1,500 exactly matches the claimed amount of ₹1,500. [F008 total 1500; claimed 1500]
[PASS   ] consistency_checks / line_item_sums
          Bill line items 1000+300+200 sum to 1500, matching the stated total. [Consultation 1000 + CBC 300 + Dengue NS1 200 = 1500]
[PASS   ] consistency_checks / doctor_consistency
          Prescription lists Dr. Arun Sharma; the bill does not reference a doctor, so nothing contradicts. Absence noted. [F007 doctor 'Dr. Arun Sharma'; F008 has no doctor field]
[PASS   ] consistency_checks / side_by_side
          Documents are consistent — same patient, date, clinic context, and no duplicate bill numbers or suspicious patterns. [No anomalies found]
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 6 of 6 passed. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 3 line item(s); documented total 1500.0; diagnosis 'Viral Fever' (excluded: no, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-11-01 is 214 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Viral Fever') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[PASS   ] policy_decision / pre_authorization
          No line item requires pre-authorization.
[PASS   ] policy_decision / line_item:Consultation Fee
          'Consultation Fee' (₹1,000) is covered.
[PASS   ] policy_decision / line_item:CBC Test
          'CBC Test' (₹300) is covered.
[PASS   ] policy_decision / line_item:Dengue NS1 Test
          'Dengue NS1 Test' (₹200) is covered.
[PASS   ] policy_decision / payable_base
          Payable base = min(claimed ₹1,500, documented covered total ₹1,500) = ₹1,500 — a member can't be paid more than the documents support; claiming less pays the claimed amount.
[PASS   ] policy_decision / per_claim_ceiling
          Covered base ₹1,500 is within the per-claim ceiling max(per_claim_limit ₹5,000, CONSULTATION sub-limit ₹2,000) = ₹5,000.
[PASS   ] policy_decision / network_discount
          No network discount: 'City Clinic, Bengaluru' is not a network hospital.
[PASS   ] policy_decision / copay
          Co-pay 10% applied SECOND, on the discounted amount ₹1,500: member pays ₹150, payable → ₹1,350.
[PASS   ] policy_decision / annual_opd_limit
          Payable ₹1,350 fits the remaining annual OPD headroom ₹45,000 (limit ₹50,000, YTD ₹5,000).
[PASS   ] policy_decision / decision
          APPROVED: ₹1,350 of claimed ₹1,500.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-11-01 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in November 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹1,500 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor not invoked: no soft signals and no claims history — nothing for the model to weigh.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision APPROVED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC005 — Waiting Period — Diabetes — ✅ PASS

*Member joined 2024-09-01. Claims for diabetes treatment on 2024-10-15, which is within the 90-day waiting period for diabetes.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | REJECTED | REJECTED | ✅ |
| Rejection reasons | WAITING_PERIOD | WAITING_PERIOD | ✅ |

**System must:**

- ✅ State the date from which the member will be eligible for diabetes-related claims
  - evidence: `WAITING_PERIOD: 'Type 2 Diabetes Mellitus' is subject to a 90-day waiting period for 'diabetes'. The member joined on 2024-09-01, so diabetes-related claims are eligible from 2024-11-30; this treatment was on 2024-10-15…`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "REJECTED",
  "approved_amount": 0.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "WAITING_PERIOD",
      "detail": "'Type 2 Diabetes Mellitus' is subject to a 90-day waiting period for 'diabetes'. The member joined on 2024-09-01, so diabetes-related claims are eligible from 2024-11-30; this treatment was on 2024-10-15."
    }
  ],
  "rejection_reasons": [
    "WAITING_PERIOD"
  ],
  "line_item_breakdown": [],
  "money_breakdown": [],
  "confidence": 1.0,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": "2024-11-30",
  "what_to_do_next": "Claims for diabetes-related treatment on or after 2024-11-30 will be accepted.",
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (35 events, final confidence 1.00)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP005 found in roster: Vikram Joshi.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹3,000 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-15 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-14).
[PASS   ] intake / minimum_amount
          Claimed amount ₹3,000 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_D7CFECB28E08.
[PASS   ] document_check / classify:F009
          Stub document: 'F009' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F010
          Stub document: 'F010' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F009').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F010').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F009
          Stub document: content of 'F009' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F010
          Stub document: content of 'F010' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[PASS   ] consistency_checks / patient_identity
          Both documents name 'Vikram Joshi', matching the member (EMP005, SELF). [F009 prescription: 'Vikram Joshi'; F010 hospital_bill: 'Vikram Joshi']
[PASS   ] consistency_checks / date_consistency
          The bill date 2024-10-15 matches the stated treatment date; the prescription carries no date, which is simply an absence and contradicts nothing. [F010 date: 2024-10-15; treatment_date: 2024-10-15; F009 has no date]
[PASS   ] consistency_checks / amount_consistency
          The bill total of ₹3,000 matches the claimed amount of ₹3,000. [F010 total: 3000; claimed: 3000]
[PASS   ] consistency_checks / line_item_sums
          The hospital bill lists only a total with no itemized line items, so there is nothing to sum against. [F010 total: 3000, no line items]
[PASS   ] consistency_checks / doctor_consistency
          The prescription names Dr. Sunil Mehta; the bill references no doctor, which is an absence and contradicts nothing. [F009 doctor: 'Dr. Sunil Mehta'; F010 has no doctor]
[PASS   ] consistency_checks / side_by_side
          Nothing unusual stands out across the documents; everything is consistent. [Consistent patient, date, and amount across F009 and F010]
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 6 of 6 passed. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 1 line item(s); documented total 3000.0; diagnosis 'Type 2 Diabetes Mellitus' (excluded: no, waiting-period key: diabetes); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-10-15 is 44 days after joining (2024-09-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Type 2 Diabetes Mellitus') maps to no excluded condition.
[FAIL   ] policy_decision / waiting_period
          'Type 2 Diabetes Mellitus' is subject to a 90-day waiting period for 'diabetes'. The member joined on 2024-09-01, so diabetes-related claims are eligible from 2024-11-30; this treatment was on 2024-10-15.
[FAIL   ] policy_decision / decision
          REJECTED (WAITING_PERIOD): 'Type 2 Diabetes Mellitus' is subject to a 90-day waiting period for 'diabetes'. The member joined on 2024-09-01, so diabetes-related claims are eligible from 2024-11-30; this treatment was on 2024-10-15.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-10-15 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in October 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹3,000 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor not invoked: no soft signals and no claims history — nothing for the model to weigh.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision REJECTED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC006 — Dental Partial Approval — Cosmetic Exclusion — ✅ PASS

*Bill includes root canal treatment (covered) and teeth whitening (cosmetic, excluded). System must approve only the covered procedure.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | PARTIAL | PARTIAL | ✅ |
| Approved amount | ₹8,000 | ₹8,000 | ✅ |

**System must:**

- ✅ Itemize which line items were approved and which were rejected
  - evidence: `Root Canal Treatment: APPROVED; Teeth Whitening: REJECTED`
- ✅ State the reason for each rejection at the line-item level
  - evidence: `Teeth Whitening: excluded_procedures: Teeth Whitening`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "PARTIAL",
  "approved_amount": 8000.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "PARTIAL_APPROVAL",
      "detail": "₹8,000 approved for the covered items; 1 item(s) excluded."
    },
    {
      "code": "LINE_ITEM_EXCLUDED",
      "detail": "'Teeth Whitening' (₹4,000) was not approved: excluded_procedures: Teeth Whitening."
    }
  ],
  "rejection_reasons": [],
  "line_item_breakdown": [
    {
      "description": "Root Canal Treatment",
      "amount": 8000.0,
      "outcome": "APPROVED",
      "reason": null
    },
    {
      "description": "Teeth Whitening",
      "amount": 4000.0,
      "outcome": "REJECTED",
      "reason": "excluded_procedures: Teeth Whitening"
    }
  ],
  "money_breakdown": [],
  "confidence": 0.95,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": null,
  "what_to_do_next": null,
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (42 events, final confidence 0.95)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP002 found in roster: Priya Singh.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category DENTAL is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹12,000 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-15 is not in the future.
[PASS   ] intake / documents_present
          1 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-14).
[PASS   ] intake / minimum_amount
          Claimed amount ₹12,000 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_13D90EBF73ED.
[PASS   ] document_check / classify:F011
          Stub document: 'F011' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F011').
[PASS   ] document_check / requirement_check
          All required documents for a DENTAL claim are present and readable (hospital bill).
[PASS   ] extraction / read:F011
          Stub document: content of 'F011' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          1 of 1 document(s) read. Status moved to EXTRACTED.
[SKIPPED] consistency_checks / check_not_applicable:doctor_consistency
          doctor_consistency not checked: no prescription in this claim (not a failure — the check does not apply to this claim).
[PASS   ] consistency_checks / patient_identity
          The only document names 'Priya Singh', which matches the member (EMP002, SELF). [F011 HOSPITAL_BILL: 'Priya Singh'; roster member: 'Priya Singh']
[PASS   ] consistency_checks / date_consistency
          The hospital bill carries no date; nothing present contradicts the stated treatment date of 2024-10-15. [F011 has no date field]
[PASS   ] consistency_checks / amount_consistency
          The bill total of ₹12,000 matches the claimed amount of ₹12,000. [F011 total 12000; claimed 12000]
[PASS   ] consistency_checks / line_item_sums
          The line items (₹8,000 Root Canal + ₹4,000 Teeth Whitening) sum exactly to the stated total of ₹12,000. [F011: 8000+4000=12000]
[WARN   ] consistency_checks / side_by_side
          A ₹4,000 'Teeth Whitening' line item appears, which is typically a cosmetic procedure that may not be covered and is worth flagging for review. [F011 line_items include 'Teeth Whitening' 4000]
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.95: 1 consistency warning(s)
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 4 of 5 passed, 1 warning(s) recorded for later stages. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 2 line item(s); documented total 12000.0; diagnosis 'Root Canal Treatment and Teeth Whitening (dental)' (excluded: no, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-10-15 is 197 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Root Canal Treatment and Teeth Whitening (dental)') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[PASS   ] policy_decision / pre_authorization
          No line item requires pre-authorization.
[PASS   ] policy_decision / line_item:Root Canal Treatment
          'Root Canal Treatment' (₹8,000) is covered.
[FAIL   ] policy_decision / line_item:Teeth Whitening
          'Teeth Whitening' (₹4,000) rejected: excluded_procedures: Teeth Whitening (mapping confidence 1.00).
[PASS   ] policy_decision / payable_base
          Payable base = min(claimed ₹12,000, documented covered total ₹8,000) = ₹8,000 — a member can't be paid more than the documents support; claiming less pays the claimed amount.
[PASS   ] policy_decision / per_claim_ceiling
          Covered base ₹8,000 is within the per-claim ceiling max(per_claim_limit ₹5,000, DENTAL sub-limit ₹10,000) = ₹10,000.
[PASS   ] policy_decision / network_discount
          No network discount: the category defines none.
[PASS   ] policy_decision / copay
          No co-pay for DENTAL claims.
[PASS   ] policy_decision / annual_opd_limit
          Payable ₹8,000 fits the remaining annual OPD headroom ₹50,000 (limit ₹50,000, YTD ₹0).
[PASS   ] policy_decision / decision
          PARTIAL: ₹8,000 of claimed ₹12,000.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-10-15 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in October 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹12,000 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor scored this claim 0.08 with 1 signal(s).
[WARN   ] fraud_check / fraud_signal:COSMETIC_LINE_ITEM
          COSMETIC_LINE_ITEM (LOW): Bill F011 includes a ₹4,000 'Teeth Whitening' line item, typically a cosmetic procedure that may not be covered. This is a coverage/exclusion question, not evidence of manipulation — and the policy outcome already excluded it (PARTIAL ₹8,000, matching the ₹8,000 Root Canal). No fraud intent indicated; noted for ops awareness only.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision PARTIAL. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC007 — MRI Without Pre-Authorization — ✅ PASS

*MRI scan costing ₹15,000 submitted without pre-authorization. Policy requires pre-auth for MRI above ₹10,000.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | REJECTED | REJECTED | ✅ |
| Rejection reasons | PRE_AUTH_MISSING | PRE_AUTH_MISSING | ✅ |

**System must:**

- ✅ Explain that pre-authorization was required and not obtained
  - evidence: `PRE_AUTH_MISSING: 'MRI Lumbar Spine' (₹15,000) requires pre-authorization above ₹10,000 (high_value_tests_requiring_pre_auth: MRI), and no pre-authorization was obtained before the treatment. \| NEXT: Ask your treating d…`
- ✅ Tell the member what they should do to resubmit with pre-auth
  - evidence: `PRE_AUTH_MISSING: 'MRI Lumbar Spine' (₹15,000) requires pre-authorization above ₹10,000 (high_value_tests_requiring_pre_auth: MRI), and no pre-authorization was obtained before the treatment. \| NEXT: Ask your treating d…`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "REJECTED",
  "approved_amount": 0.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "PRE_AUTH_MISSING",
      "detail": "'MRI Lumbar Spine' (₹15,000) requires pre-authorization above ₹10,000 (high_value_tests_requiring_pre_auth: MRI), and no pre-authorization was obtained before the treatment."
    }
  ],
  "rejection_reasons": [
    "PRE_AUTH_MISSING"
  ],
  "line_item_breakdown": [],
  "money_breakdown": [],
  "confidence": 0.95,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": null,
  "what_to_do_next": "Ask your treating doctor or the hospital to request pre-authorization from the insurer for this procedure (approval stays valid 30 days), then resubmit the claim with the approval reference attached.",
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (41 events, final confidence 0.95)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP007 found in roster: Suresh Patil.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category DIAGNOSTIC is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹15,000 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-11-02 is not in the future.
[PASS   ] intake / documents_present
          3 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-12-02).
[PASS   ] intake / minimum_amount
          Claimed amount ₹15,000 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_D9B76A15DEDF.
[PASS   ] document_check / classify:F012
          Stub document: 'F012' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F013
          Stub document: 'F013' typed as LAB_REPORT (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F014
          Stub document: 'F014' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F012').
[PASS   ] document_check / required_document:LAB_REPORT
          Required document LAB_REPORT: present and readable ('F013').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F014').
[PASS   ] document_check / requirement_check
          All required documents for a DIAGNOSTIC claim are present and readable (hospital bill, lab report and prescription).
[PASS   ] extraction / read:F012
          Stub document: content of 'F012' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F013
          Stub document: content of 'F013' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F014
          Stub document: content of 'F014' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          3 of 3 document(s) read. Status moved to EXTRACTED.
[WARN   ] consistency_checks / patient_identity
          No document carries any patient name, so identity could not be cross-checked against the member (Suresh Patil) or verified across documents. [F012 prescription: no patient name; F013 lab report: no patient name; F014 hospital bill: no patient name]
[PASS   ] consistency_checks / date_consistency
          No dates are present on any document, so nothing contradicts the stated treatment date of 2024-11-02. Absence of dates is noted, not a finding. [F012, F013, F014 carry no dates]
[PASS   ] consistency_checks / amount_consistency
          The hospital bill total of ₹15,000 matches the claimed amount of ₹15,000. [F014 total: 15000; claimed: 15000]
[PASS   ] consistency_checks / line_item_sums
          The single line item (MRI Lumbar Spine, ₹15,000) sums exactly to the stated bill total of ₹15,000. [F014: line item 15000 = total 15000]
[PASS   ] consistency_checks / doctor_consistency
          The prescription names Dr. Venkat Rao; the lab report and bill reference no doctor, which is not a contradiction. Absence noted. [F012 doctor: Dr. Venkat Rao; F013/F014: no doctor referenced]
[PASS   ] consistency_checks / side_by_side
          The MRI Lumbar Spine flows consistently from prescription to lab report to bill; nothing odd stands out. [MRI Lumbar Spine consistent across F012, F013, F014]
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.95: 1 consistency warning(s)
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 5 of 6 passed, 1 warning(s) recorded for later stages. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 1 line item(s); documented total 15000.0; diagnosis 'Suspected Lumbar Disc Herniation' (excluded: no, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-11-02 is 215 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Suspected Lumbar Disc Herniation') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[FAIL   ] policy_decision / pre_authorization
          'MRI Lumbar Spine' (₹15,000) requires pre-authorization above ₹10,000 (high_value_tests_requiring_pre_auth: MRI), and no pre-authorization was obtained before the treatment.
[FAIL   ] policy_decision / decision
          REJECTED (PRE_AUTH_MISSING): 'MRI Lumbar Spine' (₹15,000) requires pre-authorization above ₹10,000 (high_value_tests_requiring_pre_auth: MRI), and no pre-authorization was obtained before the treatment.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-11-02 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in November 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹15,000 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor scored this claim 0.08 with 1 signal(s).
[WARN   ] fraud_check / fraud_signal:IDENTITY_GAP
          IDENTITY_GAP (LOW): No document carries any patient name, so identity could not be cross-checked against the member (Suresh Patil) or verified across documents. This is a documentation gap, not evidence of manipulation — the prescription, lab report, and bill are otherwise internally consistent (MRI Lumbar Spine ordered, performed, and billed).
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision REJECTED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC008 — Per-Claim Limit Exceeded — ✅ PASS

*Claimed amount of ₹7,500 exceeds the per-claim limit of ₹5,000.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | REJECTED | REJECTED | ✅ |
| Rejection reasons | PER_CLAIM_EXCEEDED | PER_CLAIM_EXCEEDED | ✅ |

**System must:**

- ✅ State the per-claim limit and the claimed amount clearly in the rejection message
  - evidence: `PER_CLAIM_EXCEEDED: Your claim of ₹7,500 exceeds the per-claim limit of ₹5,000 for CONSULTATION claims. \| NEXT: Claims above ₹5,000 cannot be reimbursed under this policy's outpatient cover.`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "REJECTED",
  "approved_amount": 0.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "PER_CLAIM_EXCEEDED",
      "detail": "Your claim of ₹7,500 exceeds the per-claim limit of ₹5,000 for CONSULTATION claims."
    }
  ],
  "rejection_reasons": [
    "PER_CLAIM_EXCEEDED"
  ],
  "line_item_breakdown": [
    {
      "description": "Consultation Fee",
      "amount": 2000.0,
      "outcome": "APPROVED",
      "reason": null
    },
    {
      "description": "Medicines",
      "amount": 5500.0,
      "outcome": "APPROVED",
      "reason": null
    }
  ],
  "money_breakdown": [],
  "confidence": 0.95,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": null,
  "what_to_do_next": "Claims above ₹5,000 cannot be reimbursed under this policy's outpatient cover.",
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (42 events, final confidence 0.95)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP003 found in roster: Amit Verma.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹7,500 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-20 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-19).
[PASS   ] intake / minimum_amount
          Claimed amount ₹7,500 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_393DD63DF2D6.
[PASS   ] document_check / classify:F015
          Stub document: 'F015' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F016
          Stub document: 'F016' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F015').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F016').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F015
          Stub document: content of 'F015' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F016
          Stub document: content of 'F016' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[WARN   ] consistency_checks / patient_identity
          No patient name appears on any document (prescription or bill), so patient identity could not be cross-checked against the member Amit Verma. [F015 prescription: no patient name; F016 hospital_bill: no patient name]
[PASS   ] consistency_checks / date_consistency
          Neither the prescription nor the bill carries a date, so nothing contradicts the stated treatment date of 2024-10-20; absence of dates noted. [F015: no date; F016: no date; treatment_date 2024-10-20]
[PASS   ] consistency_checks / amount_consistency
          The single hospital bill total of ₹7,500 matches the claimed amount of ₹7,500. [F016 total 7500; claimed 7500]
[PASS   ] consistency_checks / line_item_sums
          The bill line items (₹2,000 consultation + ₹5,500 medicines) sum exactly to the stated total of ₹7,500. [F016: 2000+5500=7500]
[PASS   ] consistency_checks / doctor_consistency
          The prescription names Dr. R. Gupta; the bill references no doctor, so there is no contradiction to flag. [F015 doctor: Dr. R. Gupta; F016: no doctor referenced]
[PASS   ] consistency_checks / side_by_side
          Nothing anomalous stands out across the documents; the consultation prescription and bill are internally consistent. [F015 prescription and F016 bill align on a gastroenteritis consultation]
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.95: 1 consistency warning(s)
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 5 of 6 passed, 1 warning(s) recorded for later stages. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 2 line item(s); documented total 7500.0; diagnosis 'Gastroenteritis' (excluded: no, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-10-20 is 202 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Gastroenteritis') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[PASS   ] policy_decision / pre_authorization
          No line item requires pre-authorization.
[PASS   ] policy_decision / line_item:Consultation Fee
          'Consultation Fee' (₹2,000) is covered.
[PASS   ] policy_decision / line_item:Medicines
          'Medicines' (₹5,500) is covered.
[PASS   ] policy_decision / payable_base
          Payable base = min(claimed ₹7,500, documented covered total ₹7,500) = ₹7,500 — a member can't be paid more than the documents support; claiming less pays the claimed amount.
[FAIL   ] policy_decision / per_claim_ceiling
          Covered base ₹7,500 exceeds the per-claim ceiling max(per_claim_limit ₹5,000, CONSULTATION sub-limit ₹2,000) = ₹5,000.
[FAIL   ] policy_decision / decision
          REJECTED (PER_CLAIM_EXCEEDED): Your claim of ₹7,500 exceeds the per-claim limit of ₹5,000 for CONSULTATION claims.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-10-20 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in October 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹7,500 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor scored this claim 0.10 with 1 signal(s).
[WARN   ] fraud_check / fraud_signal:MISSING_PATIENT_IDENTITY
          MISSING_PATIENT_IDENTITY (LOW): No patient name appears on the prescription or hospital bill, so identity could not be cross-checked against member Amit Verma. This is a documentation gap, not manipulation; no other indicators of intent are present.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision REJECTED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC009 — Fraud Signal — Multiple Same-Day Claims — ✅ PASS

*Member EMP008 has already submitted 3 claims today before this one arrives. This is the 4th claim from the same member on the same day.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | MANUAL_REVIEW | MANUAL_REVIEW | ✅ |

**System must:**

- ✅ Flag the unusual same-day claim pattern
  - evidence: `4 claim(s) from this member on 2024-10-30 (including this one); the policy allows 2 per day.`
- ✅ Route to manual review rather than auto-rejecting
  - evidence: `decision=MANUAL_REVIEW, rejection_reasons=[]`
- ✅ Include the specific signals that triggered the flag in the output
  - evidence: `4 claim(s) from this member on 2024-10-30 (including this one); the policy allows 2 per day.`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "MANUAL_REVIEW",
  "approved_amount": 0.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "SAME_DAY_CLAIMS",
      "detail": "4 claim(s) from this member on 2024-10-30 (including this one); the policy allows 2 per day."
    }
  ],
  "rejection_reasons": [],
  "line_item_breakdown": [
    {
      "description": "Billed services (no itemization)",
      "amount": 4800.0,
      "outcome": "APPROVED",
      "reason": null
    }
  ],
  "money_breakdown": [
    {
      "step": "copay",
      "description": "Co-pay 10% applied on ₹4,800 = ₹480 deducted: ₹4,800 → ₹4,320",
      "amount_before": 4800.0,
      "amount_after": 4320.0
    }
  ],
  "confidence": 0.9,
  "manual_review_recommended": true,
  "manual_review_notes": [
    "4 claim(s) from this member on 2024-10-30 (including this one); the policy allows 2 per day."
  ],
  "eligibility_date": null,
  "what_to_do_next": null,
  "computed_policy_outcome": {
    "decision": "APPROVED",
    "approved_amount": 4320.0,
    "currency": "INR",
    "reasons": [
      {
        "code": "APPROVED",
        "detail": "Claim covered under CONSULTATION; ₹4,320 approved."
      },
      {
        "code": "COPAY_APPLIED",
        "detail": "10% co-pay applied on consultation category (₹480 deducted)."
      }
    ],
    "rejection_reasons": [],
    "line_item_breakdown": [
      {
        "description": "Billed services (no itemization)",
        "amount": 4800.0,
        "outcome": "APPROVED",
        "reason": null
      }
    ],
    "money_breakdown": [
      {
        "step": "copay",
        "description": "Co-pay 10% applied on ₹4,800 = ₹480 deducted: ₹4,800 → ₹4,320",
        "amount_before": 4800.0,
        "amount_after": 4320.0
      }
    ],
    "confidence": 0.95,
    "manual_review_recommended": false,
    "manual_review_notes": [],
    "eligibility_date": null,
    "what_to_do_next": null,
    "computed_policy_outcome": null
  }
}
```
</details>

<details><summary><b>Full trace</b> (47 events, final confidence 0.90)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP008 found in roster: Ravi Menon.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹4,800 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-30 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-29).
[PASS   ] intake / minimum_amount
          Claimed amount ₹4,800 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_0EDFA603FE36.
[PASS   ] document_check / classify:F017
          Stub document: 'F017' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F018
          Stub document: 'F018' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F017').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F018').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F017
          Stub document: content of 'F017' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F018
          Stub document: content of 'F018' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[WARN   ] consistency_checks / patient_identity
          No patient name appears on any document — neither the prescription nor the hospital bill carries a name — so identity could not be cross-checked against the member. Per the rule, this is a WARN, not a FAIL. [F017 (prescription): no patient name; F018 (hospital bill): no patient name. Member: Ravi Menon (EMP008, SELF).]
[PASS   ] consistency_checks / date_consistency
          Neither document carries a date, so nothing contradicts the stated treatment date of 2024-10-30. Absence of dates is noted, not a finding. [F017 and F018 contain no dates.]
[PASS   ] consistency_checks / amount_consistency
          The hospital bill total of ₹4,800 matches the claimed amount exactly. [F018 total: 4800; claimed: 4800.]
[PASS   ] consistency_checks / line_item_sums
          The bill lists no line items, only a total, so there is nothing to sum. [F018: only {total: 4800}, no line items.]
[PASS   ] consistency_checks / doctor_consistency
          The prescription names Dr. S. Khan; the bill references no doctor, so there is no contradiction. The absence is noted. [F017 doctor: 'Dr. S. Khan'; F018: no doctor referenced.]
[PASS   ] consistency_checks / side_by_side
          Nothing inconsistent stands out across the two documents beyond the absence of patient names already noted. [F017 prescription (Migraine, Dr. S. Khan) and F018 bill (₹4,800) align with a consultation claim.]
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.95: 1 consistency warning(s)
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 5 of 6 passed, 1 warning(s) recorded for later stages. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 1 line item(s); documented total 4800.0; diagnosis 'Migraine' (excluded: no, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-10-30 is 212 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Migraine') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[PASS   ] policy_decision / pre_authorization
          No line item requires pre-authorization.
[PASS   ] policy_decision / line_item:Billed services (no itemization)
          'Billed services (no itemization)' (₹4,800) is covered.
[PASS   ] policy_decision / payable_base
          Payable base = min(claimed ₹4,800, documented covered total ₹4,800) = ₹4,800 — a member can't be paid more than the documents support; claiming less pays the claimed amount.
[PASS   ] policy_decision / per_claim_ceiling
          Covered base ₹4,800 is within the per-claim ceiling max(per_claim_limit ₹5,000, CONSULTATION sub-limit ₹2,000) = ₹5,000.
[PASS   ] policy_decision / network_discount
          No network discount: 'the hospital' is not a network hospital.
[PASS   ] policy_decision / copay
          Co-pay 10% applied SECOND, on the discounted amount ₹4,800: member pays ₹480, payable → ₹4,320.
[PASS   ] policy_decision / annual_opd_limit
          Payable ₹4,320 fits the remaining annual OPD headroom ₹50,000 (limit ₹50,000, YTD ₹0).
[PASS   ] policy_decision / decision
          APPROVED: ₹4,320 of claimed ₹4,800.
[FAIL   ] fraud_check / same_day_claims
          4 claim(s) from this member on 2024-10-30 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          4 claim(s) from this member in October 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹4,800 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor scored this claim 0.50 with 3 signal(s).
[WARN   ] fraud_check / fraud_signal:PROVIDER_SHOPPING
          PROVIDER_SHOPPING (MEDIUM): On the treatment date 2024-10-30 this member filed four separate consultation claims across four different providers — City Clinic A (₹1,200), City Clinic B (₹1,800), Wellness Center (₹2,100), plus this ₹4,800 claim. Four distinct providers in a single day for what appears to be outpatient consultations is a classic provider-shopping shape and warrants review of whether these visits genuinely occurred.
[WARN   ] fraud_check / fraud_signal:MISSING_PATIENT_IDENTITY
          MISSING_PATIENT_IDENTITY (LOW): Neither the prescription (F017) nor the hospital bill (F018) carries a patient name, so the documents could not be cross-checked against the member. On its own this is a documentation gap, but combined with the same-day multi-provider pattern it weakens any ability to confirm these bills belong to this member.
[WARN   ] fraud_check / fraud_signal:CLAIM_SHAPE
          CLAIM_SHAPE (LOW): A ₹4,800 charge for a single migraine consultation is on the high side for an outpatient OPD visit; not anomalous alone, but worth a glance alongside the multi-clinic same-day activity.
[WARN   ] fraud_check / confidence_adjustment
          Claim confidence reduced from 0.95 to 0.90: sub-threshold fraud signals present (score 0.50)
[WARN   ] fraud_check / fraud_override
          Decision overridden from APPROVED to MANUAL_REVIEW (fraud is never auto-rejected; a human decides). Triggering signals: 4 claim(s) from this member on 2024-10-30 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision MANUAL_REVIEW. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC010 — Network Hospital — Discount Applied — ✅ PASS

*Valid claim at Apollo Hospitals, a network hospital. Network discount must be applied before co-pay.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | APPROVED | APPROVED | ✅ |
| Approved amount | ₹3,240 | ₹3,240 | ✅ |

**System must:**

- ✅ Apply network discount before co-pay, not after
  - evidence: `steps in order: ['network_discount', 'copay']; co-pay applied on ₹3,600 (the discounted amount)`
- ✅ Show the breakdown of discount and co-pay in the decision output
  - evidence: `Network discount 20% (Apollo Hospitals): ₹4,500 → ₹3,600; Co-pay 10% applied on ₹3,600 = ₹360 deducted: ₹3,600 → ₹3,240`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "APPROVED",
  "approved_amount": 3240.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "APPROVED",
      "detail": "Claim covered under CONSULTATION; ₹3,240 approved."
    },
    {
      "code": "COPAY_APPLIED",
      "detail": "10% co-pay applied on consultation category (₹360 deducted)."
    }
  ],
  "rejection_reasons": [],
  "line_item_breakdown": [
    {
      "description": "Consultation Fee",
      "amount": 1500.0,
      "outcome": "APPROVED",
      "reason": null
    },
    {
      "description": "Medicines",
      "amount": 3000.0,
      "outcome": "APPROVED",
      "reason": null
    }
  ],
  "money_breakdown": [
    {
      "step": "network_discount",
      "description": "Network discount 20% (Apollo Hospitals): ₹4,500 → ₹3,600",
      "amount_before": 4500.0,
      "amount_after": 3600.0
    },
    {
      "step": "copay",
      "description": "Co-pay 10% applied on ₹3,600 = ₹360 deducted: ₹3,600 → ₹3,240",
      "amount_before": 3600.0,
      "amount_after": 3240.0
    }
  ],
  "confidence": 1.0,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": null,
  "what_to_do_next": null,
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (43 events, final confidence 1.00)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP010 found in roster: Deepak Shah.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹4,500 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-11-03 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-12-03).
[PASS   ] intake / minimum_amount
          Claimed amount ₹4,500 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_43125F03273F.
[PASS   ] document_check / classify:F019
          Stub document: 'F019' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F020
          Stub document: 'F020' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F019').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F020').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F019
          Stub document: content of 'F019' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F020
          Stub document: content of 'F020' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[PASS   ] consistency_checks / patient_identity
          Both documents name 'Deepak Shah', matching the member (EMP010, SELF). Full-name match. [F019 prescription: 'Deepak Shah'; F020 hospital_bill: 'Deepak Shah']
[PASS   ] consistency_checks / date_consistency
          Neither the prescription nor the bill carries a date, so nothing contradicts the stated treatment date of 2024-11-03. Absence noted. [No dates present on F019 or F020.]
[PASS   ] consistency_checks / amount_consistency
          The bill total of ₹4,500 matches the claimed amount of ₹4,500. [F020 total: 4500; claimed: 4500]
[PASS   ] consistency_checks / line_item_sums
          On F020 the line items (1500 + 3000) sum exactly to the stated total of 4500. [F020: 1500 + 3000 = 4500 = total]
[PASS   ] consistency_checks / doctor_consistency
          The prescription lists Dr. S. Iyer; the bill references no doctor, so there is nothing to contradict. Absence noted. [F019 doctor: 'Dr. S. Iyer'; F020: no doctor named.]
[PASS   ] consistency_checks / side_by_side
          Documents are consistent — same patient, hospital matches member's stated Apollo Hospitals, amounts align. Nothing odd stands out.
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 6 of 6 passed. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 2 line item(s); documented total 4500.0; diagnosis 'Acute Bronchitis' (excluded: no, waiting-period key: no); network hospital: Apollo Hospitals.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-11-03 is 216 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Acute Bronchitis') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[PASS   ] policy_decision / pre_authorization
          No line item requires pre-authorization.
[PASS   ] policy_decision / line_item:Consultation Fee
          'Consultation Fee' (₹1,500) is covered.
[PASS   ] policy_decision / line_item:Medicines
          'Medicines' (₹3,000) is covered.
[PASS   ] policy_decision / payable_base
          Payable base = min(claimed ₹4,500, documented covered total ₹4,500) = ₹4,500 — a member can't be paid more than the documents support; claiming less pays the claimed amount.
[PASS   ] policy_decision / per_claim_ceiling
          Covered base ₹4,500 is within the per-claim ceiling max(per_claim_limit ₹5,000, CONSULTATION sub-limit ₹2,000) = ₹5,000.
[PASS   ] policy_decision / network_discount
          Network discount 20% applied FIRST (Apollo Hospitals matched network entry 'Apollo Hospitals'): ₹4,500 → ₹3,600.
[PASS   ] policy_decision / copay
          Co-pay 10% applied SECOND, on the discounted amount ₹3,600: member pays ₹360, payable → ₹3,240.
[PASS   ] policy_decision / annual_opd_limit
          Payable ₹3,240 fits the remaining annual OPD headroom ₹42,000 (limit ₹50,000, YTD ₹8,000).
[PASS   ] policy_decision / decision
          APPROVED: ₹3,240 of claimed ₹4,500.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-11-03 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in November 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹4,500 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor not invoked: no soft signals and no claims history — nothing for the model to weigh.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision APPROVED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC011 — Component Failure — Graceful Degradation — ✅ PASS

*One component of your system fails mid-processing (simulate with the flag below). The overall pipeline must continue, produce a decision, and make the failure visible in the output with an appropriately reduced confidence score.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | APPROVED | APPROVED | ✅ |

**System must:**

- ✅ Not crash or return a 500 error
  - evidence: `pipeline completed; status=FINALIZED`
- ✅ Indicate in the output that a component failed and was skipped
  - evidence: `skipped_components=['consistency_checks']; trace: Component 'consistency_checks' failed and was skipped; the pipeline continued without it. Error: SimulatedComponentFailure: simulated failure injected into component 'co…`
- ✅ Return a confidence score lower than a normal full-pipeline approval
  - evidence: `confidence 0.75 vs 1.00 for a clean full-pipeline approval`
- ✅ Include a note that manual review is recommended due to incomplete processing
  - evidence: `manual review recommended due to incomplete processing: component(s) skipped during processing: consistency_checks`

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "APPROVED",
  "approved_amount": 4000.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "APPROVED",
      "detail": "Claim covered under ALTERNATIVE_MEDICINE; ₹4,000 approved."
    }
  ],
  "rejection_reasons": [],
  "line_item_breakdown": [
    {
      "description": "Panchakarma Therapy (5 sessions)",
      "amount": 3000.0,
      "outcome": "APPROVED",
      "reason": null
    },
    {
      "description": "Consultation",
      "amount": 1000.0,
      "outcome": "APPROVED",
      "reason": null
    }
  ],
  "money_breakdown": [],
  "confidence": 0.75,
  "manual_review_recommended": true,
  "manual_review_notes": [
    "manual review recommended due to incomplete processing: component(s) skipped during processing: consistency_checks"
  ],
  "eligibility_date": null,
  "what_to_do_next": null,
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (39 events, final confidence 0.75)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP006 found in roster: Kavita Nair.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category ALTERNATIVE_MEDICINE is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹4,000 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-28 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-27).
[PASS   ] intake / minimum_amount
          Claimed amount ₹4,000 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_D24567E4C5C1.
[PASS   ] document_check / classify:F021
          Stub document: 'F021' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F022
          Stub document: 'F022' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F021').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F022').
[PASS   ] document_check / requirement_check
          All required documents for a ALTERNATIVE_MEDICINE claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F021
          Stub document: content of 'F021' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F022
          Stub document: content of 'F022' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[SKIPPED] consistency_checks / stage_execution
          Component 'consistency_checks' failed and was skipped; the pipeline continued without it. Error: SimulatedComponentFailure: simulated failure injected into component 'consistency_checks' (simulate_component_failure=true)
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.75: component 'consistency_checks' was skipped after a failure
[PASS   ] policy_decision / decision_prep
          Prep mapped 2 line item(s); documented total 4000.0; diagnosis 'Chronic Joint Pain; treatment: Panchakarma Therapy' (excluded: no, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-10-28 is 210 days after joining (2024-04-01); past the initial waiting period.
[PASS   ] policy_decision / exclusion_check
          Diagnosis ('Chronic Joint Pain; treatment: Panchakarma Therapy') maps to no excluded condition.
[PASS   ] policy_decision / waiting_period
          Diagnosis maps to no condition-specific waiting period.
[PASS   ] policy_decision / pre_authorization
          No line item requires pre-authorization.
[PASS   ] policy_decision / line_item:Panchakarma Therapy (5 sessions)
          'Panchakarma Therapy (5 sessions)' (₹3,000) is covered.
[PASS   ] policy_decision / line_item:Consultation
          'Consultation' (₹1,000) is covered.
[PASS   ] policy_decision / payable_base
          Payable base = min(claimed ₹4,000, documented covered total ₹4,000) = ₹4,000 — a member can't be paid more than the documents support; claiming less pays the claimed amount.
[PASS   ] policy_decision / per_claim_ceiling
          Covered base ₹4,000 is within the per-claim ceiling max(per_claim_limit ₹5,000, ALTERNATIVE_MEDICINE sub-limit ₹8,000) = ₹8,000.
[PASS   ] policy_decision / network_discount
          No network discount: the category defines none.
[PASS   ] policy_decision / copay
          No co-pay for ALTERNATIVE_MEDICINE claims.
[PASS   ] policy_decision / annual_opd_limit
          Payable ₹4,000 fits the remaining annual OPD headroom ₹50,000 (limit ₹50,000, YTD ₹0).
[PASS   ] policy_decision / decision
          APPROVED: ₹4,000 of claimed ₹4,000.
[WARN   ] policy_decision / manual_review_recommendation
          The APPROVED decision stands, but manual review is recommended due to incomplete processing: component(s) skipped during processing: consistency_checks
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-10-28 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in October 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹4,000 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor not invoked: no soft signals and no claims history — nothing for the model to weigh.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision APPROVED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>

---

## TC012 — Excluded Treatment — ✅ PASS

*Member claims for bariatric consultation and a diet program. Obesity treatment is explicitly excluded under the policy.*

| Field | Expected | Actual | OK |
|---|---|---|---|
| Decision | REJECTED | REJECTED | ✅ |
| Rejection reasons | EXCLUDED_CONDITION | EXCLUDED_CONDITION | ✅ |
| Confidence | above 0.9 | 0.95 | ✅ |

<details><summary><b>Decision output</b></summary>

```json
{
  "decision": "REJECTED",
  "approved_amount": 0.0,
  "currency": "INR",
  "reasons": [
    {
      "code": "EXCLUDED_CONDITION",
      "detail": "The diagnosis/treatment ('Morbid Obesity — BMI 37; Bariatric Consultation and Customised Diet Plan') falls under the policy exclusion 'Obesity and weight loss programs', which is permanently not covered."
    }
  ],
  "rejection_reasons": [
    "EXCLUDED_CONDITION"
  ],
  "line_item_breakdown": [],
  "money_breakdown": [],
  "confidence": 0.95,
  "manual_review_recommended": false,
  "manual_review_notes": [],
  "eligibility_date": null,
  "what_to_do_next": "Excluded conditions cannot be claimed under this policy.",
  "computed_policy_outcome": null
}
```
</details>

<details><summary><b>Full trace</b> (36 events, final confidence 0.95)</summary>

```text
[PASS   ] intake / member_exists
          Member EMP009 found in roster: Anita Desai.
[PASS   ] intake / policy_matches
          Policy ID matches active policy PLUM_GHI_2024.
[PASS   ] intake / category_valid
          Claim category CONSULTATION is covered by the policy.
[PASS   ] intake / amount_positive
          Claimed amount ₹8,000 is a positive number.
[PASS   ] intake / treatment_date_valid
          Treatment date 2024-10-18 is not in the future.
[PASS   ] intake / documents_present
          2 document(s) attached.
[PASS   ] intake / submission_window
          Submitted within 30 days of treatment (deadline was 2024-11-17).
[PASS   ] intake / minimum_amount
          Claimed amount ₹8,000 meets the ₹500 minimum.
[PASS   ] intake / claim_accepted
          All intake checks passed; claim accepted as CLM_39B2BA8FC94D.
[PASS   ] document_check / classify:F023
          Stub document: 'F023' typed as PRESCRIPTION (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / classify:F024
          Stub document: 'F024' typed as HOSPITAL_BILL (quality GOOD) from the submitted fixture — no classifier call.
[PASS   ] document_check / required_document:PRESCRIPTION
          Required document PRESCRIPTION: present and readable ('F023').
[PASS   ] document_check / required_document:HOSPITAL_BILL
          Required document HOSPITAL_BILL: present and readable ('F024').
[PASS   ] document_check / requirement_check
          All required documents for a CONSULTATION claim are present and readable (hospital bill and prescription).
[PASS   ] extraction / read:F023
          Stub document: content of 'F023' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / read:F024
          Stub document: content of 'F024' stored verbatim with extraction confidence 1.00 — no reader call.
[PASS   ] extraction / extraction_complete
          2 of 2 document(s) read. Status moved to EXTRACTED.
[WARN   ] consistency_checks / patient_identity
          Neither the prescription nor the hospital bill carries any patient name, so identity could not be cross-checked against the member Anita Desai at all. [F023 prescription: no patient name; F024 hospital_bill: no patient name]
[PASS   ] consistency_checks / date_consistency
          Neither document carries a date; nothing present contradicts the stated treatment date of 2024-10-18. [No dates on F023 or F024]
[PASS   ] consistency_checks / amount_consistency
          The bill total of ₹8,000 matches the claimed amount of ₹8,000. [F024 total 8000 = claimed 8000]
[PASS   ] consistency_checks / line_item_sums
          Line items ₹3,000 + ₹5,000 sum exactly to the stated total of ₹8,000. [3000+5000=8000]
[PASS   ] consistency_checks / doctor_consistency
          The prescription names Dr. P. Banerjee; the bill lists no doctor, which is a permissible absence and does not contradict anything. [F023: Dr. P. Banerjee; F024: no doctor named]
[PASS   ] consistency_checks / side_by_side
          Documents are coherent — a bariatric consultation prescription matched by a bill for consultation plus diet program; nothing odd stands out.
[WARN   ] consistency_checks / confidence_adjustment
          Claim confidence reduced from 1.00 to 0.95: 1 consistency warning(s)
[PASS   ] consistency_checks / consistency_complete
          Consistency checks complete: 5 of 6 passed, 1 warning(s) recorded for later stages. Status moved to CHECKED.
[PASS   ] policy_decision / decision_prep
          Prep mapped 2 line item(s); documented total 8000.0; diagnosis 'Morbid Obesity — BMI 37; Bariatric Consultation and Customised Diet Plan' (excluded: Obesity and weight loss programs, waiting-period key: no); network hospital: no match.
[PASS   ] policy_decision / membership_timing
          Treatment 2024-10-18 is 200 days after joining (2024-04-01); past the initial waiting period.
[FAIL   ] policy_decision / exclusion_check
          The diagnosis/treatment ('Morbid Obesity — BMI 37; Bariatric Consultation and Customised Diet Plan') falls under the policy exclusion 'Obesity and weight loss programs', which is permanently not covered. (mapping confidence 0.95)
[FAIL   ] policy_decision / decision
          REJECTED (EXCLUDED_CONDITION): The diagnosis/treatment ('Morbid Obesity — BMI 37; Bariatric Consultation and Customised Diet Plan') falls under the policy exclusion 'Obesity and weight loss programs', which is permanently not covered.
[PASS   ] fraud_check / same_day_claims
          1 claim(s) from this member on 2024-10-18 (including this one); the policy allows 2 per day.
[PASS   ] fraud_check / monthly_claims
          1 claim(s) from this member in October 2024 (including this one); the policy allows 6 per month.
[PASS   ] fraud_check / high_value_claim
          Claim value ₹8,000 vs the automatic-review line of ₹25,000.
[PASS   ] fraud_check / fraud_assessor
          Fraud assessor scored this claim 0.10 with 1 signal(s).
[WARN   ] fraud_check / fraud_signal:MISSING_PATIENT_IDENTITY
          MISSING_PATIENT_IDENTITY (LOW): Neither the prescription (F023) nor the hospital bill (F024) carries a patient name, so identity could not be cross-checked against member Anita Desai. This is a documentation gap rather than evidence of manipulation; no altered amounts, mismatched stamps, or other tampering signs present. Worth noting for completeness only.
[PASS   ] fraud_check / fraud_override
          No fraud threshold tripped and no score override; the policy decision stands.
[PASS   ] fraud_check / pipeline_complete
          Fraud check complete; final decision REJECTED. Status moved to FINALIZED — the record carries the full trace from intake to here.
```
</details>
