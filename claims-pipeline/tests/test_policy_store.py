"""PolicyStore: typed lookups over policy_terms.json, loud failure on a broken file."""
from __future__ import annotations

import json

import pytest

from app.errors import PolicyFileInvalid
from app.policy_store import PolicyStore


def test_loads_real_policy_file(policy):
    assert policy.policy_id == "PLUM_GHI_2024"
    assert policy.currency == "INR"
    assert len(policy.members()) == 12


def test_member_lookup(policy):
    member = policy.get_member("EMP001")
    assert member is not None and member.name == "Rajesh Kumar"
    assert member.join_date.isoformat() == "2024-04-01"
    assert policy.get_member("EMP099") is None


def test_dependents_resolved_for_family_floater(policy):
    deps = policy.get_dependents("EMP001")
    assert {d.member_id for d in deps} == {"DEP001", "DEP002"}
    assert {d.name for d in deps} == {"Sunita Kumar", "Arjun Kumar"}
    assert policy.get_dependents("EMP002") == []


def test_claim_categories(policy):
    cats = policy.claim_categories()
    assert len(cats) == 6
    assert "CONSULTATION" in cats and "DENTAL" in cats
    assert policy.is_valid_category("consultation")  # case-insensitive
    assert not policy.is_valid_category("MASSAGE")


def test_document_requirements_lookup(policy):
    reqs = policy.get_document_requirements("consultation")
    assert reqs["required"] == ["PRESCRIPTION", "HOSPITAL_BILL"]
    assert "LAB_REPORT" in reqs["optional"]


def test_category_rules_lookup(policy):
    rules = policy.get_category_rules("CONSULTATION")
    assert rules["copay_percent"] == 10
    assert rules["network_discount_percent"] == 20


def test_policy_chunks_exposed(policy):
    assert policy.coverage["per_claim_limit"] == 5000
    assert policy.waiting_periods["specific_conditions"]["diabetes"] == 90
    assert "Apollo Hospitals" in policy.network_hospitals
    assert policy.fraud_thresholds["same_day_claims_limit"] == 2
    assert policy.submission_rules["minimum_claim_amount"] == 500


def test_missing_file_fails_loudly(tmp_path):
    with pytest.raises(PolicyFileInvalid, match="not found"):
        PolicyStore(tmp_path / "nope.json")


def test_invalid_json_fails_loudly(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    with pytest.raises(PolicyFileInvalid, match="not valid JSON"):
        PolicyStore(bad)


def test_missing_required_keys_fails_loudly(tmp_path, config):
    with open(config.resolve(config.policy.policy_file)) as f:
        data = json.load(f)
    del data["members"]
    crippled = tmp_path / "no_members.json"
    crippled.write_text(json.dumps(data))
    with pytest.raises(PolicyFileInvalid, match="members"):
        PolicyStore(crippled)


def test_category_without_rules_fails_loudly(tmp_path, config):
    with open(config.resolve(config.policy.policy_file)) as f:
        data = json.load(f)
    data["document_requirements"]["PHYSIOTHERAPY"] = {"required": ["PRESCRIPTION"]}
    crippled = tmp_path / "orphan_category.json"
    crippled.write_text(json.dumps(data))
    with pytest.raises(PolicyFileInvalid, match="PHYSIOTHERAPY"):
        PolicyStore(crippled)
