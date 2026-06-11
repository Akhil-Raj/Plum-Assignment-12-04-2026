from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from app.agents import AgentSet
from app.config import AppConfig, load_config
from app.models import (
    ClaimSubmission,
    DocQuality,
    DocType,
    DocumentClassification,
    UploadedDocument,
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
            UploadedDocument(file_id="F001", file_name="prescription.jpg", actual_type="PRESCRIPTION"),
            UploadedDocument(file_id="F002", file_name="bill.jpg", actual_type="HOSPITAL_BILL"),
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


@pytest.fixture
def service_factory(tmp_path):
    """Builds a ClaimService over a temp DB with whatever agents the test scripts."""

    def build(classifier=None) -> ClaimService:
        config = load_config()
        config.storage.db_path = str(tmp_path / "service.db")
        config.files.upload_dir = str(tmp_path / "uploads")
        policy = PolicyStore(config.resolve(config.policy.policy_file))
        repo = ClaimRepository(config.resolve(config.storage.db_path))
        agents = AgentSet(classifier=classifier or FakeClassifier())
        runner = build_pipeline(policy, config, agents)
        return ClaimService(config=config, policy=policy, repo=repo, runner=runner)

    return build
