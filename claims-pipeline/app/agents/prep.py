"""Decision Prep — one LLM call that turns flexible document content into the
machine-form values the rules engine needs (the "value-pull" call promised by the
flexible-extraction design).

Why an LLM and not string matching: the mappings are semantic. "Bariatric
Consultation" must hit "Obesity and weight loss programs", "T2DM" must hit
"diabetes", and a misspelled "Apolo Hospital" must hit "Apollo Hospitals" —
lookups can't do that reliably; a model with the policy lists in front of it can,
and reports confidence per mapping. All math and all deciding stay in code.
"""
from __future__ import annotations

import json

from app.config import AppConfig
from app.llm import LLMClient
from app.models import ClaimSubmission, DocumentRead, PrepResult
from app.policy_store import PolicyStore
from app.prompts import PREP_SYSTEM, claim_block, documents_block, prep_user

AGENT = "prep"

# category-rule fields the model maps against (math fields like copay stay out —
# the engine owns those)
_MAPPING_RULE_FIELDS = [
    "covered_procedures",
    "excluded_procedures",
    "covered_items",
    "excluded_items",
    "covered_systems",
    "high_value_tests_requiring_pre_auth",
    "pre_auth_threshold",
    "requires_pre_auth",
]


def _policy_block(policy: PolicyStore, category: str) -> str:
    rules = policy.get_category_rules(category)
    block = {
        "claim_category": category.upper(),
        "category_rules": {k: rules[k] for k in _MAPPING_RULE_FIELDS if k in rules},
        "exclusions": policy.exclusions,
        "waiting_period_condition_keys": sorted(
            policy.waiting_periods.get("specific_conditions", {})
        ),
        "network_hospitals": policy.network_hospitals,
        "pre_authorization_required_for": policy.pre_authorization.get("required_for", []),
    }
    return json.dumps(block, indent=2, ensure_ascii=False)


class DecisionPrepAgent:
    def __init__(self, llm: LLMClient, config: AppConfig):
        self._llm = llm
        self._config = config

    async def prepare(
        self,
        *,
        reads: list[DocumentRead],
        submission: ClaimSubmission,
        policy: PolicyStore,
        unreadable_labels: list[str] | None = None,
    ) -> PrepResult:
        return await self._llm.structured_call(
            agent=AGENT,
            model=self._config.llm.models.prep,
            max_tokens=self._config.llm.max_tokens.prep,
            system=PREP_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": prep_user(
                        claim_block=claim_block(submission),
                        policy_block=_policy_block(policy, submission.claim_category),
                        documents_block=documents_block(reads, unreadable_labels or []),
                    ),
                }
            ],
            schema=PrepResult,
            thinking=True,
        )
