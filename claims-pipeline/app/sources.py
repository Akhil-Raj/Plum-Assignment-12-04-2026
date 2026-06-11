"""Document source adapter — the seam between real uploads and test-case stubs.

test_cases.json ships documents as JSON stubs (`actual_type`, sometimes `quality`
and `content`) rather than real images. Stub documents are taken at face value, as
if a perfect classifier/extractor had run — no LLM call. Real files go to the
vision agents as base64 content blocks. Everything downstream of this adapter is
identical in both modes, which keeps the 12-case eval deterministic at these stages
while the same pipeline serves real uploads from the UI.
"""
from __future__ import annotations

import base64
from pathlib import Path

from app.models import (
    DocQuality,
    DocType,
    DocumentClassification,
    DocumentRead,
    UploadedDocument,
)

_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


def stub_classification(document: UploadedDocument) -> DocumentClassification:
    """A stub's declared type/quality stand in for a perfect classifier."""
    try:
        detected = DocType(document.actual_type)
    except ValueError:
        detected = DocType.UNKNOWN
    try:
        quality = DocQuality(document.quality) if document.quality else DocQuality.GOOD
    except ValueError:
        quality = DocQuality.GOOD
    return DocumentClassification(
        file_id=document.file_id,
        file_name=document.file_name,
        detected_type=detected,
        confidence=1.0,
        quality=quality,
        evidence="type provided by test fixture (stub mode)",
        source="stub",
    )


def stub_read(document: UploadedDocument, doc_type: DocType) -> DocumentRead:
    """A stub's `content` block stands in for a perfect extractor: stored verbatim,
    individual confidences treated as 1.0, extraction confidence 1.0.

    Stubs that carry loose fields instead of a `content` block (e.g. TC003's
    `patient_name_on_doc`) have those fields stored as the document's content, so
    downstream stages can read them like anything else.
    """
    if document.content is not None:
        content = document.content
    else:
        content = document.stub_extra_fields() or {}
    return DocumentRead(
        file_id=document.file_id,
        doc_type=doc_type,
        extraction_confidence=1.0,
        content=content,
    )


def file_content_block(document: UploadedDocument) -> dict:
    """The Messages-API content block for a real uploaded file (image or PDF)."""
    if not document.stored_path:
        raise FileNotFoundError(f"document {document.file_id} has no stored file")
    path = Path(document.stored_path)
    data = base64.standard_b64encode(path.read_bytes()).decode()
    suffix = path.suffix.lower() or Path(document.file_name or "").suffix.lower()
    media_type = _MEDIA_TYPES.get(suffix, "image/jpeg")
    if media_type == "application/pdf":
        # PDFs go as one document block; the API reads all pages natively, so the
        # model can aggregate line items across pages without manual splitting.
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }
