"""Extraction stage: flexible content stored untouched, envelope-only validation,
read failures degrade (never crash), stubs bypass the LLM entirely."""
from __future__ import annotations

import pytest

from app.agents.reader import validate_envelope
from app.errors import AgentBadOutput, AgentCallFailed
from app.llm import strip_code_fences
from app.models import ClaimRecord, ClaimStatus, DocType, TraceResult, UploadedDocument
from app.pipeline.document_check import build_stage as build_document_check
from app.pipeline.extraction import build_stage as build_extraction
from tests.conftest import FakeClassifier, FakeReader, make_read, make_submission


async def run_pipeline_stages(policy, config, reader, classifier=None, **submission_overrides) -> ClaimRecord:
    """Run document_check + extraction back to back, as the real pipeline does."""
    submission = make_submission(**submission_overrides)
    record = ClaimRecord(claimed_amount=submission.claimed_amount, submission=submission)
    await build_document_check(policy, config, classifier or FakeClassifier())(record)
    assert record.status == ClaimStatus.DOCUMENTS_VERIFIED, "fixture should pass the gate"
    await build_extraction(config, reader)(record)
    return record


def stub(file_id, file_name, actual_type, quality=None, content=None, **extra) -> UploadedDocument:
    return UploadedDocument(
        file_id=file_id, file_name=file_name, actual_type=actual_type,
        quality=quality, content=content, **extra,
    )


TC004_PRESCRIPTION_CONTENT = {
    "doctor_name": "Dr. Arun Sharma",
    "doctor_registration": "KA/45678/2015",
    "patient_name": "Rajesh Kumar",
    "date": "2024-11-01",
    "diagnosis": "Viral Fever",
    "medicines": ["Paracetamol 650mg", "Vitamin C 500mg"],
}


# ------------------------------------------------------------------- stub mode


async def test_tc004_stub_content_stored_verbatim_with_no_llm_calls(policy, config):
    reader = FakeReader()
    record = await run_pipeline_stages(
        policy, config, reader,
        documents=[
            stub("F007", None, "PRESCRIPTION", content=TC004_PRESCRIPTION_CONTENT),
            stub("F008", None, "HOSPITAL_BILL", content={"total": 1500, "patient_name": "Rajesh Kumar"}),
        ],
    )
    assert reader.calls == [], "stub documents must not touch the reader"
    assert record.status == ClaimStatus.EXTRACTED
    rx = next(r for r in record.reads if r.file_id == "F007")
    assert rx.content == TC004_PRESCRIPTION_CONTENT, "stored byte-for-byte"
    assert rx.extraction_confidence == 1.0
    assert rx.doc_type == DocType.PRESCRIPTION


async def test_tc003_style_loose_stub_fields_become_content(policy, config):
    record = await run_pipeline_stages(
        policy, config, FakeReader(),
        documents=[
            stub("F005", "prescription_rajesh.jpg", "PRESCRIPTION", patient_name_on_doc="Rajesh Kumar"),
            stub("F006", "bill_arjun.jpg", "HOSPITAL_BILL", patient_name_on_doc="Arjun Mehta"),
        ],
    )
    rx = next(r for r in record.reads if r.file_id == "F005")
    assert rx.content == {"patient_name_on_doc": "Rajesh Kumar"}


# -------------------------------------------------------------- real-file reads


def two_real_docs():
    return [
        UploadedDocument(file_id="F001", file_name="rx.jpg"),
        UploadedDocument(file_id="F002", file_name="bill.jpg"),
    ]


def classifier_for_two_docs():
    from tests.conftest import make_classification

    return FakeClassifier(
        results={
            "F001": make_classification("F001", "PRESCRIPTION", file_name="rx.jpg"),
            "F002": make_classification("F002", "HOSPITAL_BILL", file_name="bill.jpg"),
        }
    )


async def test_reader_output_stored_exactly_as_returned(policy, config):
    weird_blob = {
        "शीर्षक": "प्रिस्क्रिप्शन",
        "fields": [{"value": "Dr. A", "confidence": 0.7}, [1, 2, {"nested": True}]],
        "free_text": "the stamp covers the date",
    }
    reader = FakeReader(
        results={
            "F001": make_read("F001", "PRESCRIPTION", content=weird_blob),
            "F002": make_read("F002", "HOSPITAL_BILL"),
        }
    )
    record = await run_pipeline_stages(
        policy, config, reader, classifier=classifier_for_two_docs(), documents=two_real_docs()
    )
    rx = next(r for r in record.reads if r.file_id == "F001")
    assert rx.content == weird_blob, "nothing may reshape the model's structure"
    assert sorted(reader.calls) == ["F001", "F002"]


async def test_low_confidence_read_warns_quoting_the_models_notes(policy, config):
    reader = FakeReader(
        results={
            "F001": make_read(
                "F001", "PRESCRIPTION", confidence=0.4,
                content={"reading_notes": "handwriting illegible in the medicines section"},
            ),
            "F002": make_read("F002", "HOSPITAL_BILL"),
        }
    )
    record = await run_pipeline_stages(
        policy, config, reader, classifier=classifier_for_two_docs(), documents=two_real_docs()
    )
    warn = next(e for e in record.trace if e.check_name == "extraction_confidence:F001")
    assert warn.result == TraceResult.WARN
    assert "handwriting illegible" in warn.detail, "must quote the model's own words"
    assert record.confidence == pytest.approx(1.0 - config.confidence.warn_deduction)
    assert record.status == ClaimStatus.EXTRACTED, "low confidence continues, never stops"


async def test_envelope_failure_attaches_read_failed_document(policy, config):
    reader = FakeReader(
        results={
            "F001": AgentBadOutput("extractor", "invalid output after 2 attempt(s): missing key"),
            "F002": make_read("F002", "HOSPITAL_BILL"),
        }
    )
    record = await run_pipeline_stages(
        policy, config, reader, classifier=classifier_for_two_docs(), documents=two_real_docs()
    )
    failed = next(r for r in record.reads if r.file_id == "F001")
    assert failed.read_failed and failed.content is None
    assert "EXTRACTOR_BAD_OUTPUT" in failed.failure_reason
    skipped = next(e for e in record.trace if e.check_name == "read:F001")
    assert skipped.result == TraceResult.SKIPPED
    assert record.status == ClaimStatus.EXTRACTED, "a failed read never stops the pipeline"
    assert record.confidence == pytest.approx(1.0 - config.confidence.read_failed_deduction)


async def test_provider_failure_on_one_doc_leaves_the_other_intact(policy, config):
    reader = FakeReader(
        results={
            "F001": make_read("F001", "PRESCRIPTION"),
            "F002": AgentCallFailed("extractor", TimeoutError("read timed out after 90s")),
        }
    )
    record = await run_pipeline_stages(
        policy, config, reader, classifier=classifier_for_two_docs(), documents=two_real_docs()
    )
    ok = next(r for r in record.reads if r.file_id == "F001")
    failed = next(r for r in record.reads if r.file_id == "F002")
    assert not ok.read_failed
    assert failed.read_failed
    assert "EXTRACTOR_CALL_FAILED" in failed.failure_reason
    assert "read timed out" in failed.failure_reason, "provider error kept verbatim"
    assert record.status == ClaimStatus.EXTRACTED
    summary = next(e for e in record.trace if e.check_name == "extraction_complete")
    assert "1 read-failed" in summary.detail


async def test_unreadable_optional_document_is_not_read(policy, config):
    reader = FakeReader()
    record = await run_pipeline_stages(
        policy, config, reader,
        documents=[
            stub("F001", "rx.jpg", "PRESCRIPTION"),
            stub("F002", "bill.jpg", "HOSPITAL_BILL"),
            stub("F003", "lab.jpg", "LAB_REPORT", quality="UNREADABLE"),
        ],
    )
    assert reader.calls == []
    assert {r.file_id for r in record.reads} == {"F001", "F002"}
    skipped = next(e for e in record.trace if e.check_name == "read:F003")
    assert skipped.result == TraceResult.SKIPPED and "UNREADABLE" in skipped.detail


async def test_extraction_works_even_if_document_check_was_skipped(config):
    # simulates the runner having skipped document_check after a crash: no
    # classifications exist, the stage degrades to declared/actual types
    submission = make_submission()
    record = ClaimRecord(claimed_amount=submission.claimed_amount, submission=submission)
    await build_extraction(config, FakeReader())(record)
    assert record.status == ClaimStatus.EXTRACTED
    assert {r.doc_type for r in record.reads} == {DocType.PRESCRIPTION, DocType.HOSPITAL_BILL}


async def test_end_to_end_reads_attached_and_persisted(service_factory):
    service = service_factory()
    record = await service.submit(make_submission())
    # the pipeline now continues past extraction into consistency (Step 4)
    assert record.status == ClaimStatus.CHECKED
    assert len(record.reads) == 2
    assert service.repo.get(record.claim_id).status == ClaimStatus.CHECKED


# ------------------------------------------------------- envelope + json helpers


def test_validate_envelope_accepts_the_required_shape():
    assert validate_envelope({"extraction_confidence": 0.8, "content_with_individual_confidences": {"a": 1}}) is None
    assert validate_envelope({"extraction_confidence": "0.8", "content_with_individual_confidences": "prose"}) is None
    assert validate_envelope({"extraction_confidence": 1, "content_with_individual_confidences": None}) is None


@pytest.mark.parametrize(
    "bad",
    [
        ["not", "an", "object"],
        {"content_with_individual_confidences": {}},
        {"extraction_confidence": 0.8},
        {"extraction_confidence": "high", "content_with_individual_confidences": {}},
        {"extraction_confidence": 1.7, "content_with_individual_confidences": {}},
        {"extraction_confidence": True, "content_with_individual_confidences": {}},
    ],
)
def test_validate_envelope_rejects_malformed_output(bad):
    assert validate_envelope(bad) is not None


def test_strip_code_fences():
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
