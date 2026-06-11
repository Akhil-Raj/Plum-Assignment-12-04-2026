"""Rules Engine: pure code over a hand-built prep output — no LLM, no network.
Each graded money/rejection scenario (TC004-TC012) is a direct unit test."""
from __future__ import annotations

from datetime import date

import pytest

from app.models import ClaimRecord, DecisionType, TraceResult
from app.pipeline.rules_engine import evaluate_rules
from tests.conftest import make_item, make_prep, make_submission


def build_record(**overrides) -> ClaimRecord:
    submission = make_submission(**overrides)
    return ClaimRecord(claimed_amount=submission.claimed_amount, submission=submission)


def run(record, prep, policy, config):
    return evaluate_rules(record, prep, policy, config)


# --------------------------------------------------------------- approvals + math


def test_tc004_clean_consultation_with_copay(policy, config):
    record = build_record(claimed_amount=1500, ytd_claims_amount=5000)
    prep = make_prep([
        make_item("Consultation Fee", 1000),
        make_item("CBC Test", 300),
        make_item("Dengue NS1 Test", 200),
    ])
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.APPROVED
    assert decision.approved_amount == 1350, "10% co-pay on ₹1,500"
    copay = next(e for e in record.trace if e.check_name == "copay")
    assert "10%" in copay.detail and "₹150" in copay.detail
    assert decision.rejection_reasons == []
    assert decision.money_breakdown[-1].amount_after == 1350


def test_tc010_network_discount_before_copay(policy, config):
    record = build_record(
        member_id="EMP010", treatment_date=date(2024, 11, 3), claimed_amount=4500,
        ytd_claims_amount=8000, hospital_name="Apollo Hospitals",
        submission_date=date(2024, 11, 3),
    )
    prep = make_prep(
        [make_item("Consultation Fee", 1500), make_item("Medicines", 3000)],
        hospital_found="Apollo Hospitals", network_match="Apollo Hospitals",
    )
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.APPROVED
    assert decision.approved_amount == 3240, "4500 → 20% discount → 3600 → 10% copay → 3240"
    steps = decision.money_breakdown
    assert [s.step for s in steps] == ["network_discount", "copay"], "discount must come first"
    assert steps[0].amount_before == 4500 and steps[0].amount_after == 3600
    assert steps[1].amount_before == 3600, "co-pay must apply to the DISCOUNTED amount"
    assert steps[1].amount_after == 3240
    # the wrong way — both percentages on the original (4500 - 450 - 900) — gives 3150
    assert decision.approved_amount != 3150


def test_payable_base_is_min_of_claimed_and_documented(policy, config):
    # claimed more than documented: math runs on the documented covered total
    record = build_record(claimed_amount=2000)
    decision = run(record, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.approved_amount == 1350

    # claimed less than documented: pay the claimed amount
    record = build_record(claimed_amount=1200)
    decision = run(record, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.approved_amount == pytest.approx(1080)


# --------------------------------------------------------------------- rejections


def test_tc005_waiting_period_states_eligibility_date(policy, config):
    record = build_record(
        member_id="EMP005", treatment_date=date(2024, 10, 15), claimed_amount=3000,
        submission_date=date(2024, 10, 20),
    )
    prep = make_prep(
        [make_item("Billed services (no itemization)", 3000)],
        raw_diagnosis="Type 2 Diabetes Mellitus", waiting_key="diabetes",
    )
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.REJECTED
    assert decision.rejection_reasons == ["WAITING_PERIOD"]
    assert decision.eligibility_date == "2024-11-30", "joined 2024-09-01 + 90 days"
    assert "2024-11-30" in decision.reasons[0].detail


def test_tc006_partial_approval_with_itemized_reasons(policy, config):
    record = build_record(
        member_id="EMP002", claim_category="DENTAL", treatment_date=date(2024, 10, 15),
        claimed_amount=12000, submission_date=date(2024, 10, 20),
    )
    prep = make_prep([
        make_item("Root Canal Treatment", 8000, matched="covered_procedures: Root Canal Treatment"),
        make_item("Teeth Whitening", 4000, "EXCLUDED", matched="excluded_procedures: Teeth Whitening"),
    ])
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.PARTIAL
    assert decision.approved_amount == 8000, "dental has no co-pay; only the covered item pays"
    by_desc = {b.description: b for b in decision.line_item_breakdown}
    assert by_desc["Root Canal Treatment"].outcome == "APPROVED"
    assert by_desc["Teeth Whitening"].outcome == "REJECTED"
    assert "Teeth Whitening" in by_desc["Teeth Whitening"].reason
    assert any("Teeth Whitening" in r.detail for r in decision.reasons)


def test_tc007_mri_without_pre_auth(policy, config):
    record = build_record(
        member_id="EMP007", claim_category="DIAGNOSTIC", treatment_date=date(2024, 11, 2),
        claimed_amount=15000, submission_date=date(2024, 11, 2),
    )
    prep = make_prep([
        make_item("MRI Lumbar Spine", 15000, "REQUIRES_PRE_AUTH",
                  matched="high_value_tests_requiring_pre_auth: MRI"),
    ])
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.REJECTED
    assert decision.rejection_reasons == ["PRE_AUTH_MISSING"]
    assert "pre-authorization" in decision.reasons[0].detail
    assert "resubmit" in decision.what_to_do_next.lower()


def test_pre_auth_item_under_threshold_is_covered(policy, config):
    record = build_record(
        member_id="EMP007", claim_category="DIAGNOSTIC", claimed_amount=8000,
    )
    prep = make_prep([
        make_item("MRI Knee", 8000, "REQUIRES_PRE_AUTH",
                  matched="high_value_tests_requiring_pre_auth: MRI"),
    ])
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.APPROVED, "₹8,000 is under the ₹10,000 threshold"
    assert decision.approved_amount == 8000


def test_pre_auth_reference_satisfies_the_requirement(policy, config):
    record = build_record(
        member_id="EMP007", claim_category="DIAGNOSTIC", claimed_amount=15000,
    )
    prep = make_prep(
        [make_item("MRI Lumbar Spine", 15000, "REQUIRES_PRE_AUTH",
                   matched="high_value_tests_requiring_pre_auth: MRI")],
        pre_auth_found=True,
    )
    decision = run(record, prep, policy, config)
    # pre-auth is satisfied; the claim then trips the diagnostic per-claim ceiling
    assert "PRE_AUTH_MISSING" not in decision.rejection_reasons
    assert decision.rejection_reasons == ["PER_CLAIM_EXCEEDED"]


def test_tc008_per_claim_limit_states_both_numbers(policy, config):
    record = build_record(
        member_id="EMP003", treatment_date=date(2024, 10, 20), claimed_amount=7500,
        ytd_claims_amount=10000, submission_date=date(2024, 10, 25),
    )
    prep = make_prep([make_item("Consultation Fee", 2000), make_item("Medicines", 5500)])
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.REJECTED
    assert decision.rejection_reasons == ["PER_CLAIM_EXCEEDED"]
    assert "₹7,500" in decision.reasons[0].detail and "₹5,000" in decision.reasons[0].detail


def test_per_claim_ceiling_uses_covered_base_not_claimed(policy, config):
    """The documented ambiguity resolution: TC006 claims ₹12,000 (over every limit)
    yet must be PARTIAL — the ceiling tests the covered base (₹8,000) against
    max(per_claim_limit ₹5,000, dental sub-limit ₹10,000)."""
    record = build_record(
        member_id="EMP002", claim_category="DENTAL", claimed_amount=12000,
    )
    prep = make_prep([
        make_item("Root Canal Treatment", 8000),
        make_item("Teeth Whitening", 4000, "EXCLUDED", matched="excluded_procedures: Teeth Whitening"),
    ])
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.PARTIAL
    assert "PER_CLAIM_EXCEEDED" not in decision.rejection_reasons


def test_tc012_exclusion_outranks_waiting_period(policy, config):
    record = build_record(
        member_id="EMP009", treatment_date=date(2024, 10, 18), claimed_amount=8000,
        submission_date=date(2024, 10, 20),
    )
    prep = make_prep(
        [make_item("Bariatric Consultation", 3000), make_item("Diet Program", 5000)],
        raw_diagnosis="Morbid Obesity — BMI 37",
        excluded_condition="Obesity and weight loss programs",
        waiting_key="obesity_treatment",  # both apply; the exclusion is the headline
    )
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.REJECTED
    assert decision.rejection_reasons == ["EXCLUDED_CONDITION"], (
        "an excluded condition is permanently not covered — that is the truthful "
        "headline even when a waiting period (or the per-claim limit) also applies"
    )
    assert "Obesity and weight loss programs" in decision.reasons[0].detail


def test_initial_waiting_period(policy, config):
    record = build_record(
        member_id="EMP005", treatment_date=date(2024, 9, 15), claimed_amount=1500,
        submission_date=date(2024, 9, 20),
    )
    decision = run(record, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.rejection_reasons == ["WAITING_PERIOD"]
    assert decision.eligibility_date == "2024-10-01", "joined 2024-09-01 + 30-day initial period"


def test_treatment_before_join_date(policy, config):
    record = build_record(
        member_id="EMP005", treatment_date=date(2024, 8, 15), claimed_amount=1500,
        submission_date=date(2024, 8, 20),
    )
    decision = run(record, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.rejection_reasons == ["MEMBERSHIP_NOT_ACTIVE"]


def test_all_items_excluded_rejects_with_breakdown(policy, config):
    record = build_record(member_id="EMP002", claim_category="DENTAL", claimed_amount=4000)
    prep = make_prep([
        make_item("Teeth Whitening", 4000, "EXCLUDED", matched="excluded_procedures: Teeth Whitening"),
    ])
    decision = run(record, prep, policy, config)
    assert decision.rejection_reasons == ["ALL_ITEMS_EXCLUDED"]
    assert decision.line_item_breakdown[0].outcome == "REJECTED"


def test_annual_opd_cap_and_exhaustion(policy, config):
    capped = build_record(claimed_amount=1500, ytd_claims_amount=49000)
    decision = run(capped, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.decision == DecisionType.APPROVED
    assert decision.approved_amount == 1000, "co-pay gives 1350, headroom caps at 1000"
    assert any(r.code == "ANNUAL_LIMIT_CAPPED" for r in decision.reasons)

    exhausted = build_record(claimed_amount=1500, ytd_claims_amount=50000)
    decision = run(exhausted, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.rejection_reasons == ["ANNUAL_LIMIT_EXHAUSTED"]


def test_no_documented_amounts_routes_to_manual_review(policy, config):
    decision = run(build_record(), make_prep([]), policy, config)
    assert decision.decision == DecisionType.MANUAL_REVIEW
    assert decision.reasons[0].code == "NO_DOCUMENTED_AMOUNTS"


def test_unknown_waiting_key_is_ignored_with_warn(policy, config):
    record = build_record()
    prep = make_prep(
        [make_item("Consultation Fee", 1500)],
        raw_diagnosis="Something", waiting_key="not_a_real_condition",
    )
    decision = run(record, prep, policy, config)
    assert decision.decision == DecisionType.APPROVED
    warn = next(e for e in record.trace if e.check_name == "waiting_period")
    assert warn.result == TraceResult.WARN and "not_a_real_condition" in warn.detail


# ----------------------------------------------------------------- overrides last


def test_step4_identity_flag_forces_manual_review_with_outcome_attached(policy, config):
    record = build_record(claimed_amount=1500)
    record.manual_review_required = True
    record.manual_review_reasons = ["patient identity matched on initials only: 'R. Kumar'"]
    decision = run(record, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.decision == DecisionType.MANUAL_REVIEW
    assert decision.computed_policy_outcome is not None
    assert decision.computed_policy_outcome.decision == DecisionType.APPROVED
    assert decision.computed_policy_outcome.approved_amount == 1350
    assert any("initials only" in r.detail for r in decision.reasons)


def test_degraded_pipeline_keeps_decision_with_recommendation(policy, config):
    record = build_record(claimed_amount=1500)
    record.skipped_components = ["consistency_checks"]
    record.confidence = 0.75  # the skip already cost confidence upstream
    decision = run(record, make_prep([make_item("Consultation Fee", 1500)]), policy, config)
    assert decision.decision == DecisionType.APPROVED, "TC011: the decision stands"
    assert decision.approved_amount == 1350
    assert decision.manual_review_recommended is True
    assert any("consistency_checks" in n for n in decision.manual_review_notes)
    assert decision.confidence == 0.75, "visibly lower than a clean run"
