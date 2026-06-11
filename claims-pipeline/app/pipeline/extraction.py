"""Extraction — the model reads each document in its own words (Step 3).

Every document that passed the Step 2 gate gets one read. Stub documents store
their `content` verbatim at extraction confidence 1.0 with no LLM call; real files
go to the Document Reader concurrently. Documents classified UNREADABLE are not
read at all (required-unreadable already stopped the claim in Step 2; an optional
one was already warned about).

This stage only reads — no checking. Consistency questions (same patient? do line
items sum?) live in Step 4; policy reasoning in Step 5.

A failed read never stops the pipeline: the document attaches read-failed with
empty content and a SKIPPED trace event, the claim's confidence drops, and the
decision engine deals with the gap later (TC011's path). Status always reaches
EXTRACTED.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import AppConfig
from app.errors import AgentError
from app.models import (
    ClaimRecord,
    ClaimStatus,
    DocQuality,
    DocType,
    DocumentClassification,
    DocumentRead,
    TraceResult,
    UploadedDocument,
)
from app.pipeline.runner import StageFn
from app.sources import stub_read

STAGE = "extraction"


def _struggle_excerpt(content: Any) -> str:
    """The model's own words about what it struggled with, for the WARN trace.
    Looks for note-like keys in its (arbitrary) structure, else truncates."""
    if isinstance(content, dict):
        for key, value in content.items():
            if any(token in key.lower() for token in ("note", "difficult", "issue", "struggle", "warn")):
                return str(value)[:300]
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    return text[:200]


@dataclass
class _ReadOutcome:
    read: Optional[DocumentRead]  # None = deliberately not read (unreadable file)
    notes: list[tuple[str, TraceResult, str]] = field(default_factory=list)
    deduction: Optional[tuple[float, str]] = None  # (amount, reason)


def _doc_type_for(
    document: UploadedDocument, classification: Optional[DocumentClassification]
) -> DocType:
    if classification is not None:
        return classification.detected_type
    # document_check was skipped entirely (component failure): degrade to the best
    # available type instead of refusing to read
    if document.declared_type is not None:
        return document.declared_type
    try:
        return DocType(document.actual_type)
    except ValueError:
        return DocType.UNKNOWN


async def _read_one(
    document: UploadedDocument,
    classification: Optional[DocumentClassification],
    reader,
    config: AppConfig,
) -> _ReadOutcome:
    label = document.file_name or document.file_id
    check = f"read:{document.file_id}"
    doc_type = _doc_type_for(document, classification)

    if classification is not None and classification.quality == DocQuality.UNREADABLE:
        return _ReadOutcome(
            None,
            notes=[(
                check,
                TraceResult.SKIPPED,
                f"'{label}' was not read: it was classified UNREADABLE in document "
                "check, so there is nothing to extract.",
            )],
        )

    if document.is_stub:
        read = stub_read(document, doc_type)
        empty_note = "" if read.content else " (the stub carried no content)"
        return _ReadOutcome(
            read,
            notes=[(
                check,
                TraceResult.PASS,
                f"Stub document: content of '{label}' stored verbatim with extraction "
                f"confidence 1.00{empty_note} — no reader call.",
            )],
        )

    try:
        read = await reader.read(document, doc_type)
        return _ReadOutcome(
            read,
            notes=[(
                check,
                TraceResult.PASS,
                f"Read '{label}' ({doc_type.value}) with extraction confidence "
                f"{read.extraction_confidence:.2f}.",
            )],
        )
    except Exception as exc:
        error_text = str(exc) if isinstance(exc, AgentError) else f"{type(exc).__name__}: {exc}"
        read = DocumentRead(
            file_id=document.file_id,
            doc_type=doc_type,
            extraction_confidence=0.0,
            content=None,
            read_failed=True,
            failure_reason=error_text,
        )
        return _ReadOutcome(
            read,
            notes=[(
                check,
                TraceResult.SKIPPED,
                f"Could not read '{label}' ({error_text}). The document is attached "
                "with empty content; downstream stages will work around the gap.",
            )],
            deduction=(
                config.confidence.read_failed_deduction,
                f"document '{label}' could not be read",
            ),
        )


def build_stage(config: AppConfig, reader) -> StageFn:
    async def extraction(record: ClaimRecord) -> None:
        documents = record.submission.documents
        outcomes = await asyncio.gather(
            *[
                _read_one(doc, record.get_classification(doc.file_id), reader, config)
                for doc in documents
            ]
        )
        # results applied in submission order so the trace is deterministic
        for outcome in outcomes:
            if outcome.read is not None:
                record.reads.append(outcome.read)
            for check_name, result, detail in outcome.notes:
                record.add_trace(STAGE, check_name, result, detail)
            if outcome.deduction is not None:
                amount, reason = outcome.deduction
                record.deduct_confidence(
                    amount, stage=STAGE, reason=reason, floor=config.confidence.floor
                )

        threshold = config.thresholds.extraction_confidence_warn
        for read in record.reads:
            if not read.read_failed and read.extraction_confidence < threshold:
                doc = record.get_document(read.file_id)
                label = (doc.file_name if doc and doc.file_name else read.file_id)
                record.add_trace(
                    STAGE,
                    f"extraction_confidence:{read.file_id}",
                    TraceResult.WARN,
                    f"'{label}' was read with low confidence "
                    f"({read.extraction_confidence:.2f} < {threshold}). The model "
                    f"says: {_struggle_excerpt(read.content)}",
                )
                record.deduct_confidence(
                    config.confidence.warn_deduction,
                    stage=STAGE,
                    reason=f"low-confidence read of '{label}'",
                    floor=config.confidence.floor,
                )

        failed = sum(1 for r in record.reads if r.read_failed)
        record.status = ClaimStatus.EXTRACTED
        record.add_trace(
            STAGE,
            "extraction_complete",
            TraceResult.PASS if failed == 0 else TraceResult.WARN,
            f"{len(record.reads) - failed} of {len(documents)} document(s) read"
            + (f"; {failed} read-failed" if failed else "")
            + ". Status moved to EXTRACTED.",
        )

    return extraction
