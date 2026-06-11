"""Consistency Checks stage: the TC003 gate, the three-way name rule routing,
deterministic check selection, and degradation when the checker fails.

All plumbing uses the fake checker; the real prompt is exercised by the live eval.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.consistency import ConsistencyCheckerAgent, ConsistencyOutput, VerdictOut
from app.errors import AgentBadOutput, AgentCallFailed
from app.models import ClaimStatus, ClaimSubmission, TraceResult, UploadedDocument, VerdictResult
from tests.conftest import FakeConsistencyChecker, make_read, make_submission, make_verdict

TC003_INPUT = next(
    c for c in json.loads(
        (Path(__file__).resolve().parents[1] / "test_cases.json").read_text()
    )["test_cases"]
    if c["case_id"] == "TC003"
)["input"]


def patient_mismatch_checker(confidence: float = 0.95) -> FakeConsistencyChecker:
    return FakeConsistencyChecker(
        verdicts={
            "patient_identity": make_verdict(
                "patient_identity",
                "FAIL",
                confidence=confidence,
                explanation="The prescription is for Rajesh Kumar, but the hospital bill is for Arjun Mehta.",
                evidence="prescription_rajesh.jpg: 'Rajesh Kumar'; bill_arjun.jpg: 'Arjun Mehta'",
            )
        }
    )


# ------------------------------------------------------------------ TC003 gate


async def test_tc003_patient_mismatch_stops_with_both_names(service_factory):
    service = service_factory(consistency=patient_mismatch_checker())
    submission = ClaimSubmission(**TC003_INPUT, submission_date=TC003_INPUT["treatment_date"])
    record = await service.submit(submission)

    assert record.status == ClaimStatus.NEEDS_RESUBMISSION
    assert record.decision is None, "must not proceed to a claim decision"
    problem = record.problems[0]
    assert problem.error_code == "PATIENT_MISMATCH"
    assert "Rajesh Kumar" in problem.message and "Arjun Mehta" in problem.message
    assert problem.what_to_do_next
    persisted = service.repo.get(record.claim_id)
    assert persisted.status == ClaimStatus.NEEDS_RESUBMISSION


async def test_low_confidence_mismatch_routes_to_review_instead_of_stopping(service_factory, config):
    service = service_factory(consistency=patient_mismatch_checker(confidence=0.3))
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.CHECKED, "a blurry name must not bounce a legitimate claim"
    assert record.problems == []
    assert record.manual_review_required is True
    assert any("low confidence" in r for r in record.manual_review_reasons)
    warn = next(e for e in record.trace if e.check_name == "patient_identity")
    assert warn.result == TraceResult.WARN


async def test_abbreviation_only_match_flags_manual_review(service_factory):
    checker = FakeConsistencyChecker(
        verdicts={
            "patient_identity": make_verdict(
                "patient_identity",
                "MANUAL_REVIEW",
                explanation="The bill says 'R. Kumar' which is consistent with member 'Rajesh Kumar' but does not confirm identity.",
                evidence="bill.jpg: 'R. Kumar' vs member 'Rajesh Kumar'",
            )
        }
    )
    service = service_factory(consistency=checker)
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.CHECKED, "not bounced — the documents may be genuine"
    assert record.manual_review_required is True
    assert any("initials only" in r for r in record.manual_review_reasons)
    assert any(
        e.check_name == "patient_identity" and e.result == TraceResult.WARN for e in record.trace
    )


# ------------------------------------------------------------ verdict handling


async def test_all_pass_reaches_checked_with_six_pass_verdicts(service_factory):
    service = service_factory()  # default checker auto-passes every asked check
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.CHECKED
    check_events = [
        e for e in record.trace
        if e.stage == "consistency_checks" and e.check_name in {v.check_id for v in record.verdicts}
    ]
    assert len(check_events) == 6, "consultation rx+bill asks all six checks"
    assert all(e.result == TraceResult.PASS for e in check_events)
    assert record.confidence == 1.0


async def test_sum_mismatch_warns_and_is_stored_for_fraud(service_factory, config):
    checker = FakeConsistencyChecker(
        verdicts={
            "line_item_sums": make_verdict(
                "line_item_sums",
                "WARN",
                explanation="Line items sum to ₹11,800 but the bill states ₹12,000.",
            )
        }
    )
    service = service_factory(consistency=checker)
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.CHECKED, "warnings travel forward, they don't stop"
    assert any("line_item_sums" in s and "11,800" in s for s in record.soft_signals)
    assert record.confidence == pytest.approx(1.0 - config.confidence.warn_deduction)
    assert any(v.check_id == "line_item_sums" and v.result == VerdictResult.WARN for v in record.verdicts)


async def test_fail_on_non_patient_check_is_downgraded_to_warn(service_factory):
    checker = FakeConsistencyChecker(
        verdicts={
            "date_consistency": make_verdict(
                "date_consistency", "FAIL", explanation="The bill predates the prescription."
            )
        }
    )
    service = service_factory(consistency=checker)
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.CHECKED
    event = next(e for e in record.trace if e.check_name == "date_consistency")
    assert event.result == TraceResult.WARN
    assert "only a patient mismatch stops a claim" in event.detail


# ------------------------------------------------------------------ degradation


async def test_checker_failure_degrades_and_recommends_review(service_factory, config):
    checker = FakeConsistencyChecker(
        error=AgentCallFailed("consistency", RuntimeError("socket hang up"))
    )
    service = service_factory(consistency=checker)
    record = await service.submit(make_submission())

    assert record.status == ClaimStatus.CHECKED, "the claim still reaches a decision without it"
    assert "consistency_checks" in record.skipped_components
    skipped = next(
        e for e in record.trace
        if e.stage == "consistency_checks" and e.result == TraceResult.SKIPPED
    )
    assert "CONSISTENCY_CALL_FAILED" in skipped.detail and "socket hang up" in skipped.detail
    assert "manual review recommended" in skipped.detail
    assert record.confidence == pytest.approx(1.0 - config.confidence.skipped_component_deduction)
    assert record.manual_review_required is False, (
        "degraded pipeline recommends review; it does not force the decision to MANUAL_REVIEW"
    )


async def test_no_readable_content_degrades_without_calling_checker(service_factory, config):
    checker = FakeConsistencyChecker()
    service = service_factory(consistency=checker)
    record = await service.submit(
        make_submission(
            documents=[
                UploadedDocument(file_id="F001", file_name="rx.jpg", actual_type="PRESCRIPTION"),
                UploadedDocument(file_id="F002", file_name="bill.jpg", actual_type="HOSPITAL_BILL"),
            ]
        )
    )
    assert checker.asked is None, "nothing readable: the checker must not be called"
    assert record.status == ClaimStatus.CHECKED
    assert "consistency_checks" in record.skipped_components


# ------------------------------------------------- check selection + plumbing


async def test_dental_claim_without_prescription_skips_doctor_check(service_factory):
    checker = FakeConsistencyChecker()
    service = service_factory(consistency=checker)
    record = await service.submit(
        make_submission(
            member_id="EMP002",
            claim_category="DENTAL",
            documents=[
                UploadedDocument(
                    file_id="F011",
                    file_name="dental_bill.jpg",
                    actual_type="HOSPITAL_BILL",
                    content={
                        "hospital_name": "Smile Dental Clinic",
                        "patient_name": "Priya Singh",
                        "line_items": [{"description": "Root Canal Treatment", "amount": 1500}],
                        "total": 1500,
                    },
                )
            ],
        )
    )
    assert checker.asked == [
        "patient_identity", "date_consistency", "amount_consistency", "line_item_sums", "side_by_side",
    ]
    not_applicable = next(
        e for e in record.trace if e.check_name == "check_not_applicable:doctor_consistency"
    )
    assert "no prescription" in not_applicable.detail
    assert record.status == ClaimStatus.CHECKED


async def test_checker_receives_member_and_dependents_for_family_floater(service_factory):
    checker = FakeConsistencyChecker()
    service = service_factory(consistency=checker)
    await service.submit(make_submission())  # EMP001 has two dependents

    assert checker.received["member"].name == "Rajesh Kumar"
    assert {d.name for d in checker.received["dependents"]} == {"Sunita Kumar", "Arjun Kumar"}


# ----------------------------------------------- agent completeness enforcement


class FakeLLM:
    def __init__(self, outputs: list):
        self.outputs = list(outputs)
        self.calls = 0

    async def structured_call(self, **kwargs):
        out = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return out


TWO_CHECKS = [("patient_identity", "q1"), ("date_consistency", "q2")]


def out_with(*check_ids: str) -> ConsistencyOutput:
    return ConsistencyOutput(
        verdicts=[
            VerdictOut(check_id=c, result=VerdictResult.PASS, confidence=0.9, explanation="ok")
            for c in check_ids
        ]
    )


async def agent_check(llm, config):
    agent = ConsistencyCheckerAgent(llm, config)
    return await agent.check(
        reads=[make_read("F001", content={"patient_name": "Rajesh Kumar"})],
        submission=make_submission(),
        member=None,
        dependents=[],
        checks=TWO_CHECKS,
    )


async def test_incomplete_verdicts_are_retried_then_fail(config):
    llm = FakeLLM([out_with("patient_identity"), out_with("patient_identity")])
    with pytest.raises(AgentBadOutput, match="missing \\['date_consistency'\\]"):
        await agent_check(llm, config)
    assert llm.calls == 1 + config.llm.bad_output_retries


async def test_incomplete_verdicts_recover_on_retry(config):
    llm = FakeLLM([out_with("patient_identity"), out_with("patient_identity", "date_consistency")])
    verdicts = await agent_check(llm, config)
    assert [v.check_id for v in verdicts] == ["patient_identity", "date_consistency"]
    assert llm.calls == 2


async def test_unexpected_verdict_ids_are_rejected(config):
    bad = out_with("patient_identity", "date_consistency", "made_up_check")
    llm = FakeLLM([bad, bad])
    with pytest.raises(AgentBadOutput, match="unexpected \\['made_up_check'\\]"):
        await agent_check(llm, config)
