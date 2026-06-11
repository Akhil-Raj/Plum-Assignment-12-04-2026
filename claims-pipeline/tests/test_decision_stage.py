"""Policy Decision stage: prep wiring, prep-failure fallback, low-confidence
mapping warns, and the end-to-end override behavior (Step 4 flag, TC011 shape)."""
from __future__ import annotations

from datetime import date

import pytest

from app.errors import AgentCallFailed
from app.models import ClaimStatus, DecisionType, TraceResult, UploadedDocument
from tests.conftest import (
    FakeConsistencyChecker,
    FakePrepAgent,
    make_item,
    make_prep,
    make_submission,
    make_verdict,
)


async def test_tc004_end_to_end_approved_with_copay(service_factory):
    prep = FakePrepAgent(result=make_prep([
        make_item("Consultation Fee", 1000),
        make_item("CBC Test", 300),
        make_item("Dengue NS1 Test", 200),
    ]))
    service = service_factory(prep=prep)
    record = await service.submit(make_submission(ytd_claims_amount=5000))

    assert record.status == ClaimStatus.DECIDED
    assert record.decision.decision == DecisionType.APPROVED
    assert record.decision.approved_amount == 1350
    assert record.decision.confidence == 1.0, "clean run: no uncertainty events"
    assert record.prep is not None
    persisted = service.repo.get(record.claim_id)
    assert persisted.decision.approved_amount == 1350


async def test_prep_failure_routes_to_manual_review(service_factory, config):
    prep = FakePrepAgent(error=AgentCallFailed("prep", RuntimeError("socket hang up")))
    service = service_factory(prep=prep)
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.DECIDED, "never a crash, never a guess"
    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    assert record.decision.reasons[0].code == "PREP_FAILED"
    assert "PREP_CALL_FAILED" in record.decision.reasons[0].detail
    skipped = next(
        e for e in record.trace
        if e.stage == "policy_decision" and e.result == TraceResult.SKIPPED
    )
    assert "socket hang up" in skipped.detail
    assert record.confidence == pytest.approx(1.0 - config.confidence.skipped_component_deduction)


async def test_no_readable_content_skips_prep_entirely(service_factory):
    prep = FakePrepAgent()
    service = service_factory(prep=prep)
    record = await service.submit(
        make_submission(documents=[
            UploadedDocument(file_id="F001", file_name="rx.jpg", actual_type="PRESCRIPTION"),
            UploadedDocument(file_id="F002", file_name="bill.jpg", actual_type="HOSPITAL_BILL"),
        ])
    )
    assert prep.calls == 0, "nothing readable: prep must not be called"
    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    assert record.status == ClaimStatus.DECIDED


async def test_low_confidence_mapping_warns_and_lowers_confidence(service_factory, config):
    prep = FakePrepAgent(result=make_prep(
        [make_item("Consultation Fee", 1500)],
        raw_diagnosis="T2DM?", waiting_key="diabetes", diagnosis_confidence=0.4,
    ))
    service = service_factory(prep=prep)
    # EMP001 joined 2024-04-01: the 90-day diabetes waiting period ended long
    # before the 2024-11-01 treatment, so the claim still approves
    record = await service.submit(make_submission())

    assert record.decision.decision == DecisionType.APPROVED
    warn = next(e for e in record.trace if e.check_name == "prep_mapping_confidence")
    assert "diabetes" in warn.detail and "0.40" in warn.detail
    assert record.decision.confidence == pytest.approx(1.0 - config.confidence.warn_deduction)


async def test_step4_identity_flag_forces_manual_review_end_to_end(service_factory):
    checker = FakeConsistencyChecker(
        verdicts={
            "patient_identity": make_verdict(
                "patient_identity", "MANUAL_REVIEW",
                explanation="Initials-only match.",
                evidence="bill.jpg: 'R. Kumar' vs member 'Rajesh Kumar'",
            )
        }
    )
    service = service_factory(consistency=checker)
    record = await service.submit(make_submission())

    assert record.decision.decision == DecisionType.MANUAL_REVIEW
    outcome = record.decision.computed_policy_outcome
    assert outcome is not None and outcome.decision == DecisionType.APPROVED
    assert outcome.approved_amount == 1350, "the reviewer sees what the rules computed"


async def test_tc011_simulated_failure_keeps_approval_with_recommendation(service_factory):
    prep = FakePrepAgent(result=make_prep([
        make_item("Panchakarma Therapy (5 sessions)", 3000),
        make_item("Consultation", 1000),
    ]))
    service = service_factory(prep=prep)
    record = await service.submit(
        make_submission(
            member_id="EMP006",
            claim_category="ALTERNATIVE_MEDICINE",
            treatment_date=date(2024, 10, 28),
            submission_date=date(2024, 10, 28),
            claimed_amount=4000,
            simulate_component_failure=True,
            documents=[
                UploadedDocument(
                    file_id="F021", actual_type="PRESCRIPTION",
                    content={"doctor_name": "Vaidya T. Krishnan", "diagnosis": "Chronic Joint Pain"},
                ),
                UploadedDocument(
                    file_id="F022", actual_type="HOSPITAL_BILL",
                    content={"hospital_name": "Ayur Wellness Centre", "total": 4000},
                ),
            ],
        )
    )

    # TC011's full expected shape: no crash, decision made, failure visible,
    # lower confidence, manual review recommended
    assert record.status == ClaimStatus.DECIDED
    assert record.decision.decision == DecisionType.APPROVED
    assert record.decision.approved_amount == 4000, "alternative medicine has no co-pay"
    assert "consistency_checks" in record.skipped_components
    assert any(e.result == TraceResult.SKIPPED and "simulated" in e.detail for e in record.trace)
    assert record.decision.confidence == pytest.approx(0.75), "visibly lower than a clean run"
    assert record.decision.manual_review_recommended is True
    assert any("consistency_checks" in n for n in record.decision.manual_review_notes)
