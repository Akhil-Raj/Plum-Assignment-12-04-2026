"""HTTP API.

POST /claims        — multipart submission (real files), used by the UI
POST /claims/json   — JSON submission (stub documents allowed), used by the eval
                      runner and direct API callers
GET  /claims        — ops list view
GET  /claims/{id}   — full claim record with trace
GET  /policy/meta   — dropdown data for the submission form
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import ROOT_DIR
from app.errors import IntakeRejected
from app.models import ClaimSubmission, DocType, UploadedDocument
from app.service import ClaimService

router = APIRouter()


def _service(request: Request) -> ClaimService:
    return request.app.state.service


def _intake_rejection(exc: IntakeRejected) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "status": "REJECTED_AT_INTAKE",
            "errors": [p.model_dump() for p in exc.problems],
        },
    )


@router.get("/policy/meta")
def policy_meta(request: Request):
    policy = _service(request).policy
    return {
        "policy_id": policy.policy_id,
        "policy_name": policy.policy_name,
        "currency": policy.currency,
        "members": [
            {"member_id": m.member_id, "name": m.name, "relationship": m.relationship}
            for m in policy.members()
        ],
        "claim_categories": policy.claim_categories(),
        "document_types": [t.value for t in DocType if t != DocType.UNKNOWN],
        "submission_rules": policy.submission_rules,
        "document_requirements": {
            c: policy.get_document_requirements(c) for c in policy.claim_categories()
        },
    }


@router.post("/claims/json")
async def submit_claim_json(request: Request, submission: ClaimSubmission):
    service = _service(request)
    try:
        record = await service.submit(submission)
    except IntakeRejected as exc:
        return _intake_rejection(exc)
    return record


@router.post("/claims")
async def submit_claim_multipart(
    request: Request,
    member_id: str = Form(...),
    policy_id: str = Form(...),
    claim_category: str = Form(...),
    treatment_date: date = Form(...),
    claimed_amount: float = Form(...),
    hospital_name: Optional[str] = Form(None),
    ytd_claims_amount: float = Form(0.0),
    simulate_component_failure: bool = Form(False),
    declared_types: Optional[str] = Form(None),  # JSON list aligned with files
    files: list[UploadFile] = File(default=[]),
):
    service = _service(request)
    upload_dir = service.config.resolve(service.config.files.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    declared: list[Optional[str]] = []
    if declared_types:
        try:
            parsed = json.loads(declared_types)
            if isinstance(parsed, list):
                declared = [str(t) if t else None for t in parsed]
        except json.JSONDecodeError:
            declared = []

    documents: list[UploadedDocument] = []
    for i, upload in enumerate(files):
        data = await upload.read()
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", upload.filename or f"file_{i + 1}")
        stored = upload_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
        stored.write_bytes(data)
        declared_type: Optional[DocType] = None
        if i < len(declared) and declared[i] in DocType.__members__:
            declared_type = DocType(declared[i])
        documents.append(
            UploadedDocument(
                file_id=f"F{i + 1:03d}",
                file_name=upload.filename,
                declared_type=declared_type,
                content_type=upload.content_type,
                size_bytes=len(data),
                stored_path=str(stored),
            )
        )

    submission = ClaimSubmission(
        member_id=member_id,
        policy_id=policy_id,
        claim_category=claim_category,
        treatment_date=treatment_date,
        claimed_amount=claimed_amount,
        hospital_name=hospital_name,
        ytd_claims_amount=ytd_claims_amount,
        simulate_component_failure=simulate_component_failure,
        documents=documents,
    )
    try:
        record = await service.submit(submission)
    except IntakeRejected as exc:
        return _intake_rejection(exc)
    return record


@router.get("/claims")
def list_claims(request: Request):
    return {"claims": _service(request).repo.list_claims()}


@router.get("/claims/{claim_id}")
def get_claim(request: Request, claim_id: str):
    record = _service(request).repo.get(claim_id)
    if record is None:
        return JSONResponse(status_code=404, content={"error": f"claim {claim_id} not found"})
    return record


@router.get("/test-cases")
def list_test_cases():
    """The bundled test scenarios, for the UI's demo runner. The eval runner is the
    authoritative consumer of test_cases.json; this endpoint just mirrors it."""
    path = ROOT_DIR / "test_cases.json"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "test_cases.json not found"})
    data = json.loads(path.read_text())
    return {
        "cases": [
            {
                "case_id": c["case_id"],
                "case_name": c.get("case_name", ""),
                "description": c.get("description", ""),
                "input": c["input"],
            }
            for c in data.get("test_cases", [])
        ]
    }


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
