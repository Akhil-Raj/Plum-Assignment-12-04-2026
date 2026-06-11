"""Fraud Check — the last gate, and the end of the pipeline (Step 6).

Runs AFTER the policy decision and asks one question: should a human look at this
before money moves? Calling fraud is a human's job; the system's job is to route
and to explain.

Two halves:
- Threshold checks (pure code): claims per day, claims per month, claim value —
  counted against the policy's fraud_thresholds. Counting is not a judgment call,
  so no LLM is involved; any trip routes to manual review by itself (TC009).
- Fraud Assessor (one LLM call): weighs the soft signals accumulated through the
  pipeline. Only invoked when there is something to weigh (soft signals or a
  claims history); a clean first claim has nothing for a model to assess.

Override rules:
- any threshold trip, OR fraud_score >= the policy's review threshold
    -> final decision becomes MANUAL_REVIEW; the computed policy outcome stays
       attached for the reviewer; the specific signals are listed in the output.
- a claim Step 5 already REJECTED is not overridden — there is no payment to
  protect; its signals are still recorded in the trace for pattern intelligence.
- fraud NEVER auto-rejects. The worst this stage does to a member is ask a human
  to look.

Status: DECIDED -> FINALIZED. The record now carries everything the eval report
and the ops UI need.
"""
from __future__ import annotations

from app.config import AppConfig
from app.errors import AgentError
from app.models import (
    ClaimRecord,
    ClaimStatus,
    Decision,
    DecisionType,
    FraudAssessment,
    Reason,
    TraceResult,
    format_inr,
)
from app.pipeline.runner import StageFn
from app.policy_store import PolicyStore

STAGE = "fraud_check"


def build_stage(policy: PolicyStore, config: AppConfig, assessor) -> StageFn:
    async def fraud_check(record: ClaimRecord) -> None:
        thresholds = policy.fraud_thresholds
        submission = record.submission
        treatment_date = submission.treatment_date
        trips: list[tuple[str, str]] = []  # (code, member-readable detail)

        # Defensive: if the decision stage was skipped entirely, the claim must
        # still leave the pipeline with a decision a human can act on.
        if record.decision is None:
            record.decision = Decision(
                decision=DecisionType.MANUAL_REVIEW,
                currency=policy.currency,
                reasons=[Reason(
                    code="DECISION_MISSING",
                    detail="The decision component did not run; a human must decide this claim.",
                )],
                confidence=record.confidence,
                manual_review_recommended=True,
                manual_review_notes=["decision component did not run"],
            )
            record.add_trace(
                STAGE, "decision_missing", TraceResult.WARN,
                "No policy decision was computed upstream; substituted MANUAL_REVIEW "
                "so the claim leaves the pipeline with an actionable outcome.",
            )

        # ---------------------------------------------- threshold checks (pure code)
        same_day_limit = thresholds.get("same_day_claims_limit")
        if same_day_limit is not None:
            # the current claim counts toward its own treatment-date's tally
            same_day = sum(1 for h in submission.claims_history if h.date == treatment_date) + 1
            ok = same_day <= int(same_day_limit)
            detail = (
                f"{same_day} claim(s) from this member on {treatment_date.isoformat()} "
                f"(including this one); the policy allows {int(same_day_limit)} per day."
            )
            record.add_trace(
                STAGE, "same_day_claims", TraceResult.PASS if ok else TraceResult.FAIL, detail,
                data={"count": same_day, "limit": int(same_day_limit)},
            )
            if not ok:
                trips.append(("SAME_DAY_CLAIMS", detail))

        monthly_limit = thresholds.get("monthly_claims_limit")
        if monthly_limit is not None:
            monthly = sum(
                1 for h in submission.claims_history
                if h.date.year == treatment_date.year and h.date.month == treatment_date.month
            ) + 1
            ok = monthly <= int(monthly_limit)
            detail = (
                f"{monthly} claim(s) from this member in {treatment_date.strftime('%B %Y')} "
                f"(including this one); the policy allows {int(monthly_limit)} per month."
            )
            record.add_trace(
                STAGE, "monthly_claims", TraceResult.PASS if ok else TraceResult.FAIL, detail,
                data={"count": monthly, "limit": int(monthly_limit)},
            )
            if not ok:
                trips.append(("MONTHLY_CLAIMS", detail))

        auto_review_above = thresholds.get("auto_manual_review_above")
        if auto_review_above is not None:
            ok = record.claimed_amount <= float(auto_review_above)
            detail = (
                f"Claim value {format_inr(record.claimed_amount)} vs the automatic-review "
                f"line of {format_inr(float(auto_review_above))}."
            )
            record.add_trace(
                STAGE, "high_value_claim", TraceResult.PASS if ok else TraceResult.FAIL, detail,
                data={"claimed": record.claimed_amount, "limit": float(auto_review_above)},
            )
            if not ok:
                trips.append((
                    "HIGH_VALUE_CLAIM",
                    f"Claim value {format_inr(record.claimed_amount)} is above the "
                    f"{format_inr(float(auto_review_above))} automatic-review line.",
                ))

        # --------------------------------------------------- fraud assessor (LLM)
        score_threshold = thresholds.get("fraud_score_manual_review_threshold")
        readable = [r for r in record.reads if not r.read_failed and bool(r.content)]
        assessment: FraudAssessment | None = None

        if submission.claims_history or record.soft_signals:
            decision_summary = (
                f"{record.decision.decision.value}, approved amount "
                f"{format_inr(record.decision.approved_amount)}"
            )
            try:
                assessment = await assessor.assess(
                    submission=submission,
                    soft_signals=list(record.soft_signals),
                    reads=readable,
                    decision_summary=decision_summary,
                    manual_review_threshold=(
                        float(score_threshold) if score_threshold is not None else None
                    ),
                )
                record.fraud = assessment
                record.add_trace(
                    STAGE, "fraud_assessor", TraceResult.PASS,
                    f"Fraud assessor scored this claim {assessment.fraud_score:.2f} with "
                    f"{len(assessment.signals)} signal(s).",
                    data={"fraud_score": assessment.fraud_score},
                )
                for signal in assessment.signals:
                    record.add_trace(
                        STAGE, f"fraud_signal:{signal.name}", TraceResult.WARN,
                        f"{signal.name} ({signal.severity}): {signal.explanation}",
                    )
            except AgentError as exc:
                # hard limits above are already enforced; only the soft-signal
                # judgment is missing
                record.fraud = FraudAssessment(fraud_score=0.0, signals=[], source="skipped")
                record.skipped_components.append("fraud_assessor")
                record.add_trace(
                    STAGE, "fraud_assessor", TraceResult.SKIPPED,
                    f"Fraud assessor failed ({exc}); soft fraud signals were not fully "
                    "assessed. Hard threshold checks above remain enforced.",
                )
                record.deduct_confidence(
                    config.confidence.skipped_component_deduction,
                    stage=STAGE, reason="fraud assessor was skipped",
                    floor=config.confidence.floor,
                )
        else:
            record.add_trace(
                STAGE, "fraud_assessor", TraceResult.PASS,
                "Fraud assessor not invoked: no soft signals and no claims history — "
                "nothing for the model to weigh.",
            )

        if assessment is not None and assessment.source == "llm" and score_threshold is not None:
            if assessment.fraud_score >= float(score_threshold):
                trips.append((
                    "FRAUD_SCORE",
                    f"Fraud score {assessment.fraud_score:.2f} is at or above the "
                    f"policy's manual-review threshold of {float(score_threshold):.2f}: "
                    + "; ".join(s.explanation for s in assessment.signals[:3]),
                ))
            elif assessment.signals and assessment.fraud_score >= config.confidence.fraud_signal_dip_min_score:
                record.deduct_confidence(
                    config.confidence.fraud_signal_deduction,
                    stage=STAGE,
                    reason=f"sub-threshold fraud signals present (score {assessment.fraud_score:.2f})",
                    floor=config.confidence.floor,
                )

        # ----------------------------------------------------------- override logic
        if trips:
            if record.decision.decision == DecisionType.REJECTED:
                record.add_trace(
                    STAGE, "fraud_override", TraceResult.WARN,
                    "Fraud thresholds tripped, but the claim is already REJECTED — there "
                    "is no payment to protect, so the decision stands. Signals recorded "
                    "for pattern intelligence: " + "; ".join(d for _, d in trips),
                )
            else:
                computed = record.decision
                record.decision = Decision(
                    decision=DecisionType.MANUAL_REVIEW,
                    approved_amount=0.0,
                    currency=computed.currency,
                    reasons=[Reason(code=code, detail=detail) for code, detail in trips],
                    line_item_breakdown=computed.line_item_breakdown,
                    money_breakdown=computed.money_breakdown,
                    confidence=record.confidence,
                    manual_review_recommended=True,
                    manual_review_notes=[detail for _, detail in trips],
                    computed_policy_outcome=computed,
                )
                record.add_trace(
                    STAGE, "fraud_override", TraceResult.WARN,
                    f"Decision overridden from {computed.decision.value} to MANUAL_REVIEW "
                    "(fraud is never auto-rejected; a human decides). Triggering signals: "
                    + "; ".join(d for _, d in trips),
                )
        else:
            record.add_trace(
                STAGE, "fraud_override", TraceResult.PASS,
                "No fraud threshold tripped and no score override; the policy decision stands.",
            )

        # final confidence may have moved during this stage — keep the decision in sync
        record.decision.confidence = record.confidence
        record.status = ClaimStatus.FINALIZED
        record.add_trace(
            STAGE, "pipeline_complete", TraceResult.PASS,
            f"Fraud check complete; final decision {record.decision.decision.value}. "
            "Status moved to FINALIZED — the record carries the full trace from intake to here.",
        )

    return fraud_check
