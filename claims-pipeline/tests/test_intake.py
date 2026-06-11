"""Intake checks: every check passes on a good claim; each bad input produces the
right specific, actionable error; all failures are collected in one round trip."""
from __future__ import annotations

from datetime import date

from app.models import TraceResult, UploadedDocument
from app.pipeline.intake import run_intake_checks
from tests.conftest import make_submission


def codes(problems):
    return [p.error_code for p in problems]


def find(problems, code):
    return next(p for p in problems if p.error_code == code)


def test_valid_submission_passes_every_check(policy, config):
    events, problems = run_intake_checks(make_submission(), policy, config)
    assert problems == []
    assert all(e.result == TraceResult.PASS for e in events)
    # every check writes a trace event whether it passes or fails
    names = {e.check_name for e in events}
    assert {
        "member_exists", "policy_matches", "category_valid", "amount_positive",
        "treatment_date_valid", "documents_present", "submission_window", "minimum_amount",
    } <= names


def test_unknown_member(policy, config):
    _, problems = run_intake_checks(make_submission(member_id="EMP099"), policy, config)
    p = find(problems, "MEMBER_NOT_FOUND")
    assert "EMP099" in p.message
    assert p.what_to_do_next


def test_policy_mismatch_names_both_ids(policy, config):
    _, problems = run_intake_checks(make_submission(policy_id="OTHER_2023"), policy, config)
    p = find(problems, "POLICY_MISMATCH")
    assert "OTHER_2023" in p.message and "PLUM_GHI_2024" in p.message


def test_unknown_category_lists_valid_ones(policy, config):
    _, problems = run_intake_checks(make_submission(claim_category="MASSAGE"), policy, config)
    p = find(problems, "UNKNOWN_CATEGORY")
    assert "MASSAGE" in p.message and "CONSULTATION" in p.message and "DENTAL" in p.message


def test_non_positive_amount(policy, config):
    _, problems = run_intake_checks(make_submission(claimed_amount=0), policy, config)
    assert "INVALID_AMOUNT" in codes(problems)
    _, problems = run_intake_checks(make_submission(claimed_amount=-100), policy, config)
    assert "INVALID_AMOUNT" in codes(problems)


def test_future_treatment_date(policy, config):
    _, problems = run_intake_checks(
        make_submission(treatment_date=date(2024, 12, 1), submission_date=date(2024, 11, 5)),
        policy,
        config,
    )
    p = find(problems, "INVALID_DATE")
    assert "2024-12-01" in p.message


def test_no_documents(policy, config):
    _, problems = run_intake_checks(make_submission(documents=[]), policy, config)
    assert "NO_DOCUMENTS" in codes(problems)


def test_bad_file_extension_names_the_file(policy, config):
    docs = [UploadedDocument(file_id="F001", file_name="scan.gif", actual_type="PRESCRIPTION")]
    _, problems = run_intake_checks(make_submission(documents=docs), policy, config)
    p = find(problems, "BAD_FILE")
    assert "scan.gif" in p.message and "gif" in p.message
    assert p.file_id == "F001"


def test_oversize_file(policy, config):
    docs = [
        UploadedDocument(
            file_id="F001", file_name="huge.jpg", size_bytes=11 * 1024 * 1024, actual_type="PRESCRIPTION"
        )
    ]
    _, problems = run_intake_checks(make_submission(documents=docs), policy, config)
    p = find(problems, "BAD_FILE")
    assert "huge.jpg" in p.message


def test_stub_documents_without_file_names_are_accepted(policy, config):
    # test-case stubs (e.g. TC004) carry no file_name or bytes; intake must not choke
    docs = [UploadedDocument(file_id="F007", actual_type="PRESCRIPTION", content={"diagnosis": "X"})]
    _, problems = run_intake_checks(make_submission(documents=docs), policy, config)
    assert codes(problems) == []


def test_submission_window_missed_states_the_deadline(policy, config):
    _, problems = run_intake_checks(
        make_submission(treatment_date=date(2024, 9, 1), submission_date=date(2024, 11, 5)),
        policy,
        config,
    )
    p = find(problems, "SUBMISSION_TOO_LATE")
    # deadline = 2024-09-01 + 30 days (policy submission_rules)
    assert "2024-10-01" in p.message and "30" in p.message


def test_below_minimum_amount_states_both_numbers(policy, config):
    _, problems = run_intake_checks(make_submission(claimed_amount=300), policy, config)
    p = find(problems, "BELOW_MINIMUM_AMOUNT")
    assert "₹500" in p.message and "₹300" in p.message


def test_all_failures_collected_in_one_pass(policy, config):
    _, problems = run_intake_checks(
        make_submission(member_id="EMP099", claimed_amount=-1, claim_category="MASSAGE"),
        policy,
        config,
    )
    assert {"MEMBER_NOT_FOUND", "INVALID_AMOUNT", "UNKNOWN_CATEGORY"} <= set(codes(problems))


def test_failing_checks_write_fail_trace_events(policy, config):
    events, _ = run_intake_checks(make_submission(member_id="EMP099"), policy, config)
    member_events = [e for e in events if e.check_name == "member_exists"]
    assert len(member_events) == 1 and member_events[0].result == TraceResult.FAIL
