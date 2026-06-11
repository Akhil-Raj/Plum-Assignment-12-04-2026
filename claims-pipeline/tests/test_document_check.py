"""Document Check stage: the early gate.

All plumbing is tested with a fake classifier (no LLM, no network). Stub documents
exercise the test-case path; "real" documents (no actual_type) exercise the
classifier path with scripted answers and failures.
"""
from __future__ import annotations

import pytest

from app.errors import AgentBadOutput, AgentCallFailed
from app.models import ClaimRecord, ClaimStatus, DocQuality, DocType, TraceResult, UploadedDocument
from app.pipeline.document_check import build_stage
from tests.conftest import FakeClassifier, make_classification, make_submission


async def run_document_check(policy, config, classifier, **submission_overrides) -> ClaimRecord:
    submission = make_submission(**submission_overrides)
    record = ClaimRecord(claimed_amount=submission.claimed_amount, submission=submission)
    await build_stage(policy, config, classifier)(record)
    return record


def stub(file_id, file_name, actual_type, quality=None) -> UploadedDocument:
    return UploadedDocument(
        file_id=file_id, file_name=file_name, actual_type=actual_type, quality=quality
    )


# ----------------------------------------------------------------- the two gates


async def test_tc001_wrong_document_stops_with_both_types_named(policy, config):
    record = await run_document_check(
        policy, config, FakeClassifier(),
        documents=[
            stub("F001", "dr_sharma_prescription.jpg", "PRESCRIPTION"),
            stub("F002", "another_prescription.jpg", "PRESCRIPTION"),
        ],
    )
    assert record.status == ClaimStatus.NEEDS_RESUBMISSION
    assert record.decision is None, "must stop before any claim decision"
    problem = record.problems[0]
    assert problem.error_code == "WRONG_DOCUMENT_TYPE"
    message = problem.message.lower()
    assert "prescription" in message, "must name the uploaded type"
    assert "hospital bill" in message, "must name the required type"
    assert "2 prescriptions" in problem.message
    assert "hospital bill" in problem.what_to_do_next.lower()


async def test_tc002_unreadable_required_doc_asks_for_that_file_only(policy, config):
    record = await run_document_check(
        policy, config, FakeClassifier(),
        claim_category="PHARMACY",
        documents=[
            stub("F003", "prescription.jpg", "PRESCRIPTION", quality="GOOD"),
            stub("F004", "blurry_bill.jpg", "PHARMACY_BILL", quality="UNREADABLE"),
        ],
    )
    assert record.status == ClaimStatus.NEEDS_RESUBMISSION, "stopped, not rejected"
    assert record.decision is None
    problem = record.problems[0]
    assert problem.error_code == "UNREADABLE_DOCUMENT"
    assert problem.file_name == "blurry_bill.jpg"
    assert "blurry_bill.jpg" in problem.message
    assert "pharmacy bill" in problem.message.lower()
    assert "prescription" in problem.message.lower() and "fine" in problem.message
    assert "clearer" in problem.what_to_do_next.lower()


async def test_correct_readable_documents_are_verified(policy, config):
    record = await run_document_check(policy, config, FakeClassifier())
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED
    assert record.problems == []
    verdict = next(e for e in record.trace if e.check_name == "requirement_check")
    assert verdict.result == TraceResult.PASS


# ------------------------------------------------------------- classifier paths


async def test_real_files_go_through_the_classifier(policy, config):
    fake = FakeClassifier(
        results={
            "F001": make_classification("F001", "PRESCRIPTION", file_name="rx.jpg"),
            "F002": make_classification("F002", "HOSPITAL_BILL", file_name="bill.jpg"),
        }
    )
    record = await run_document_check(
        policy, config, fake,
        documents=[
            UploadedDocument(file_id="F001", file_name="rx.jpg"),
            UploadedDocument(file_id="F002", file_name="bill.jpg"),
        ],
    )
    assert sorted(fake.calls) == ["F001", "F002"]
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED


async def test_stub_documents_never_touch_the_classifier(policy, config):
    fake = FakeClassifier()
    record = await run_document_check(policy, config, fake)
    assert fake.calls == []
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED
    assert all(c.source == "stub" for c in record.classifications)


async def test_classifier_failure_falls_back_to_declared_type(policy, config):
    fake = FakeClassifier(
        results={
            "F001": make_classification("F001", "PRESCRIPTION", file_name="rx.jpg"),
            "F002": AgentCallFailed("classifier", RuntimeError("socket hang up")),
        }
    )
    record = await run_document_check(
        policy, config, fake,
        documents=[
            UploadedDocument(file_id="F001", file_name="rx.jpg"),
            UploadedDocument(file_id="F002", file_name="bill.jpg", declared_type=DocType.HOSPITAL_BILL),
        ],
    )
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED, "declared type keeps the claim moving"
    fallback = record.get_classification("F002")
    assert fallback.source == "declared_fallback"
    assert fallback.detected_type == DocType.HOSPITAL_BILL
    warn = next(e for e in record.trace if e.check_name == "classify:F002")
    assert warn.result == TraceResult.WARN
    assert "CLASSIFIER_CALL_FAILED" in warn.detail and "socket hang up" in warn.detail
    assert record.confidence < 1.0


async def test_classifier_failure_without_declared_type_stops_required_doc(policy, config):
    fake = FakeClassifier(
        results={
            "F002": AgentCallFailed("classifier", RuntimeError("connection reset")),
        }
    )
    record = await run_document_check(
        policy, config, fake,
        documents=[
            stub("F001", "rx.jpg", "PRESCRIPTION"),
            UploadedDocument(file_id="F002", file_name="bill.jpg"),  # no declared type
        ],
    )
    assert record.status == ClaimStatus.NEEDS_RESUBMISSION, "pipeline degraded, never crashed"
    problem = record.problems[0]
    assert problem.error_code == "MISSING_DOCUMENT"
    assert "bill.jpg" in problem.message, "must name the file we couldn't process"
    assert "hospital bill" in problem.message.lower()


async def test_bad_output_twice_is_a_classifier_failure(policy, config):
    fake = FakeClassifier(
        results={
            "F001": make_classification("F001", "PRESCRIPTION", file_name="rx.jpg"),
            "F002": AgentBadOutput("classifier", "schema validation failed after 2 attempt(s)"),
        }
    )
    record = await run_document_check(
        policy, config, fake,
        documents=[
            UploadedDocument(file_id="F001", file_name="rx.jpg"),
            UploadedDocument(file_id="F002", file_name="bill.jpg", declared_type=DocType.HOSPITAL_BILL),
        ],
    )
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED
    warn = next(e for e in record.trace if e.check_name == "classify:F002")
    assert "CLASSIFIER_BAD_OUTPUT" in warn.detail


async def test_low_confidence_classification_warns_and_continues(policy, config):
    fake = FakeClassifier(
        results={
            "F001": make_classification("F001", "PRESCRIPTION", file_name="rx.jpg", confidence=0.4),
            "F002": make_classification("F002", "HOSPITAL_BILL", file_name="bill.jpg"),
        }
    )
    record = await run_document_check(
        policy, config, fake,
        documents=[
            UploadedDocument(file_id="F001", file_name="rx.jpg"),
            UploadedDocument(file_id="F002", file_name="bill.jpg"),
        ],
    )
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED
    warn = next(e for e in record.trace if e.check_name == "classification_confidence:F001")
    assert warn.result == TraceResult.WARN and "0.40" in warn.detail
    assert record.confidence == pytest.approx(1.0 - config.confidence.warn_deduction)


# ----------------------------------------------------------- secondary behavior


async def test_unreadable_optional_document_continues_with_warn(policy, config):
    record = await run_document_check(
        policy, config, FakeClassifier(),
        documents=[
            stub("F001", "rx.jpg", "PRESCRIPTION"),
            stub("F002", "bill.jpg", "HOSPITAL_BILL"),
            stub("F003", "lab.jpg", "LAB_REPORT", quality="UNREADABLE"),
        ],
    )
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED
    assert any(e.check_name == "unreadable_optional:F003" for e in record.trace)


async def test_unrelated_document_warns_but_does_not_stop(policy, config):
    record = await run_document_check(
        policy, config, FakeClassifier(),
        documents=[
            stub("F001", "rx.jpg", "PRESCRIPTION"),
            stub("F002", "bill.jpg", "HOSPITAL_BILL"),
            stub("F003", "discharge.jpg", "DISCHARGE_SUMMARY"),
        ],
    )
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED
    warn = next(e for e in record.trace if e.check_name == "unused_document:F003")
    assert warn.result == TraceResult.WARN


async def test_classifier_notes_become_soft_signals_for_fraud(policy, config):
    fake = FakeClassifier(
        results={
            "F001": make_classification(
                "F001", "PRESCRIPTION", file_name="rx.jpg",
                notes="total appears crossed out and rewritten",
            ),
            "F002": make_classification("F002", "HOSPITAL_BILL", file_name="bill.jpg"),
        }
    )
    record = await run_document_check(
        policy, config, fake,
        documents=[
            UploadedDocument(file_id="F001", file_name="rx.jpg"),
            UploadedDocument(file_id="F002", file_name="bill.jpg"),
        ],
    )
    assert any("crossed out" in s for s in record.soft_signals)


# ------------------------------------------------------------------ end to end


async def test_tc001_end_to_end_through_service(service_factory):
    service = service_factory()
    record = await service.submit(
        make_submission(
            documents=[
                stub("F001", "dr_sharma_prescription.jpg", "PRESCRIPTION"),
                stub("F002", "another_prescription.jpg", "PRESCRIPTION"),
            ]
        )
    )
    assert record.status == ClaimStatus.NEEDS_RESUBMISSION
    persisted = service.repo.get(record.claim_id)
    assert persisted is not None
    assert persisted.status == ClaimStatus.NEEDS_RESUBMISSION
    assert persisted.problems[0].error_code == "WRONG_DOCUMENT_TYPE"
