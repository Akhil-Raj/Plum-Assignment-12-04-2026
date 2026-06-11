"""Pipeline assembly: stages run in this order, each a function (ClaimRecord) -> None
that mutates the shared record.

The stage list grows one step at a time as the system is built:
document_check (Step 2) -> extraction (Step 3) -> consistency_checks (Step 4)
-> policy_decision (Step 5) -> fraud_check (Step 6). In Step 1 the runner exists
with its failure rule baked in, but no stages yet — intake runs before the pipeline.
"""
from __future__ import annotations

from app.config import AppConfig
from app.pipeline.runner import PipelineRunner, StageFn
from app.policy_store import PolicyStore


def build_pipeline(policy: PolicyStore, config: AppConfig) -> PipelineRunner:
    stages: list[tuple[str, StageFn]] = []
    return PipelineRunner(stages, config)
