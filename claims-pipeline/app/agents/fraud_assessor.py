"""Fraud Assessor — one LLM call that weighs the soft signals accumulated through
the pipeline (classifier notes, reader observations embedded in document content,
consistency warnings) plus the claims history. Free-text judgment is a language
task; everything countable was already enforced by the pure-code threshold checks
before this call.

Output is a small strict schema because code must compare the score against the
policy threshold. The score is the model's judgment; the routing decision is the
policy's.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.config import AppConfig
from app.llm import LLMClient
from app.models import (
    ClaimSubmission,
    DocumentRead,
    FraudAssessment,
    FraudSignal,
)
from app.prompts import claim_block, documents_block, fraud_assessor_system, fraud_user

AGENT = "assessor"


class SignalOut(BaseModel):
    name: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    explanation: str


class AssessorOutput(BaseModel):
    fraud_score: float = Field(ge=0, le=1)
    signals: list[SignalOut] = Field(default_factory=list)


def _history_block(submission: ClaimSubmission) -> str:
    if not submission.claims_history:
        return "none on record"
    lines = []
    for h in submission.claims_history:
        provider = f" at {h.provider}" if h.provider else ""
        claim_id = f" ({h.claim_id})" if h.claim_id else ""
        lines.append(f"- {h.date.isoformat()}: ₹{h.amount:,.0f}{provider}{claim_id}")
    return "\n".join(lines)


def _signals_block(soft_signals: list[str]) -> str:
    if not soft_signals:
        return "none recorded"
    return "\n".join(f"- {s}" for s in soft_signals)


class FraudAssessorAgent:
    def __init__(self, llm: LLMClient, config: AppConfig):
        self._llm = llm
        self._config = config

    async def assess(
        self,
        *,
        submission: ClaimSubmission,
        soft_signals: list[str],
        reads: list[DocumentRead],
        decision_summary: str,
        manual_review_threshold: float | None,
    ) -> FraudAssessment:
        out = await self._llm.structured_call(
            agent=AGENT,
            model=self._config.llm.models.fraud_assessor,
            max_tokens=self._config.llm.max_tokens.fraud_assessor,
            system=fraud_assessor_system(manual_review_threshold),
            messages=[
                {
                    "role": "user",
                    "content": fraud_user(
                        claim_block=claim_block(submission),
                        history_block=_history_block(submission),
                        signals_block=_signals_block(soft_signals),
                        documents_block=documents_block(reads, []) or "no readable documents",
                        decision_summary=decision_summary,
                    ),
                }
            ],
            schema=AssessorOutput,
            thinking=True,
        )
        return FraudAssessment(
            fraud_score=out.fraud_score,
            signals=[
                FraudSignal(name=s.name, severity=s.severity, explanation=s.explanation)
                for s in out.signals
            ],
            source="llm",
        )
