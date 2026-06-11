"""Thin wrapper around the Anthropic SDK, shared by every agent.

Two call shapes:
- structured_call: generation is constrained to a Pydantic schema via the API's
  structured-output support (messages.parse). Used by agents whose entire output
  code must branch on (classifier, consistency, prep, fraud assessor).
- raw_json_call: the response must be JSON but its shape is the model's choice.
  Used by the Document Reader, whose content is deliberately schema-free — the
  caller validates only a tiny envelope via the `validate` hook. Structured
  outputs can't express "this field may be anything", so enforcing a schema here
  would re-impose exactly the fixed shape the extraction design rejects.

Failure discipline (identical for every agent):
- the provider call fails (timeout, network, API error, missing key)
    -> AgentCallFailed, keeping the provider's own error name and message verbatim
- the call succeeds but the content fails validation (schema, JSON, or envelope)
    -> retried up to config.bad_output_retries times, then AgentBadOutput
Both are caught inside the calling stage and trigger that stage's fallback;
they never crash the pipeline.

The underlying client is created lazily, so the app boots without an API key and
agent calls degrade per stage design instead of failing at import time.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional, TypeVar

import anthropic
from pydantic import BaseModel

from app.config import LLMConfig
from app.errors import AgentBadOutput, AgentCallFailed

T = TypeVar("T", bound=BaseModel)


class MissingAPIKey(Exception):
    pass


def strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


class LLMClient:
    def __init__(self, config: LLMConfig):
        self._config = config
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            api_key = os.environ.get(self._config.api_key_env)
            if not api_key:
                raise MissingAPIKey(
                    f"environment variable {self._config.api_key_env} is not set; "
                    "LLM calls are unavailable"
                )
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key,
                timeout=self._config.timeout_seconds,
                max_retries=self._config.sdk_retries,
            )
        return self._client

    async def structured_call(
        self,
        *,
        agent: str,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        schema: type[T],
        thinking: bool = False,
    ) -> T:
        attempts = 1 + max(0, self._config.bad_output_retries)
        last_detail = "no attempts made"
        for _ in range(attempts):
            try:
                client = self._get_client()
                kwargs: dict[str, Any] = dict(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    output_format=schema,
                )
                if thinking:
                    kwargs["thinking"] = {"type": "adaptive"}
                response = await client.messages.parse(**kwargs)
            except anthropic.APIError as exc:
                raise AgentCallFailed(agent, exc) from exc
            except MissingAPIKey as exc:
                raise AgentCallFailed(agent, exc) from exc
            except Exception as exc:
                # SDK-side parse/validation failures: retryable bad output
                last_detail = f"{type(exc).__name__}: {exc}"
                continue
            parsed = getattr(response, "parsed_output", None)
            if parsed is not None:
                return parsed
            last_detail = (
                f"response could not be parsed into {schema.__name__} "
                f"(stop_reason={getattr(response, 'stop_reason', None)})"
            )
        raise AgentBadOutput(agent, f"schema validation failed after {attempts} attempt(s): {last_detail}")

    async def raw_json_call(
        self,
        *,
        agent: str,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        thinking: bool = False,
        validate: Optional[Callable[[Any], Optional[str]]] = None,
    ) -> Any:
        """Ask for JSON, parse it, optionally validate it (`validate` returns an
        error string or None). Invalid output is fed back to the model for the
        retry so it can correct itself."""
        attempts = 1 + max(0, self._config.bad_output_retries)
        msgs = list(messages)
        last_detail = "no attempts made"
        for _ in range(attempts):
            try:
                client = self._get_client()
                kwargs: dict[str, Any] = dict(
                    model=model, max_tokens=max_tokens, system=system, messages=msgs
                )
                if thinking:
                    kwargs["thinking"] = {"type": "adaptive"}
                response = await client.messages.create(**kwargs)
            except anthropic.APIError as exc:
                raise AgentCallFailed(agent, exc) from exc
            except MissingAPIKey as exc:
                raise AgentCallFailed(agent, exc) from exc
            text = "".join(b.text for b in response.content if b.type == "text")
            problem: Optional[str]
            try:
                parsed = json.loads(strip_code_fences(text))
            except json.JSONDecodeError as exc:
                problem = f"response was not valid JSON: {exc}"
            else:
                problem = validate(parsed) if validate else None
                if problem is None:
                    return parsed
            last_detail = problem
            msgs = list(messages) + [
                {"role": "assistant", "content": text[:4000] or "(empty)"},
                {
                    "role": "user",
                    "content": f"Your previous response was invalid: {problem}. "
                    "Respond again with ONLY a valid JSON object in the required "
                    "envelope — no prose, no code fences.",
                },
            ]
        raise AgentBadOutput(agent, f"invalid output after {attempts} attempt(s): {last_detail}")
