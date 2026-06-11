from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from app.agents import AgentSet
from app.config import AppConfig, load_config
from app.models import (
    CheckVerdict,
    ClaimSubmission,
    Coverage,
    DocQuality,
    DocType,
    DocumentClassification,
    DocumentRead,
    PrepDiagnosis,
    PrepHospital,
    PrepLineItem,
    PrepResult,
    UploadedDocument,
    VerdictResult,
)
from app.pipeline import build_pipeline
from app.policy_store import PolicyStore
from app.service import ClaimService
from app.storage import ClaimRepository


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_config()


@pytest.fixture(scope="session")
def policy(config: AppConfig) -> PolicyStore:
    return PolicyStore(config.resolve(config.policy.policy_file))


@pytest.fixture
def repo(tmp_path) -> ClaimRepository:
    return ClaimRepository(tmp_path / "test.db")


def make_submission(**overrides) -> ClaimSubmission:
    """A valid CONSULTATION submission (stub documents); override any field per test.

    submission_date is pinned so tests are deterministic regardless of when they run
    — the same knob the eval runner uses for the 2024-dated test cases.
    """
    defaults = dict(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category="CONSULTATION",
        treatment_date=date(2024, 11, 1),
        claimed_amount=1500.0,
        submission_date=date(2024, 11, 5),
        documents=[
            UploadedDocument(
                file_id="F001",
                file_name="prescription.jpg",
                actual_type="PRESCRIPTION",
                content={
                    "doctor_name": "Dr. Arun Sharma",
                    "patient_name": "Rajesh Kumar",
                    "diagnosis": "Viral Fever",
                    "date": "2024-11-01",
                },
            ),
            UploadedDocument(
                file_id="F002",
                file_name="bill.jpg",
                actual_type="HOSPITAL_BILL",
                content={
                    "hospital_name": "City Clinic",
                    "patient_name": "Rajesh Kumar",
                    "date": "2024-11-01",
                    "line_items": [{"description": "Consultation Fee", "amount": 1500}],
                    "total": 1500,
                },
            ),
        ],
    )
    defaults.update(overrides)
    return ClaimSubmission(**defaults)


@pytest.fixture
def submission_factory():
    return make_submission


class FakeClassifier:
    """Stand-in for the Document Classifier: returns whatever the test scripts for
    each file_id (a DocumentClassification, or an Exception to raise) and records
    calls, so stub-mode tests can assert zero LLM involvement."""

    def __init__(self, results: Optional[dict] = None):
        self.results = results or {}
        self.calls: list[str] = []

    async def classify(self, document: UploadedDocument) -> DocumentClassification:
        self.calls.append(document.file_id)
        result = self.results.get(document.file_id)
        if result is None:
            raise AssertionError(f"FakeClassifier has no scripted result for {document.file_id}")
        if isinstance(result, Exception):
            raise result
        return result


def make_classification(
    file_id: str,
    detected_type: str,
    *,
    # Scripted test input, not a system knob: a "cleanly classified" document,
    # comfortably above thresholds.classification_confidence_warn. Tests that
    # exercise low confidence pass their own value.
    confidence: float = 0.95,
    quality: DocQuality = DocQuality.GOOD,
    file_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> DocumentClassification:
    return DocumentClassification(
        file_id=file_id,
        file_name=file_name,
        detected_type=DocType(detected_type),
        confidence=confidence,
        quality=quality,
        evidence="scripted by test",
        notes=notes,
        source="llm",
    )


class FakeReader:
    """Stand-in for the Document Reader: returns whatever the test scripts for each
    file_id (a DocumentRead, or an Exception to raise) and records calls."""

    def __init__(self, results: Optional[dict] = None):
        self.results = results or {}
        self.calls: list[str] = []

    async def read(self, document: UploadedDocument, detected_type: DocType) -> DocumentRead:
        self.calls.append(document.file_id)
        result = self.results.get(document.file_id)
        if result is None:
            raise AssertionError(f"FakeReader has no scripted result for {document.file_id}")
        if isinstance(result, Exception):
            raise result
        return result


def make_read(
    file_id: str,
    doc_type: str = "PRESCRIPTION",
    *,
    # Scripted test input: a cleanly read document, comfortably above
    # thresholds.extraction_confidence_warn. Low-confidence tests pass their own.
    confidence: float = 0.92,
    content: object = None,
) -> DocumentRead:
    return DocumentRead(
        file_id=file_id,
        doc_type=DocType(doc_type),
        extraction_confidence=confidence,
        content=content if content is not None else {"scripted": "by test"},
    )


def make_verdict(
    check_id: str,
    result: str = "PASS",
    *,
    # Scripted test input: a confident verdict, above thresholds like
    # name_mismatch_stop_confidence. Low-confidence tests pass their own value.
    confidence: float = 0.9,
    explanation: str = "scripted by test",
    evidence: str = "",
) -> CheckVerdict:
    return CheckVerdict(
        check_id=check_id,
        result=VerdictResult(result),
        confidence=confidence,
        explanation=explanation,
        evidence=evidence,
    )


class FakeConsistencyChecker:
    """Stand-in for the Consistency Checker. Scripted verdicts (by check_id) are
    merged with auto-PASS for every other asked check, mirroring the real agent's
    completeness guarantee. Records what it was asked and given."""

    def __init__(self, verdicts: Optional[dict[str, CheckVerdict]] = None, error: Optional[Exception] = None):
        self.verdicts = verdicts or {}
        self.error = error
        self.asked: Optional[list[str]] = None
        self.received: dict = {}

    async def check(self, *, reads, submission, member, dependents, checks, unreadable_labels=None):
        self.asked = [check_id for check_id, _ in checks]
        self.received = dict(
            reads=reads, member=member, dependents=dependents, unreadable_labels=unreadable_labels
        )
        if self.error is not None:
            raise self.error
        return [
            self.verdicts.get(check_id) or make_verdict(check_id)
            for check_id in self.asked
        ]


def make_item(
    description: str,
    amount: float,
    coverage: str = "COVERED",
    *,
    matched: Optional[str] = None,
    # Scripted test input: a confident mapping, above prep_mapping_confidence_warn.
    confidence: float = 0.95,
) -> PrepLineItem:
    return PrepLineItem(
        description=description,
        amount=amount,
        coverage=Coverage(coverage),
        matched_policy_entry=matched,
        confidence=confidence,
    )


def make_prep(
    items: list[PrepLineItem],
    *,
    documented_total: Optional[float] = None,
    raw_diagnosis: Optional[str] = None,
    excluded_condition: Optional[str] = None,
    waiting_key: Optional[str] = None,
    diagnosis_confidence: float = 0.95,
    hospital_found: Optional[str] = None,
    network_match: Optional[str] = None,
    hospital_confidence: float = 0.95,
    pre_auth_found: bool = False,
) -> PrepResult:
    return PrepResult(
        line_items=items,
        documented_total=documented_total if documented_total is not None else sum(i.amount for i in items),
        diagnosis=PrepDiagnosis(
            raw_diagnosis=raw_diagnosis,
            excluded_condition=excluded_condition,
            waiting_period_key=waiting_key,
            confidence=diagnosis_confidence,
        ),
        hospital=PrepHospital(
            hospital_name_found=hospital_found,
            matched_network_hospital=network_match,
            confidence=hospital_confidence,
        ),
        pre_auth_reference_found=pre_auth_found,
    )


class FakePrepAgent:
    """Stand-in for Decision Prep: returns the scripted PrepResult (default: one
    covered item for the claimed amount), or raises the scripted error."""

    def __init__(self, result: Optional[PrepResult] = None, error: Optional[Exception] = None):
        self.result = result
        self.error = error
        self.calls = 0

    async def prepare(self, *, reads, submission, policy, unreadable_labels=None) -> PrepResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return make_prep([make_item("Billed services", submission.claimed_amount)])


@pytest.fixture
def service_factory(tmp_path):
    """Builds a ClaimService over a temp DB with whatever agents the test scripts."""

    def build(classifier=None, reader=None, consistency=None, prep=None) -> ClaimService:
        config = load_config()
        config.storage.db_path = str(tmp_path / "service.db")
        config.files.upload_dir = str(tmp_path / "uploads")
        policy = PolicyStore(config.resolve(config.policy.policy_file))
        repo = ClaimRepository(config.resolve(config.storage.db_path))
        agents = AgentSet(
            classifier=classifier or FakeClassifier(),
            reader=reader or FakeReader(),
            consistency=consistency or FakeConsistencyChecker(),
            prep=prep or FakePrepAgent(),
        )
        runner = build_pipeline(policy, config, agents)
        return ClaimService(config=config, policy=policy, repo=repo, runner=runner)

    return build
