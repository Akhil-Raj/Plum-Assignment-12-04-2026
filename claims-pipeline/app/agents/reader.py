"""Document Reader — one LLM read per document, in the model's own words.

Design decision (recorded for the architecture document): extraction output is
fully flexible — no enforced schema, not even a per-field shape. Real documents
vary too much for a fixed field list, and forcing one risks losing or distorting
what's actually on the page. The only requirement is a two-part envelope:

    {"extraction_confidence": <0..1>, "content_with_individual_confidences": <anything>}

Validation touches the envelope only, never the content. The content is stored
as-is; later stages consume it with LLM calls (consistency in Step 4, decision
prep in Step 5). extraction_confidence comes from the model's own judgment, not
from any formula of ours.
"""
from __future__ import annotations

from typing import Any, Optional

from app.config import AppConfig
from app.llm import LLMClient
from app.models import DocType, DocumentRead, UploadedDocument
from app.prompts import READER_SYSTEM, reader_user
from app.sources import file_content_block

AGENT = "extractor"

CONFIDENCE_KEY = "extraction_confidence"
CONTENT_KEY = "content_with_individual_confidences"


def validate_envelope(parsed: Any) -> Optional[str]:
    """The one thing we require of the reader's output. Returns an error string
    (fed back to the model for its retry) or None."""
    if not isinstance(parsed, dict):
        return "the response must be a JSON object (the two-key envelope)"
    if CONFIDENCE_KEY not in parsed:
        return f"missing required key '{CONFIDENCE_KEY}'"
    if CONTENT_KEY not in parsed:
        return f"missing required key '{CONTENT_KEY}'"
    value = parsed[CONFIDENCE_KEY]
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return f"'{CONFIDENCE_KEY}' must be a number between 0 and 1"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"'{CONFIDENCE_KEY}' must be a number between 0 and 1"
    if not 0 <= value <= 1:
        return f"'{CONFIDENCE_KEY}' must be between 0 and 1 (got {value})"
    return None


class DocumentReaderAgent:
    def __init__(self, llm: LLMClient, config: AppConfig):
        self._llm = llm
        self._config = config

    async def read(self, document: UploadedDocument, detected_type: DocType) -> DocumentRead:
        parsed = await self._llm.raw_json_call(
            agent=AGENT,
            model=self._config.llm.models.reader,
            max_tokens=self._config.llm.max_tokens.reader,
            system=READER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        file_content_block(document),
                        {"type": "text", "text": reader_user(detected_type.value)},
                    ],
                }
            ],
            thinking=True,
            validate=validate_envelope,
        )
        return DocumentRead(
            file_id=document.file_id,
            doc_type=detected_type,
            extraction_confidence=float(parsed[CONFIDENCE_KEY]),
            content=parsed[CONTENT_KEY],
        )
