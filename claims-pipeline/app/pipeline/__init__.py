"""Pipeline assembly: stages run in this order, each a function (ClaimRecord) -> None
that mutates the shared record.

The stage list grows one step at a time as the system is built:
document_check (Step 2) -> extraction (Step 3) -> consistency_checks (Step 4)
-> policy_decision (Step 5) -> fraud_check (Step 6). Intake runs before the
pipeline, at the API edge.
"""
from __future__ import annotations

from app.agents import AgentSet
from app.config import AppConfig
from app.pipeline import consistency_checks, document_check, extraction
from app.pipeline.runner import PipelineRunner, StageFn
from app.policy_store import PolicyStore


def build_pipeline(policy: PolicyStore, config: AppConfig, agents: AgentSet) -> PipelineRunner:
    stages: list[tuple[str, StageFn]] = [
        ("document_check", document_check.build_stage(policy, config, agents.classifier)),
        ("extraction", extraction.build_stage(config, agents.reader)),
        ("consistency_checks", consistency_checks.build_stage(policy, config, agents.consistency)),
    ]
    return PipelineRunner(stages, config)
