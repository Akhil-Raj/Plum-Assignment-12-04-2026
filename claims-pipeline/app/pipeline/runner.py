"""Pipeline runner — stages run in order over one shared ClaimRecord.

The failure rule lives here: if a stage throws, the runner catches it, writes a
SKIPPED trace event with the error, lowers the claim's confidence, and moves on.
The pipeline never dies because one component did (TC011). Stages additionally
catch their own agent-level failures and degrade more precisely; this catch is the
last resort for a whole component going down.

`simulate_component_failure` (TC011) injects a failure into the configured stage
so the runner's guarantee is demonstrated end to end.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.config import AppConfig
from app.errors import SimulatedComponentFailure
from app.models import ClaimRecord, ClaimStatus, TraceResult

StageFn = Callable[[ClaimRecord], Awaitable[None]]


class PipelineRunner:
    def __init__(self, stages: list[tuple[str, StageFn]], config: AppConfig):
        self._stages = stages
        self._config = config

    @property
    def stage_names(self) -> list[str]:
        return [name for name, _ in self._stages]

    async def run(self, record: ClaimRecord) -> ClaimRecord:
        for name, stage_fn in self._stages:
            if record.status == ClaimStatus.NEEDS_RESUBMISSION:
                break  # a gate stopped the claim; nothing downstream may run
            try:
                if (
                    record.submission.simulate_component_failure
                    and name == self._config.pipeline.simulated_failure_stage
                ):
                    raise SimulatedComponentFailure(
                        f"simulated failure injected into component '{name}' "
                        "(simulate_component_failure=true)"
                    )
                await stage_fn(record)
            except Exception as exc:
                record.skipped_components.append(name)
                record.add_trace(
                    name,
                    "stage_execution",
                    TraceResult.SKIPPED,
                    f"Component '{name}' failed and was skipped; the pipeline continued "
                    f"without it. Error: {type(exc).__name__}: {exc}",
                )
                record.deduct_confidence(
                    self._config.confidence.skipped_component_deduction,
                    stage=name,
                    reason=f"component '{name}' was skipped after a failure",
                    floor=self._config.confidence.floor,
                )
        return record
