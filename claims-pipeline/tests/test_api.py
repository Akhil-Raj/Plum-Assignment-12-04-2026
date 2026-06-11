"""API tests: submit a valid claim -> processed with trace; each bad claim -> the
right specific error; /policy/meta serves the dropdown data from the policy file.

TC001/TC002 run end-to-end here straight from test_cases.json — their documents are
stubs, so no LLM is involved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.main import create_app

TEST_CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "test_cases.json").read_text()
)["test_cases"]


def case_input(case_id: str) -> dict:
    case = next(c for c in TEST_CASES if c["case_id"] == case_id)
    # pin the submission date so the 30-day window holds for the 2024-dated cases
    return {**case["input"], "submission_date": case["input"]["treatment_date"]}


@pytest.fixture()
def client(tmp_path):
    config = load_config()
    config.storage.db_path = str(tmp_path / "api.db")
    config.files.upload_dir = str(tmp_path / "uploads")
    app = create_app(config)
    with TestClient(app) as c:
        yield c


VALID_CLAIM = {
    "member_id": "EMP001",
    "policy_id": "PLUM_GHI_2024",
    "claim_category": "CONSULTATION",
    "treatment_date": "2024-11-01",
    "claimed_amount": 1500,
    "submission_date": "2024-11-05",
    "documents": [
        {"file_id": "F001", "file_name": "prescription.jpg", "actual_type": "PRESCRIPTION"},
        {"file_id": "F002", "file_name": "bill.jpg", "actual_type": "HOSPITAL_BILL"},
    ],
}


def test_valid_claim_passes_intake_gate_and_extraction(client):
    response = client.post("/claims/json", json=VALID_CLAIM)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "EXTRACTED"
    assert body["claim_id"].startswith("CLM_")
    assert len(body["trace"]) >= 12
    assert all(e["result"] == "PASS" for e in body["trace"])
    assert {c["detected_type"] for c in body["classifications"]} == {"PRESCRIPTION", "HOSPITAL_BILL"}
    assert len(body["reads"]) == 2, "stub documents read with no LLM involved"


def test_tc001_wrong_document_stops_via_api(client):
    response = client.post("/claims/json", json=case_input("TC001"))
    assert response.status_code == 200, "a stopped claim is not an HTTP error"
    body = response.json()
    assert body["status"] == "NEEDS_RESUBMISSION"
    assert body["decision"] is None
    message = body["problems"][0]["message"].lower()
    assert "prescription" in message and "hospital bill" in message


def test_tc002_unreadable_document_stops_via_api(client):
    response = client.post("/claims/json", json=case_input("TC002"))
    body = response.json()
    assert body["status"] == "NEEDS_RESUBMISSION"
    assert body["decision"] is None
    problem = body["problems"][0]
    assert problem["error_code"] == "UNREADABLE_DOCUMENT"
    assert problem["file_name"] == "blurry_bill.jpg"
    assert "blurry_bill.jpg" in problem["message"]


def test_claim_round_trip_and_listing(client):
    claim_id = client.post("/claims/json", json=VALID_CLAIM).json()["claim_id"]

    fetched = client.get(f"/claims/{claim_id}")
    assert fetched.status_code == 200
    assert fetched.json()["claim_id"] == claim_id
    assert fetched.json()["submission"]["member_id"] == "EMP001"

    listing = client.get("/claims").json()["claims"]
    assert any(c["claim_id"] == claim_id for c in listing)

    assert client.get("/claims/CLM_DOESNOTEXIST").status_code == 404


def test_invalid_member_gets_specific_422(client):
    body = {**VALID_CLAIM, "member_id": "EMP099"}
    response = client.post("/claims/json", json=body)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert errors[0]["error_code"] == "MEMBER_NOT_FOUND"
    assert "EMP099" in errors[0]["message"]
    assert errors[0]["what_to_do_next"]


def test_multiple_intake_problems_returned_at_once(client):
    body = {**VALID_CLAIM, "member_id": "EMP099", "claimed_amount": -5, "claim_category": "MASSAGE"}
    errors = client.post("/claims/json", json=body).json()["errors"]
    assert {e["error_code"] for e in errors} >= {"MEMBER_NOT_FOUND", "INVALID_AMOUNT", "UNKNOWN_CATEGORY"}


def test_malformed_request_is_translated(client):
    body = {**VALID_CLAIM, "claimed_amount": "not-a-number"}
    response = client.post("/claims/json", json=body)
    assert response.status_code == 422
    assert response.json()["errors"][0]["error_code"] == "MALFORMED_REQUEST"


def test_policy_meta_serves_dropdown_data(client):
    meta = client.get("/policy/meta").json()
    assert meta["policy_id"] == "PLUM_GHI_2024"
    assert len(meta["members"]) == 12
    assert {"member_id": "EMP001", "name": "Rajesh Kumar", "relationship": "SELF"} in meta["members"]
    assert len(meta["claim_categories"]) == 6
    assert meta["document_requirements"]["CONSULTATION"]["required"] == ["PRESCRIPTION", "HOSPITAL_BILL"]
    assert meta["submission_rules"]["minimum_claim_amount"] == 500


def test_multipart_submission_stores_files_and_degrades_without_llm(client, tmp_path, monkeypatch):
    # with no API key the classifier fails per design and the stage falls back to
    # the member-declared types — the claim still gets through the gate, degraded
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post(
        "/claims",
        data={
            "member_id": "EMP001",
            "policy_id": "PLUM_GHI_2024",
            "claim_category": "CONSULTATION",
            "treatment_date": "2026-06-01",
            "claimed_amount": "1500",
            "declared_types": '["PRESCRIPTION", "HOSPITAL_BILL"]',
        },
        files=[
            ("files", ("rx.jpg", b"\xff\xd8\xff fake-jpeg-bytes", "image/jpeg")),
            ("files", ("bill.png", b"\x89PNG fake-png-bytes", "image/png")),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    docs = body["submission"]["documents"]
    assert [d["file_id"] for d in docs] == ["F001", "F002"]
    assert docs[0]["declared_type"] == "PRESCRIPTION"
    assert docs[0]["stored_path"] and (tmp_path / "uploads").exists()
    assert body["status"] == "EXTRACTED"
    assert {c["source"] for c in body["classifications"]} == {"declared_fallback"}
    assert all(r["read_failed"] for r in body["reads"]), "reader degrades without a key"
    assert any(e["result"] == "WARN" for e in body["trace"])
    assert any(e["result"] == "SKIPPED" for e in body["trace"])
    assert body["confidence"] < 1.0


def test_multipart_with_bad_extension_is_rejected(client):
    response = client.post(
        "/claims",
        data={
            "member_id": "EMP001",
            "policy_id": "PLUM_GHI_2024",
            "claim_category": "CONSULTATION",
            "treatment_date": "2026-06-01",
            "claimed_amount": "1500",
        },
        files=[("files", ("notes.txt", b"some text", "text/plain"))],
    )
    assert response.status_code == 422
    assert any(e["error_code"] == "BAD_FILE" for e in response.json()["errors"])


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}
