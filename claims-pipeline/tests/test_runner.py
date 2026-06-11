"""Pipeline runner: a failing stage is skipped with a SKIPPED trace event and a
confidence drop, and the pipeline continues — it never crashes (TC011's foundation)."""
from __future__ import annotations

import pytest

from app.models import ClaimRecord, ClaimStatus, TraceResult
from app.pipeline.runner import PipelineRunner
from tests.conftest import make_submission


def build_record(**overrides) -> ClaimRecord:
    submission = make_submission(**overrides)
    return ClaimRecord(claimed_amount=submission.claimed_amount, submission=submission)


async def test_failing_stage_is_skipped_and_pipeline_continues(config):
    ran = []

    async def exploding(record):
        raise RuntimeError("synthetic component crash")

    async def downstream(record):
        ran.append("downstream")

    runner = PipelineRunner([("broken_stage", exploding), ("next_stage", downstream)], config)
    record = await runner.run(build_record())

    assert ran == ["downstream"], "pipeline must continue past a failed component"
    skipped = [e for e in record.trace if e.result == TraceResult.SKIPPED]
    assert len(skipped) == 1
    assert skipped[0].stage == "broken_stage"
    assert "synthetic component crash" in skipped[0].detail
    assert record.skipped_components == ["broken_stage"]
    assert record.confidence == pytest.approx(1.0 - config.confidence.skipped_component_deduction)


async def test_confidence_adjustment_is_traced(config):
    async def exploding(record):
        raise RuntimeError("boom")

    runner = PipelineRunner([("broken_stage", exploding)], config)
    record = await runner.run(build_record())
    adjustments = [e for e in record.trace if e.check_name == "confidence_adjustment"]
    assert len(adjustments) == 1
    assert adjustments[0].data["deduction"] == config.confidence.skipped_component_deduction


async def test_simulate_component_failure_breaks_the_configured_stage(config):
    ran = []

    async def healthy(record):
        ran.append("ran")

    stage_name = config.pipeline.simulated_failure_stage
    runner = PipelineRunner([(stage_name, healthy)], config)
    record = await runner.run(build_record(simulate_component_failure=True))

    assert ran == [], "the simulated-failure stage must not execute"
    assert record.skipped_components == [stage_name]
    assert any(e.result == TraceResult.SKIPPED and "simulated" in e.detail for e in record.trace)


async def test_needs_resubmission_stops_the_pipeline(config):
    ran = []

    async def gate(record):
        record.status = ClaimStatus.NEEDS_RESUBMISSION

    async def downstream(record):
        ran.append("downstream")

    runner = PipelineRunner([("gate", gate), ("downstream", downstream)], config)
    record = await runner.run(build_record())
    assert ran == []
    assert record.status == ClaimStatus.NEEDS_RESUBMISSION


async def test_multiple_failures_accumulate_deductions(config):
    async def exploding(record):
        raise RuntimeError("boom")

    runner = PipelineRunner([("a", exploding), ("b", exploding)], config)
    record = await runner.run(build_record())
    expected = 1.0 - 2 * config.confidence.skipped_component_deduction
    assert record.confidence == pytest.approx(max(config.confidence.floor, expected))
    assert record.skipped_components == ["a", "b"]
