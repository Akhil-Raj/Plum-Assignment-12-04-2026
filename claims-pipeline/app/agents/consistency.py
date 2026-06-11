"""Consistency Checker — one LLM call per claim over all document reads together.

This is where the flexible-extraction decision pays its way: the Step 3 content
has no fixed shape, so comparing documents is a language task — judging that
"Rajesh Kumar" and a transliterated "राजेश कुमार" are the same person, that
"Arjun Mehta" is not, and that "R. Kumar" is only a partial match needing human
eyes.

The checks are ours and chosen by code before the call; only the documents are
flexible. The response must contain a verdict for EXACTLY the checks asked —
nothing missing, nothing extra. A missing verdict is bad output and goes through
the normal retry path, so an omission can never be mistaken for "didn't apply".
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import AppConfig
from app.errors import AgentBadOutput
from app.llm import LLMClient
from app.models import CheckVerdict, ClaimSubmission, DocumentRead, VerdictResult
from app.policy_store import Member
from app.prompts import (
    CONSISTENCY_SYSTEM,
    claim_block,
    consistency_user,
    documents_block,
    member_block,
)

AGENT = "consistency"


class VerdictOut(BaseModel):
    check_id: str
    result: VerdictResult
    confidence: float = Field(ge=0, le=1)
    explanation: str
    evidence: str = ""


class ConsistencyOutput(BaseModel):
    verdicts: list[VerdictOut]


class ConsistencyCheckerAgent:
    def __init__(self, llm: LLMClient, config: AppConfig):
        self._llm = llm
        self._config = config

    async def check(
        self,
        *,
        reads: list[DocumentRead],
        submission: ClaimSubmission,
        member: Member | None,
        dependents: list[Member],
        checks: list[tuple[str, str]],
        unreadable_labels: list[str] | None = None,
    ) -> list[CheckVerdict]:
        asked_ids = [check_id for check_id, _ in checks]
        base_messages = [
            {
                "role": "user",
                "content": consistency_user(
                    claim_block=claim_block(submission),
                    member_block=member_block(member, dependents),
                    documents_block=documents_block(reads, unreadable_labels or []),
                    checks=checks,
                ),
            }
        ]
        messages = base_messages
        attempts = 1 + max(0, self._config.llm.bad_output_retries)
        problem = "no attempts made"
        for _ in range(attempts):
            out = await self._llm.structured_call(
                agent=AGENT,
                model=self._config.llm.models.consistency,
                max_tokens=self._config.llm.max_tokens.consistency,
                system=CONSISTENCY_SYSTEM,
                messages=messages,
                schema=ConsistencyOutput,
                thinking=True,
            )
            got_ids = [v.check_id for v in out.verdicts]
            missing = [c for c in asked_ids if c not in got_ids]
            extra = [c for c in got_ids if c not in asked_ids]
            duplicated = len(got_ids) != len(set(got_ids))
            if not missing and not extra and not duplicated:
                by_id = {v.check_id: v for v in out.verdicts}
                return [
                    CheckVerdict(
                        check_id=check_id,
                        result=by_id[check_id].result,
                        confidence=by_id[check_id].confidence,
                        explanation=by_id[check_id].explanation,
                        evidence=by_id[check_id].evidence,
                    )
                    for check_id in asked_ids  # stable order = asked order
                ]
            problem = (
                f"verdicts must cover exactly these check_ids once each: {asked_ids}; "
                f"missing {missing or 'none'}, unexpected {extra or 'none'}"
                + (", duplicates present" if duplicated else "")
            )
            messages = base_messages + [
                {"role": "assistant", "content": out.model_dump_json()},
                {
                    "role": "user",
                    "content": f"Your verdict list was invalid: {problem}. Answer "
                    "again with one verdict per asked check_id — nothing missing, "
                    "nothing extra.",
                },
            ]
        raise AgentBadOutput(AGENT, problem)
