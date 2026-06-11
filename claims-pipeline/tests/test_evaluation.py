"""Eval harness: the field comparisons and system_must checkers agree with
records produced by the scripted pipeline (no LLM, no network)."""
from __future__ import annotations

import json
from pathlib import Path

from app.evaluation import CaseResult, evaluate_case, render_report
from app.models import ClaimSubmission
from tests.conftest import FakePrepAgent, make_item, make_prep

TEST_CASES = {
    c["case_id"]: c
    for c in json.loads(
        (Path(__file__).resolve().parents[1] / "test_cases.json").read_text()
    )["test_cases"]
}


def submission_for(case_id: str) -> ClaimSubmission:
    case = TEST_CASES[case_id]
    return ClaimSubmission(
        **{**case["input"], "submission_date": case["input"]["treatment_date"]}
    )


async def test_tc004_record_satisfies_every_expected_field(service_factory):
    prep = FakePrepAgent(result=make_prep([
        make_item("Consultation Fee", 1000),
        make_item("CBC Test", 300),
        make_item("Dengue NS1 Test", 200),
    ]))
    service = service_factory(prep=prep)
    record = await service.submit(submission_for("TC004"))

    rows, musts = evaluate_case(TEST_CASES["TC004"], record)
    assert rows, "TC004 pins decision, amount, and confidence"
    assert all(ok for *_, ok in rows), rows
    assert musts == []


async def test_evaluation_detects_a_mismatch(service_factory):
    prep = FakePrepAgent(result=make_prep([make_item("Consultation Fee", 1000)]))
    service = service_factory(prep=prep)
    record = await service.submit(submission_for("TC004"))  # approves 900, not 1350

    rows, _ = evaluate_case(TEST_CASES["TC004"], record)
    amount_row = next(r for r in rows if r[0] == "Approved amount")
    assert amount_row[3] is False
    assert "₹1,350" in amount_row[1] and "₹900" in amount_row[2]


async def test_tc001_system_must_checks_pass_with_evidence(service_factory):
    service = service_factory()
    record = await service.submit(submission_for("TC001"))

    rows, musts = evaluate_case(TEST_CASES["TC001"], record)
    assert all(ok for *_, ok in rows)
    assert len(musts) == 3 and all(ok for _, ok, _ in musts)
    assert any("hospital bill" in evidence.lower() for _, _, evidence in musts)


async def test_tc011_system_must_checks_pass(service_factory):
    prep = FakePrepAgent(result=make_prep([
        make_item("Panchakarma Therapy (5 sessions)", 3000),
        make_item("Consultation", 1000),
    ]))
    service = service_factory(prep=prep)
    record = await service.submit(submission_for("TC011"))

    rows, musts = evaluate_case(TEST_CASES["TC011"], record)
    assert all(ok for *_, ok in rows), rows
    assert len(musts) == 4 and all(ok for _, ok, _ in musts), musts


async def test_report_renders_summary_details_and_failures(service_factory, config):
    service = service_factory()
    record = await service.submit(submission_for("TC001"))
    rows, musts = evaluate_case(TEST_CASES["TC001"], record)
    passing = CaseResult(TEST_CASES["TC001"], record, None, 0.1, rows, musts)
    crashed = CaseResult(TEST_CASES["TC004"], None, "RuntimeError: boom", 0.1, [], [])

    report = render_report([passing, crashed], config, key_present=False)
    assert "# Eval Report" in report
    assert "1/2 PASS" in report
    assert "ANTHROPIC_API_KEY" in report, "keyless runs are stamped with a warning"
    assert "TC001" in report and "✅ PASS" in report
    assert "❌ FAIL" in report and "RuntimeError: boom" in report
    assert "Full trace" in report and "Decision output" in report
