"""All data models for the claims pipeline.

Every stage reads and writes one shared object — the ClaimRecord — which carries the
submission, everything learned so far, and a trace: a list of events saying what was
checked and what happened. The trace is what makes every decision reconstructable.

WARN vs SKIPPED: WARN means a check ran and found something concerning (e.g. a field
read with low confidence). SKIPPED means a check or component never ran at all (it
crashed or timed out and the pipeline moved on). They reduce confidence differently.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_claim_id() -> str:
    return f"CLM_{uuid.uuid4().hex[:12].upper()}"


def format_inr(amount: float) -> str:
    """₹1,234 — used by every member-facing message so the eval can match numbers."""
    if amount == int(amount):
        return f"₹{int(amount):,}"
    return f"₹{amount:,.2f}"


# ---------------------------------------------------------------------------- enums

class ClaimStatus(str, Enum):
    RECEIVED = "RECEIVED"
    NEEDS_RESUBMISSION = "NEEDS_RESUBMISSION"
    DOCUMENTS_VERIFIED = "DOCUMENTS_VERIFIED"
    EXTRACTED = "EXTRACTED"
    CHECKED = "CHECKED"
    DECIDED = "DECIDED"
    FINALIZED = "FINALIZED"


class TraceResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIPPED = "SKIPPED"


class DocType(str, Enum):
    PRESCRIPTION = "PRESCRIPTION"
    HOSPITAL_BILL = "HOSPITAL_BILL"
    PHARMACY_BILL = "PHARMACY_BILL"
    LAB_REPORT = "LAB_REPORT"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    DENTAL_REPORT = "DENTAL_REPORT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    UNKNOWN = "UNKNOWN"


class DocQuality(str, Enum):
    GOOD = "GOOD"
    POOR = "POOR"
    UNREADABLE = "UNREADABLE"


class VerdictResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class DecisionType(str, Enum):
    APPROVED = "APPROVED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Coverage(str, Enum):
    COVERED = "COVERED"
    EXCLUDED = "EXCLUDED"
    REQUIRES_PRE_AUTH = "REQUIRES_PRE_AUTH"


# ------------------------------------------------------------------------ submission

class HistoryClaim(BaseModel):
    claim_id: Optional[str] = None
    date: date
    amount: float = 0.0
    provider: Optional[str] = None


class UploadedDocument(BaseModel):
    """A document as submitted.

    Real uploads carry bytes on disk (stored_path). Test-case stubs instead carry
    `actual_type` / `quality` / `content` — the document source adapter uses those
    directly, as if a perfect classifier/extractor had run, so the 12-case eval needs
    no vision calls. Unknown extra fields (e.g. TC003's `patient_name_on_doc`) are kept
    and exposed via stub_extra_fields().
    """

    model_config = ConfigDict(extra="allow")

    file_id: str
    file_name: Optional[str] = None
    declared_type: Optional[DocType] = None  # member-stated type; classifier fallback
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    stored_path: Optional[str] = None
    # stub-mode fields (from test_cases.json)
    actual_type: Optional[str] = None
    quality: Optional[str] = None
    content: Any = None

    @property
    def is_stub(self) -> bool:
        return self.actual_type is not None

    def stub_extra_fields(self) -> dict[str, Any]:
        return dict(self.__pydantic_extra__ or {})


class ClaimSubmission(BaseModel):
    member_id: str
    policy_id: str
    claim_category: str
    treatment_date: date
    claimed_amount: float
    documents: list[UploadedDocument] = Field(default_factory=list)
    ytd_claims_amount: float = 0.0
    claims_history: list[HistoryClaim] = Field(default_factory=list)
    hospital_name: Optional[str] = None
    simulate_component_failure: bool = False
    # Settable so the eval and tests are deterministic (test cases are dated 2024);
    # real submissions default to today at intake.
    submission_date: Optional[date] = None


# ----------------------------------------------------------------------------- trace

class TraceEvent(BaseModel):
    stage: str
    check_name: str
    result: TraceResult
    detail: str
    data: Optional[dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=utcnow)


class Problem(BaseModel):
    """One member-facing problem in the stop response: what is wrong and what to do."""

    error_code: str
    message: str
    what_to_do_next: str
    file_id: Optional[str] = None
    file_name: Optional[str] = None


# -------------------------------------------------------------------- stage outputs

class DocumentClassification(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    detected_type: DocType
    confidence: float
    quality: DocQuality
    evidence: str = ""
    notes: Optional[str] = None
    source: str = "llm"  # llm | stub | declared_fallback | unknown_fallback


class DocumentRead(BaseModel):
    file_id: str
    doc_type: DocType
    extraction_confidence: float
    # Whatever structure the model chose for this document — only the envelope
    # (extraction_confidence + content) is validated; content is stored untouched.
    content: Any = None
    read_failed: bool = False
    failure_reason: Optional[str] = None


class CheckVerdict(BaseModel):
    check_id: str
    result: VerdictResult
    confidence: float
    explanation: str
    evidence: str = ""


class PrepLineItem(BaseModel):
    description: str
    amount: float
    coverage: Coverage
    matched_policy_entry: Optional[str] = None
    confidence: float = 1.0


class PrepDiagnosis(BaseModel):
    raw_diagnosis: Optional[str] = None
    excluded_condition: Optional[str] = None
    waiting_period_key: Optional[str] = None
    confidence: float = 1.0


class PrepHospital(BaseModel):
    hospital_name_found: Optional[str] = None
    matched_network_hospital: Optional[str] = None
    confidence: float = 1.0


class PrepResult(BaseModel):
    line_items: list[PrepLineItem] = Field(default_factory=list)
    documented_total: Optional[float] = None
    diagnosis: PrepDiagnosis = Field(default_factory=PrepDiagnosis)
    hospital: PrepHospital = Field(default_factory=PrepHospital)
    treatment_date_iso: Optional[str] = None
    pre_auth_reference_found: bool = False
    notes: Optional[str] = None


class Reason(BaseModel):
    code: str
    detail: str


class LineItemOutcome(BaseModel):
    description: str
    amount: float
    outcome: str  # APPROVED | REJECTED
    reason: Optional[str] = None


class MoneyStep(BaseModel):
    step: str
    description: str
    amount_before: float
    amount_after: float


class Decision(BaseModel):
    decision: DecisionType
    approved_amount: float = 0.0
    currency: str = "INR"
    reasons: list[Reason] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    line_item_breakdown: list[LineItemOutcome] = Field(default_factory=list)
    money_breakdown: list[MoneyStep] = Field(default_factory=list)
    confidence: float = 1.0
    manual_review_recommended: bool = False
    manual_review_notes: list[str] = Field(default_factory=list)
    eligibility_date: Optional[str] = None
    what_to_do_next: Optional[str] = None
    # When fraud/identity overrides replace the policy outcome, the computed outcome
    # stays attached so the human reviewer sees what the rules engine concluded.
    computed_policy_outcome: Optional["Decision"] = None


class FraudSignal(BaseModel):
    name: str
    severity: str = "LOW"  # LOW | MEDIUM | HIGH
    explanation: str = ""


class FraudAssessment(BaseModel):
    fraud_score: float
    signals: list[FraudSignal] = Field(default_factory=list)
    source: str = "llm"  # llm | skipped


# ---------------------------------------------------------------------- claim record

class ClaimRecord(BaseModel):
    claim_id: str = Field(default_factory=new_claim_id)
    status: ClaimStatus = ClaimStatus.RECEIVED
    claimed_amount: float
    currency: str = "INR"
    submission: ClaimSubmission
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    trace: list[TraceEvent] = Field(default_factory=list)
    classifications: list[DocumentClassification] = Field(default_factory=list)
    reads: list[DocumentRead] = Field(default_factory=list)
    verdicts: list[CheckVerdict] = Field(default_factory=list)
    # Fraud-relevant observations collected in Steps 2–4 (classifier notes, reader
    # notes, consistency warnings) and consumed by the fraud stage.
    soft_signals: list[str] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    manual_review_required: bool = False
    manual_review_reasons: list[str] = Field(default_factory=list)
    skipped_components: list[str] = Field(default_factory=list)
    prep: Optional[PrepResult] = None
    decision: Optional[Decision] = None
    fraud: Optional[FraudAssessment] = None
    # Running confidence: starts at 1.0, pulled down by WARN (low-confidence reads)
    # and SKIPPED (failed components) events. Becomes the decision's confidence.
    confidence: float = 1.0

    def add_trace(
        self,
        stage: str,
        check_name: str,
        result: TraceResult,
        detail: str,
        data: Optional[dict[str, Any]] = None,
    ) -> TraceEvent:
        event = TraceEvent(stage=stage, check_name=check_name, result=result, detail=detail, data=data)
        self.trace.append(event)
        self.updated_at = utcnow()
        return event

    def deduct_confidence(self, amount: float, stage: str, reason: str, floor: float = 0.05) -> None:
        before = self.confidence
        self.confidence = max(floor, round(self.confidence - amount, 4))
        self.add_trace(
            stage,
            "confidence_adjustment",
            TraceResult.WARN,
            f"Claim confidence reduced from {before:.2f} to {self.confidence:.2f}: {reason}",
            data={"before": before, "after": self.confidence, "deduction": amount},
        )

    def get_document(self, file_id: str) -> Optional[UploadedDocument]:
        for doc in self.submission.documents:
            if doc.file_id == file_id:
                return doc
        return None

    def get_classification(self, file_id: str) -> Optional[DocumentClassification]:
        for c in self.classifications:
            if c.file_id == file_id:
                return c
        return None
