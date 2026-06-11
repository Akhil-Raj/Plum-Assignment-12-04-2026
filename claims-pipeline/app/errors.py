"""Error types for the pipeline.

Agent errors split by who detected the problem:
- *_CALL_FAILED  — the provider call itself failed. We do not rename what the SDK
  already names: the provider's own error class and message are kept verbatim and
  surface in the trace detail.
- *_BAD_OUTPUT   — the call succeeded but the content failed schema validation after
  the configured retries. The provider has no error for this, so it is our own code.

Both are caught inside their stage and trigger that stage's fallback; they never
escape to the pipeline runner.
"""
from __future__ import annotations

from app.models import Problem


class PolicyFileInvalid(Exception):
    """policy_terms.json is missing, unparseable, or structurally wrong. Boot-time only."""


class AgentError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class AgentCallFailed(AgentError):
    """The provider call failed (timeout, network, API error). Keeps the provider's
    own error name and message verbatim."""

    def __init__(self, agent: str, provider_error: BaseException):
        self.agent = agent
        self.provider_error_name = type(provider_error).__name__
        self.provider_error_message = str(provider_error)
        super().__init__(
            f"{agent.upper()}_CALL_FAILED",
            f"{self.provider_error_name}: {self.provider_error_message}",
        )


class AgentBadOutput(AgentError):
    """The call succeeded but the content failed schema validation after retries."""

    def __init__(self, agent: str, detail: str):
        self.agent = agent
        super().__init__(f"{agent.upper()}_BAD_OUTPUT", detail)


class SimulatedComponentFailure(Exception):
    """Raised when `simulate_component_failure` is set on the submission (TC011)."""


class IntakeRejected(Exception):
    """The submission failed intake validation. Carries every problem at once so the
    member fixes everything in one round trip."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__(f"intake rejected with {len(problems)} problem(s)")
