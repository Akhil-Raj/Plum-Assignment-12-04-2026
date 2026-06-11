"""Document Classifier — the first LLM call in the system.

One vision call per uploaded file answering exactly two questions: what kind of
document is this, and can it be read? It deliberately does NOT extract field values
— extraction is a separate, more expensive call (Step 3) gated by this cheap one.
Keeping the calls separate keeps each prompt small and testable.

Why a strict output schema even though documents vary wildly: the documents vary,
but the question never does — what type, how readable, how confident. Downstream
code must branch on those answers deterministically. The messiness is absorbed by
the free-text `evidence` and `notes` fields, where the model can say anything.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.config import AppConfig
from app.llm import LLMClient
from app.models import DocQuality, DocType, DocumentClassification, UploadedDocument
from app.prompts import CLASSIFIER_SYSTEM, CLASSIFIER_USER
from app.sources import file_content_block

AGENT = "classifier"


class ClassifierOutput(BaseModel):
    detected_type: DocType
    confidence: float = Field(ge=0, le=1)
    quality: DocQuality
    evidence: str
    notes: Optional[str] = None


class DocumentClassifierAgent:
    def __init__(self, llm: LLMClient, config: AppConfig):
        self._llm = llm
        self._config = config

    async def classify(self, document: UploadedDocument) -> DocumentClassification:
        out = await self._llm.structured_call(
            agent=AGENT,
            model=self._config.llm.models.classifier,
            max_tokens=self._config.llm.max_tokens.classifier,
            system=CLASSIFIER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        file_content_block(document),
                        {"type": "text", "text": CLASSIFIER_USER},
                    ],
                }
            ],
            schema=ClassifierOutput,
        )
        return DocumentClassification(
            file_id=document.file_id,
            file_name=document.file_name,
            detected_type=out.detected_type,
            confidence=out.confidence,
            quality=out.quality,
            evidence=out.evidence,
            notes=out.notes,
            source="llm",
        )
