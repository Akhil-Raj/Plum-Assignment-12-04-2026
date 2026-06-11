"""Document Check — the early gate (Step 2).

Before any extraction or decision happens, each uploaded file is classified (is
this the right kind of document for this claim type, and can it be read at all?)
and the classified list is compared against the policy's document requirements.
If something is wrong, the claim stops right here — status NEEDS_RESUBMISSION,
decision stays null — with a message that names the exact problem and the exact
fix (TC001: wrong document; TC002: unreadable document).

Classification is concurrent across documents (the calls are independent). The
requirement check is deterministic code over the classified list — no LLM.

Fallback chain when the classifier fails for a file (timeout, network error, or
bad JSON after the retry): use the member-declared type with a WARN and reduced
confidence -> otherwise UNKNOWN. A required document whose type cannot be
established stops the claim with a message naming the file we could not process.
The pipeline never dies because the classifier did.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from app.config import AppConfig
from app.errors import AgentError
from app.models import (
    ClaimRecord,
    ClaimStatus,
    DocQuality,
    DocType,
    DocumentClassification,
    Problem,
    TraceResult,
)
from app.pipeline.runner import StageFn
from app.policy_store import PolicyStore
from app.sources import stub_classification

STAGE = "document_check"


def human(doc_type: str | DocType) -> str:
    """HOSPITAL_BILL -> 'hospital bill'; UNKNOWN -> 'unrecognized document'."""
    value = doc_type.value if isinstance(doc_type, DocType) else str(doc_type)
    if value == DocType.UNKNOWN.value:
        return "unrecognized document"
    return value.replace("_", " ").lower()


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


@dataclass
class _ClassifyOutcome:
    classification: DocumentClassification
    notes: list[tuple[str, TraceResult, str]] = field(default_factory=list)
    deduction_reason: str | None = None


async def _classify_one(document, classifier) -> _ClassifyOutcome:
    label = document.file_name or document.file_id
    check = f"classify:{document.file_id}"

    if document.is_stub:
        c = stub_classification(document)
        return _ClassifyOutcome(
            c,
            notes=[(
                check,
                TraceResult.PASS,
                f"Stub document: '{label}' typed as {c.detected_type.value} "
                f"(quality {c.quality.value}) from the submitted fixture — no classifier call.",
            )],
        )

    try:
        c = await classifier.classify(document)
        return _ClassifyOutcome(
            c,
            notes=[(
                check,
                TraceResult.PASS,
                f"Classified '{label}' as {c.detected_type.value} "
                f"(confidence {c.confidence:.2f}, quality {c.quality.value}). {c.evidence}",
            )],
        )
    except Exception as exc:
        # AgentError already carries its code + the provider's error verbatim;
        # anything else (e.g. a missing file) keeps its own name the same way.
        error_text = str(exc) if isinstance(exc, AgentError) else f"{type(exc).__name__}: {exc}"
        if document.declared_type is not None:
            c = DocumentClassification(
                file_id=document.file_id,
                file_name=document.file_name,
                detected_type=document.declared_type,
                confidence=0.5,
                quality=DocQuality.GOOD,
                evidence="classifier failed; member-declared type used",
                source="declared_fallback",
            )
            return _ClassifyOutcome(
                c,
                notes=[(
                    check,
                    TraceResult.WARN,
                    f"Classifier failed for '{label}' ({error_text}); falling back to the "
                    f"member-declared type {document.declared_type.value}.",
                )],
                deduction_reason=f"classification of '{label}' fell back to the member-declared type",
            )
        c = DocumentClassification(
            file_id=document.file_id,
            file_name=document.file_name,
            detected_type=DocType.UNKNOWN,
            confidence=0.0,
            quality=DocQuality.GOOD,
            evidence="classifier failed and no declared type exists",
            source="unknown_fallback",
        )
        return _ClassifyOutcome(
            c,
            notes=[(
                check,
                TraceResult.WARN,
                f"Classifier failed for '{label}' ({error_text}) and the member declared no "
                "type; treating it as UNKNOWN.",
            )],
            deduction_reason=f"the type of '{label}' could not be established",
        )


def _apply_low_confidence_warns(record: ClaimRecord, config: AppConfig) -> None:
    threshold = config.thresholds.classification_confidence_warn
    for c in record.classifications:
        # fallback-sourced classifications already received their WARN + deduction
        if c.source == "llm" and c.confidence < threshold:
            label = c.file_name or c.file_id
            record.add_trace(
                STAGE,
                f"classification_confidence:{c.file_id}",
                TraceResult.WARN,
                f"'{label}' was classified as {c.detected_type.value} with low confidence "
                f"({c.confidence:.2f} < {threshold}).",
            )
            record.deduct_confidence(
                config.confidence.warn_deduction,
                stage=STAGE,
                reason=f"low-confidence classification of '{label}'",
                floor=config.confidence.floor,
            )


def _apply_requirement_check(record: ClaimRecord, policy: PolicyStore, config: AppConfig) -> None:
    category = record.submission.claim_category.upper()
    requirements = policy.get_document_requirements(category)
    required = requirements["required"]
    allowed = set(required) | set(requirements["optional"])

    by_type: dict[str, list[DocumentClassification]] = {}
    for c in record.classifications:
        by_type.setdefault(c.detected_type.value, []).append(c)

    def label(c: DocumentClassification) -> str:
        return c.file_name or c.file_id

    present_types = set(by_type) - {DocType.UNKNOWN.value}
    readable_types = {
        t for t, cs in by_type.items() if any(c.quality != DocQuality.UNREADABLE for c in cs)
    }
    unknown_docs = by_type.get(DocType.UNKNOWN.value, [])
    missing = [t for t in required if t not in present_types]
    unreadable_required = [t for t in required if t in present_types and t not in readable_types]

    # docs that are readable and actually usable by this claim — named in messages
    fine_docs = [
        c
        for c in record.classifications
        if c.quality != DocQuality.UNREADABLE
        and c.detected_type.value in allowed
        and c.detected_type.value not in unreadable_required
    ]

    problems: list[Problem] = []

    # per-required-type trace events, pass or fail
    for t in required:
        if t in present_types and t in readable_types:
            files = _join([f"'{label(c)}'" for c in by_type[t]])
            record.add_trace(
                STAGE, f"required_document:{t}", TraceResult.PASS,
                f"Required document {t}: present and readable ({files}).",
            )
        elif t in unreadable_required:
            record.add_trace(
                STAGE, f"required_document:{t}", TraceResult.FAIL,
                f"Required document {t}: present but unreadable.",
            )
        else:
            record.add_trace(
                STAGE, f"required_document:{t}", TraceResult.FAIL,
                f"Required document {t}: missing — nothing uploaded was classified as this type.",
            )

    # (a) required documents that are present but unreadable (TC002)
    for t in unreadable_required:
        bad = next(c for c in by_type[t] if c.quality == DocQuality.UNREADABLE)
        message = f"We couldn't read '{label(bad)}' (the {human(t)})."
        if fine_docs:
            fine_names = _join(sorted({human(c.detected_type) for c in fine_docs}))
            verb = "is" if len(fine_docs) == 1 else "are"
            message += f" The {fine_names} you sent {verb} fine."
        problems.append(
            Problem(
                error_code="UNREADABLE_DOCUMENT",
                message=message,
                what_to_do_next=(
                    f"Take a clearer, well-lit photo of just the {human(t)} and submit the "
                    "claim again"
                    + (" — the other documents don't need to change." if fine_docs else ".")
                ),
                file_id=bad.file_id,
                file_name=bad.file_name,
            )
        )

    # (b) required documents that are missing entirely (TC001)
    if missing:
        counts = Counter(human(c.detected_type) for c in record.classifications)
        uploaded_summary = _join(
            [f"{n} {name}{'s' if n > 1 else ''}" for name, n in counts.items()]
        )
        needed_summary = _join([f"a {human(t)}" for t in required])
        # surplus = anything beyond what the requirements can use (duplicates of a
        # required type, or types this category neither requires nor accepts)
        surplus = any(
            c.detected_type.value not in allowed for c in record.classifications
            if c.detected_type != DocType.UNKNOWN
        ) or any(len(by_type.get(t, [])) > 1 for t in required)

        for t in missing:
            message = (
                f"You uploaded {uploaded_summary}. A {category} claim needs "
                f"{needed_summary}. Please upload the {human(t)} for this visit."
            )
            if unknown_docs:
                names = _join([f"'{label(c)}'" for c in unknown_docs])
                message += (
                    f" We could not determine what {names} is — if that is your "
                    f"{human(t)}, please re-upload a clearer copy."
                )
            problems.append(
                Problem(
                    error_code="WRONG_DOCUMENT_TYPE" if surplus else "MISSING_DOCUMENT",
                    message=message,
                    what_to_do_next=(
                        f"Submit the claim again including the {human(t)} (photo or PDF) "
                        "along with your other documents."
                    ),
                )
            )

    # informational warns for documents the claim cannot use
    for c in record.classifications:
        if c.detected_type != DocType.UNKNOWN and c.detected_type.value not in allowed:
            record.add_trace(
                STAGE, f"unused_document:{c.file_id}", TraceResult.WARN,
                f"'{label(c)}' ({human(c.detected_type)}) is not used by a {category} claim.",
            )
        if c.quality == DocQuality.UNREADABLE and c.detected_type.value not in required:
            record.add_trace(
                STAGE, f"unreadable_optional:{c.file_id}", TraceResult.WARN,
                f"'{label(c)}' ({human(c.detected_type)}) is unreadable, but it is not "
                f"required for a {category} claim; continuing without it.",
            )
    if unknown_docs and not missing:
        names = _join([f"'{label(c)}'" for c in unknown_docs])
        record.add_trace(
            STAGE, "unknown_documents", TraceResult.WARN,
            f"Could not establish a type for {names}; not needed for this claim, continuing.",
        )

    if problems:
        record.problems = problems
        record.status = ClaimStatus.NEEDS_RESUBMISSION
        record.add_trace(
            STAGE, "requirement_check", TraceResult.FAIL,
            f"{len(problems)} document problem(s) found; claim stopped for resubmission "
            "before any extraction or decision. No decision was made.",
        )
    else:
        record.status = ClaimStatus.DOCUMENTS_VERIFIED
        have = _join(sorted(human(t) for t in (set(required) & present_types))) or "none required"
        record.add_trace(
            STAGE, "requirement_check", TraceResult.PASS,
            f"All required documents for a {category} claim are present and readable ({have}).",
        )


def build_stage(policy: PolicyStore, config: AppConfig, classifier) -> StageFn:
    async def document_check(record: ClaimRecord) -> None:
        outcomes = await asyncio.gather(
            *[_classify_one(doc, classifier) for doc in record.submission.documents]
        )
        # results applied in submission order so the trace is deterministic
        for outcome in outcomes:
            record.classifications.append(outcome.classification)
            for check_name, result, detail in outcome.notes:
                record.add_trace(STAGE, check_name, result, detail)
            if outcome.deduction_reason:
                record.deduct_confidence(
                    config.confidence.warn_deduction,
                    stage=STAGE,
                    reason=outcome.deduction_reason,
                    floor=config.confidence.floor,
                )
            c = outcome.classification
            if c.notes:
                record.soft_signals.append(
                    f"[classifier] '{c.file_name or c.file_id}': {c.notes}"
                )

        _apply_low_confidence_warns(record, config)
        _apply_requirement_check(record, policy, config)

    return document_check
