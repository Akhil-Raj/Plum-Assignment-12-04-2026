"""API tests: submit a valid claim -> RECEIVED + trace; each bad claim -> the right
specific error; /policy/meta serves the dropdown data from the policy file."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.main import create_app


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


def test_valid_claim_is_received_with_trace(client):
    response = client.post("/claims/json", json=VALID_CLAIM)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "RECEIVED"
    assert body["claim_id"].startswith("CLM_")
    assert len(body["trace"]) >= 8
    assert all(e["result"] == "PASS" for e in body["trace"])


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


def test_multipart_submission_stores_files(client, tmp_path):
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
    assert body["status"] == "RECEIVED"
    docs = body["submission"]["documents"]
    assert [d["file_id"] for d in docs] == ["F001", "F002"]
    assert docs[0]["declared_type"] == "PRESCRIPTION"
    assert docs[0]["stored_path"] and (tmp_path / "uploads").exists()


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
