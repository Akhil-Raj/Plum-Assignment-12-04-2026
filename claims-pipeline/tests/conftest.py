from __future__ import annotations

from datetime import date

import pytest

from app.config import AppConfig, load_config
from app.models import ClaimSubmission, UploadedDocument
from app.policy_store import PolicyStore
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
