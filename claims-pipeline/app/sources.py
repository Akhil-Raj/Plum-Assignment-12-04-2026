"""Document source adapter — the seam between real uploads and test-case stubs.

test_cases.json ships documents as JSON stubs (`actual_type`, sometimes `quality`
and `content`) rather than real images. Stub documents are taken at face value, as
if a perfect classifier had run — no LLM call. Real files go to the vision
classifier. Everything downstream of this adapter is identical in both modes, which
keeps the 12-case eval deterministic and free at this stage while the same pipeline
serves real uploads from the UI.
"""
from __future__ import annotations

from app.models import DocQuality, DocType, DocumentClassification, UploadedDocument


def stub_classification(document: UploadedDocument) -> DocumentClassification:
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
