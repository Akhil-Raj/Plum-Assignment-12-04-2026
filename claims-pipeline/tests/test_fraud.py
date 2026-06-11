"""Fraud Check stage: pure-code threshold checks, the assessor's score override,
and the routing rules — fraud never auto-rejects, rejected claims are not
overridden, and the worst outcome for a member is a human looking at the claim."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.errors import AgentCallFailed
from app.models import (
    ClaimRecord,
    ClaimStatus,
    Decision,
    DecisionType,
    FraudAssessment,
    FraudSignal,
    TraceResult,
)
from app.pipeline.fraud_check import build_stage
from tests.conftest import (
    FakeFraudAssessor,
    FakePrepAgent,
    make_item,
    make_prep,
    make_submission,
)

TC009_INPUT = next(
    c for c in json.loads(
        (Path(__file__).resolve().parents[1] / "test_cases.json").read_text()
    )["test_cases"]
    if c["case_id"] == "TC009"
)["input"]


# --------------------------------------------------------------- TC009 end to end


async def test_tc009_same_day_pattern_routes_to_manual_review(service_factory):
    from app.models import ClaimSubmission

    submission = ClaimSubmission(**TC009_INPUT, submission_date=TC009_INPUT["treatment_date"])
    prep = FakePrepAgent(result=make_prep([make_item("Billed services (no itemization)", 4800)]))
    assessor = FakeFraudAssessor(
        result=FraudAssessment(
            fraud_score=0.55,
            signals=[FraudSignal(
                name="PROVIDER_SHOPPING", severity="MEDIUM",
                explanation="Four claims at four different providers on one day.",
            )],
            source="llm",
        )
    )
    service = service_factory(prep=prep, fraud_assessor=assessor)
    record = await service.submit(submission)

    assert record.status == ClaimStatus.FINALIZED
    assert record.decision.decision == DecisionType.MANUAL_REVIEW, "routed, not auto-rejected"
    assert record.decision.rejection_reasons == [], "this is not a rejection"
    # the specific signal, with both numbers, is in the output (TC009 grades this)
    same_day_reason = next(r for r in record.decision.reasons if r.code == "SAME_DAY_CLAIMS")
    assert "4 claim(s)" in same_day_reason.detail and "2 per day" in same_day_reason.detail
    # the computed policy outcome stays attached for the reviewer
    outcome = record.decision.computed_policy_outcome
    assert outcome is not None and outcome.decision == DecisionType.APPROVED
    assert outcome.approved_amount == pytest.approx(4320), "4800 minus 10% consultation co-pay"
    assert assessor.calls == 1, "history present -> the assessor weighs the soft picture"


# ------------------------------------------------------------- threshold checks


async def test_monthly_limit_trip(service_factory):
    history = [
        {"claim_id": f"CLM_{i}", "date": f"2024-10-{i + 10:02d}", "amount": 600, "provider": "Clinic"}
        for i in range(6)
    ]
    service = service_factory()
    record = await service.submit(
        make_submission(
            treatment_date=date(2024, 10, 30),
            submission_date=date(2024, 10, 30),
            claims_history=history,
        )
    )
    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    reason = next(r for r in record.decision.reasons if r.code == "MONTHLY_CLAIMS")
    assert "7 claim(s)" in reason.detail and "6 per month" in reason.detail


async def test_high_value_claim_trips_even_with_clean_history(policy, config):
    # claims above ₹25,000 are rejected by the per-claim ceiling long before fraud,
    # so the override path is exercised stage-level with a hand-built APPROVED claim
    submission = make_submission(claimed_amount=26000)
    record = ClaimRecord(claimed_amount=26000, submission=submission)
    record.decision = Decision(decision=DecisionType.APPROVED, approved_amount=26000)

    await build_stage(policy, config, FakeFraudAssessor())(record)

    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    reason = next(r for r in record.decision.reasons if r.code == "HIGH_VALUE_CLAIM")
    assert "₹26,000" in reason.detail and "₹25,000" in reason.detail
    assert record.status == ClaimStatus.FINALIZED


async def test_clean_first_claim_never_calls_the_assessor(service_factory):
    assessor = FakeFraudAssessor()
    service = service_factory(fraud_assessor=assessor)
    record = await service.submit(make_submission())

    assert assessor.calls == 0, "no soft signals, no history — nothing to weigh"
    assert record.status == ClaimStatus.FINALIZED
    assert record.decision.decision == DecisionType.APPROVED
    assert record.confidence == 1.0
    not_invoked = next(e for e in record.trace if e.check_name == "fraud_assessor")
    assert not_invoked.result == TraceResult.PASS and "not invoked" in not_invoked.detail


# ------------------------------------------------------------- score override


def scored_assessor(score: float) -> FakeFraudAssessor:
    return FakeFraudAssessor(
        result=FraudAssessment(
            fraud_score=score,
            signals=[FraudSignal(
                name="DOCUMENT_ALTERATION", severity="HIGH",
                explanation="The bill total appears crossed out and rewritten.",
            )],
            source="llm",
        )
    )


async def test_score_at_threshold_overrides_to_manual_review(service_factory):
    # soft signals exist (scripted via consistency WARN) so the assessor runs
    from tests.conftest import FakeConsistencyChecker, make_verdict

    checker = FakeConsistencyChecker(
        verdicts={"line_item_sums": make_verdict("line_item_sums", "WARN", explanation="sum mismatch")}
    )
    service = service_factory(consistency=checker, fraud_assessor=scored_assessor(0.85))
    record = await service.submit(make_submission())

    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    reason = next(r for r in record.decision.reasons if r.code == "FRAUD_SCORE")
    assert "0.85" in reason.detail and "0.80" in reason.detail
    assert "crossed out" in reason.detail, "the triggering signals are named in the output"
    assert record.decision.computed_policy_outcome is not None


async def test_score_below_threshold_keeps_decision_with_slight_dip(service_factory, config):
    from tests.conftest import FakeConsistencyChecker, make_verdict

    checker = FakeConsistencyChecker(
        verdicts={"line_item_sums": make_verdict("line_item_sums", "WARN", explanation="sum mismatch")}
    )
    service = service_factory(consistency=checker, fraud_assessor=scored_assessor(0.30))
    record = await service.submit(make_submission())

    assert record.decision.decision == DecisionType.APPROVED, "the decision stands"
    assert any(e.check_name == "fraud_signal:DOCUMENT_ALTERATION" for e in record.trace)
    # one consistency WARN + one sub-threshold fraud dip
    expected = 1.0 - config.confidence.warn_deduction - config.confidence.fraud_signal_deduction
    assert record.confidence == pytest.approx(expected)
    assert record.fraud.fraud_score == 0.30


# ----------------------------------------------------------------- degradation


async def test_assessor_failure_keeps_thresholds_enforced(service_factory, config):
    from app.models import ClaimSubmission

    submission = ClaimSubmission(**TC009_INPUT, submission_date=TC009_INPUT["treatment_date"])
    prep = FakePrepAgent(result=make_prep([make_item("Billed services (no itemization)", 4800)]))
    assessor = FakeFraudAssessor(error=AgentCallFailed("assessor", RuntimeError("socket hang up")))
    service = service_factory(prep=prep, fraud_assessor=assessor)
    record = await service.submit(submission)

    assert record.status == ClaimStatus.FINALIZED, "nothing crashes"
    assert record.decision.decision == DecisionType.MANUAL_REVIEW, "hard limits still enforced"
    assert any(r.code == "SAME_DAY_CLAIMS" for r in record.decision.reasons)
    skipped = next(e for e in record.trace if e.check_name == "fraud_assessor")
    assert skipped.result == TraceResult.SKIPPED
    assert "ASSESSOR_CALL_FAILED" in skipped.detail and "socket hang up" in skipped.detail
    assert record.fraud.source == "skipped"
    assert record.confidence < 1.0


# ------------------------------------------------------------------ no override


async def test_already_rejected_claim_is_not_overridden(service_factory):
    from app.models import ClaimSubmission

    # TC009's history (3 same-day claims) on a claim that gets REJECTED by the
    # waiting period: thresholds trip, but there is no payment to protect
    submission = make_submission(
        member_id="EMP005",
        treatment_date=date(2024, 10, 15),
        submission_date=date(2024, 10, 20),
        claimed_amount=3000,
        claims_history=[
            {"claim_id": "CLM_1", "date": "2024-10-15", "amount": 1000, "provider": "A"},
            {"claim_id": "CLM_2", "date": "2024-10-15", "amount": 1200, "provider": "B"},
            {"claim_id": "CLM_3", "date": "2024-10-15", "amount": 900, "provider": "C"},
        ],
    )
    prep = FakePrepAgent(result=make_prep(
        [make_item("Billed services (no itemization)", 3000)],
        raw_diagnosis="Type 2 Diabetes Mellitus", waiting_key="diabetes",
    ))
    service = service_factory(prep=prep)
    record = await service.submit(submission)

    assert record.decision.decision == DecisionType.REJECTED, "rejection stands"
    assert record.decision.rejection_reasons == ["WAITING_PERIOD"]
    trip = next(e for e in record.trace if e.check_name == "same_day_claims")
    assert trip.result == TraceResult.FAIL, "the signal is still recorded in the trace"
    override = next(e for e in record.trace if e.check_name == "fraud_override")
    assert "no payment to protect" in override.detail
    assert record.status == ClaimStatus.FINALIZED


# -------------------------------------------------------------------- defensive


async def test_missing_decision_is_substituted_for_manual_review(policy, config):
    # if the decision stage was skipped by the runner, the claim must still leave
    # the pipeline with an actionable outcome
    submission = make_submission()
    record = ClaimRecord(claimed_amount=submission.claimed_amount, submission=submission)
    assert record.decision is None

    await build_stage(policy, config, FakeFraudAssessor())(record)

    assert record.decision is not None
    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    assert record.decision.reasons[0].code == "DECISION_MISSING"
    assert record.status == ClaimStatus.FINALIZED
