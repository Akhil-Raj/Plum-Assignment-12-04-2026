"""PolicyStore — the single component every stage reads policy through.

Loads and validates policy_terms.json at startup and fails loudly at boot if the
file is broken — never mid-claim. No policy rule lives in code anywhere else.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from app.errors import PolicyFileInvalid


class Member(BaseModel):
    member_id: str
    name: str
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    relationship: str = "SELF"
    join_date: Optional[date] = None
    dependents: list[str] = Field(default_factory=list)
    primary_member_id: Optional[str] = None


_REQUIRED_TOP_LEVEL_KEYS = [
    "policy_id",
    "coverage",
    "opd_categories",
    "waiting_periods",
    "exclusions",
    "pre_authorization",
    "network_hospitals",
    "submission_rules",
    "document_requirements",
    "fraud_thresholds",
    "members",
]


class PolicyStore:
    def __init__(self, path: Path):
        self._path = path
        try:
            with open(path) as f:
                self._raw: dict[str, Any] = json.load(f)
        except FileNotFoundError as e:
            raise PolicyFileInvalid(f"policy file not found: {path}") from e
        except json.JSONDecodeError as e:
            raise PolicyFileInvalid(f"policy file is not valid JSON: {e}") from e

        missing = [k for k in _REQUIRED_TOP_LEVEL_KEYS if k not in self._raw]
        if missing:
            raise PolicyFileInvalid(f"policy file is missing required keys: {missing}")

        try:
            self._members = {m["member_id"]: Member.model_validate(m) for m in self._raw["members"]}
        except (ValidationError, KeyError, TypeError) as e:
            raise PolicyFileInvalid(f"policy member roster is invalid: {e}") from e

        # Every claim category in document_requirements must have category rules,
        # so a category mismatch surfaces at boot, not mid-claim.
        for category in self._raw["document_requirements"]:
            if category.lower() not in self._raw["opd_categories"]:
                raise PolicyFileInvalid(
                    f"document_requirements category '{category}' has no matching "
                    f"entry in opd_categories"
                )

    # ------------------------------------------------------------------ identity

    @property
    def policy_id(self) -> str:
        return self._raw["policy_id"]

    @property
    def policy_name(self) -> str:
        return self._raw.get("policy_name", self.policy_id)

    @property
    def currency(self) -> str:
        return self._raw["submission_rules"].get("currency", "INR")

    # ------------------------------------------------------------------- members

    def members(self) -> list[Member]:
        return list(self._members.values())

    def get_member(self, member_id: str) -> Optional[Member]:
        return self._members.get(member_id)

    def get_dependents(self, member_id: str) -> list[Member]:
        member = self.get_member(member_id)
        if member is None:
            return []
        by_link = [m for m in self._members.values() if m.primary_member_id == member_id]
        by_list = [self._members[d] for d in member.dependents if d in self._members]
        seen: dict[str, Member] = {}
        for m in by_link + by_list:
            seen[m.member_id] = m
        return list(seen.values())

    # ---------------------------------------------------------------- categories

    def claim_categories(self) -> list[str]:
        return [c.upper() for c in self._raw["document_requirements"]]

    def is_valid_category(self, category: str) -> bool:
        return category.upper() in self.claim_categories()

    def get_document_requirements(self, category: str) -> dict[str, list[str]]:
        reqs = self._raw["document_requirements"][category.upper()]
        return {"required": list(reqs.get("required", [])), "optional": list(reqs.get("optional", []))}

    def get_category_rules(self, category: str) -> dict[str, Any]:
        return self._raw["opd_categories"][category.lower()]

    # -------------------------------------------------------------------- chunks

    @property
    def coverage(self) -> dict[str, Any]:
        return self._raw["coverage"]

    @property
    def submission_rules(self) -> dict[str, Any]:
        return self._raw["submission_rules"]

    @property
    def waiting_periods(self) -> dict[str, Any]:
        return self._raw["waiting_periods"]

    @property
    def exclusions(self) -> dict[str, Any]:
        return self._raw["exclusions"]

    @property
    def pre_authorization(self) -> dict[str, Any]:
        return self._raw["pre_authorization"]

    @property
    def network_hospitals(self) -> list[str]:
        return list(self._raw["network_hospitals"])

    @property
    def fraud_thresholds(self) -> dict[str, Any]:
        return self._raw["fraud_thresholds"]
