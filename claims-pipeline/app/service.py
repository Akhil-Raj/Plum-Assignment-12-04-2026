"""ClaimService — the one entry point for processing a claim, shared by the HTTP API
and the eval runner so both exercise exactly the same path."""
from __future__ import annotations

from dataclasses import dataclass

from app.config import AppConfig
from app.errors import IntakeRejected
from app.models import ClaimRecord, ClaimSubmission, TraceResult
from app.pipeline.intake import run_intake_checks
from app.pipeline.runner import PipelineRunner
from app.policy_store import PolicyStore
from app.storage import ClaimRepository


@dataclass
class ClaimService:
    config: AppConfig
    policy: PolicyStore
    repo: ClaimRepository
    runner: PipelineRunner

    def build_claim_record(self, submission: ClaimSubmission) -> ClaimRecord:
        """Run intake checks; returns a RECEIVED record or raises IntakeRejected
        carrying every problem at once."""
        events, problems = run_intake_checks(submission, self.policy, self.config)
        if problems:
            raise IntakeRejected(problems)
        record = ClaimRecord(
            claimed_amount=submission.claimed_amount,
            currency=self.policy.currency,
            submission=submission,
        )
        record.trace.extend(events)
        record.add_trace(
            "intake",
            "claim_accepted",
            TraceResult.PASS,
            f"All intake checks passed; claim accepted as {record.claim_id}.",
        )
        return record

    async def submit(self, submission: ClaimSubmission) -> ClaimRecord:
        record = self.build_claim_record(submission)
        self.repo.save(record)  # persisted as RECEIVED before any processing
        record = await self.runner.run(record)
        self.repo.save(record)
        return record
