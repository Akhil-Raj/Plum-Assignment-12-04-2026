"""Policy Decision stage (Step 5): Decision Prep (one LLM call) feeds the Rules
Engine (pure code). The split is deliberate: the model does the semantic mapping
it is good at; deterministic code applies the policy in a fixed, documented order
so every claim's outcome can be reconstructed from the trace.

If prep fails entirely, the decision is MANUAL_REVIEW — "could not reliably read
the values needed for an automatic decision" — with a SKIPPED trace event and
reduced confidence. Never a crash, never a guess.
"""
from __future__ import annotations

from app.config import AppConfig
from app.errors import AgentError
from app.models import (
    ClaimRecord,
    ClaimStatus,
    Decision,
    DecisionType,
    PrepResult,
    Reason,
    TraceResult,
)
from app.pipeline.rules_engine import evaluate_rules
from app.pipeline.runner import StageFn
from app.policy_store import PolicyStore

STAGE = "policy_decision"


def _manual_review_fallback(record: ClaimRecord, policy: PolicyStore, detail: str) -> Decision:
    return Decision(
        decision=DecisionType.MANUAL_REVIEW,
        approved_amount=0.0,
        currency=policy.currency,
        reasons=[Reason(code="PREP_FAILED", detail=detail)],
        confidence=record.confidence,
        manual_review_recommended=True,
        manual_review_notes=[detail],
    )


def _warn_low_confidence_mappings(record: ClaimRecord, prep: PrepResult, config: AppConfig) -> None:
    """The per-field confidence assumption lands here: load-bearing mappings the
    model was unsure about are surfaced and pull the claim's confidence down."""
    threshold = config.thresholds.prep_mapping_confidence_warn
    suspicious: list[str] = []
    if (prep.diagnosis.excluded_condition or prep.diagnosis.waiting_period_key) and \
            prep.diagnosis.confidence < threshold:
        suspicious.append(
            f"diagnosis mapping '{prep.diagnosis.raw_diagnosis}' → "
            f"'{prep.diagnosis.excluded_condition or prep.diagnosis.waiting_period_key}' "
            f"(confidence {prep.diagnosis.confidence:.2f})"
        )
    if prep.hospital.matched_network_hospital and prep.hospital.confidence < threshold:
        suspicious.append(
            f"network-hospital match '{prep.hospital.hospital_name_found}' → "
            f"'{prep.hospital.matched_network_hospital}' (confidence {prep.hospital.confidence:.2f})"
        )
    for item in prep.line_items:
        if item.confidence < threshold:
            suspicious.append(
                f"line-item mapping '{item.description}' → {item.coverage.value} "
                f"(confidence {item.confidence:.2f})"
            )
    for description in suspicious:
        record.add_trace(
            STAGE, "prep_mapping_confidence", TraceResult.WARN,
            f"Low-confidence policy mapping: {description}.",
        )
        record.deduct_confidence(
            config.confidence.warn_deduction,
            stage=STAGE,
            reason=f"low-confidence policy mapping ({description})",
            floor=config.confidence.floor,
        )


def build_stage(policy: PolicyStore, config: AppConfig, prep_agent) -> StageFn:
    async def policy_decision(record: ClaimRecord) -> None:
        readable = [r for r in record.reads if not r.read_failed and bool(r.content)]
        unreadable_labels = []
        for read in record.reads:
            if read.read_failed or not read.content:
                doc = record.get_document(read.file_id)
                label = doc.file_name if doc and doc.file_name else read.file_id
                unreadable_labels.append(f"{label} ({read.doc_type.value})")

        if not readable:
            detail = (
                "Could not reliably read the values needed for an automatic decision: "
                "no document content was readable."
            )
            record.skipped_components.append("decision_prep")
            record.add_trace(STAGE, "decision_prep", TraceResult.SKIPPED, detail)
            record.deduct_confidence(
                config.confidence.skipped_component_deduction,
                stage=STAGE, reason="decision prep was skipped",
                floor=config.confidence.floor,
            )
            record.decision = _manual_review_fallback(record, policy, detail)
            record.status = ClaimStatus.DECIDED
            return

        try:
            prep = await prep_agent.prepare(
                reads=readable,
                submission=record.submission,
                policy=policy,
                unreadable_labels=unreadable_labels,
            )
        except AgentError as exc:
            detail = (
                f"Could not reliably read the values needed for an automatic decision: "
                f"decision prep failed ({exc})."
            )
            record.skipped_components.append("decision_prep")
            record.add_trace(STAGE, "decision_prep", TraceResult.SKIPPED, detail)
            record.deduct_confidence(
                config.confidence.skipped_component_deduction,
                stage=STAGE, reason="decision prep failed",
                floor=config.confidence.floor,
            )
            record.decision = _manual_review_fallback(record, policy, detail)
            record.status = ClaimStatus.DECIDED
            return

        record.prep = prep
        record.add_trace(
            STAGE, "decision_prep", TraceResult.PASS,
            f"Prep mapped {len(prep.line_items)} line item(s); documented total "
            f"{prep.documented_total}; diagnosis '{prep.diagnosis.raw_diagnosis or 'n/a'}' "
            f"(excluded: {prep.diagnosis.excluded_condition or 'no'}, waiting-period key: "
            f"{prep.diagnosis.waiting_period_key or 'no'}); network hospital: "
            f"{prep.hospital.matched_network_hospital or 'no match'}.",
        )
        _warn_low_confidence_mappings(record, prep, config)

        record.decision = evaluate_rules(record, prep, policy, config)
        record.status = ClaimStatus.DECIDED

    return policy_decision
