"""Consistency Checks — do the documents agree with each other and with the claim?
(Step 4 — the second and last gate before decision-making.)

Which checks run is decided deterministically by code, before the call, from the
document types Step 2 established; the LLM then performs exactly the chosen checks.
Checks that weren't asked get a trace event from code saying why, keeping the trace
complete for the ops view.

Verdict handling is pure code — no judgment, just routing:
- patient_identity FAIL (confident)      -> the only hard stop (TC003):
  NEEDS_RESUBMISSION, decision stays null, both names surfaced to the member.
- patient_identity FAIL (low confidence) -> don't stop on a shaky read: WARN +
  routed toward manual review. A blurry name must not bounce a legitimate claim.
- patient_identity MANUAL_REVIEW         -> abbreviation-only match: claim
  continues but is flagged; Step 5 must turn the final decision into
  MANUAL_REVIEW with the policy outcome attached for the reviewer.
- any WARN                               -> trace + recorded for Steps 5-6.
- checker fails entirely                 -> SKIPPED + confidence drop + "manual
  review recommended" note; the claim still reaches a decision without it, which
  is exactly what TC011 wants to see (this stage is the simulated-failure target).
"""
from __future__ import annotations

from app.config import AppConfig
from app.errors import AgentError
from app.models import (
    ClaimRecord,
    ClaimStatus,
    DocType,
    Problem,
    TraceResult,
    VerdictResult,
)
from app.pipeline.runner import StageFn
from app.policy_store import PolicyStore
from app.prompts import CONSISTENCY_CHECK_QUESTIONS

STAGE = "consistency_checks"

_BILL_TYPES = {DocType.HOSPITAL_BILL, DocType.PHARMACY_BILL}
_DOCTOR_REFERENCING_TYPES = {
    DocType.HOSPITAL_BILL,
    DocType.PHARMACY_BILL,
    DocType.LAB_REPORT,
    DocType.DIAGNOSTIC_REPORT,
    DocType.DENTAL_REPORT,
    DocType.DISCHARGE_SUMMARY,
}


def select_checks(readable_types: set[DocType]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Deterministic applicability: (asked [(check_id, question)], skipped
    [(check_id, reason)]) based only on the document types present and readable."""
    asked: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    def ask(check_id: str) -> None:
        asked.append((check_id, CONSISTENCY_CHECK_QUESTIONS[check_id]))

    ask("patient_identity")
    ask("date_consistency")

    if readable_types & _BILL_TYPES:
        ask("amount_consistency")
        ask("line_item_sums")
    else:
        reason = "no readable bill in this claim"
        skipped.append(("amount_consistency", reason))
        skipped.append(("line_item_sums", reason))

    if DocType.PRESCRIPTION in readable_types and readable_types & _DOCTOR_REFERENCING_TYPES:
        ask("doctor_consistency")
    elif DocType.PRESCRIPTION not in readable_types:
        skipped.append(("doctor_consistency", "no prescription in this claim"))
    else:
        skipped.append(("doctor_consistency", "no bill or report that references a doctor"))

    ask("side_by_side")
    return asked, skipped


def _degrade(record: ClaimRecord, config: AppConfig, detail: str) -> None:
    """The checker never ran (or produced nothing usable): record it honestly and
    keep going. Step 5 turns skipped components into a 'manual review recommended'
    note while keeping the computed decision (TC011's expected shape)."""
    record.skipped_components.append(STAGE)
    record.add_trace(
        STAGE,
        "stage_execution",
        TraceResult.SKIPPED,
        f"{detail} The claim continues to a decision, with manual review recommended "
        "because consistency was never verified.",
    )
    record.deduct_confidence(
        config.confidence.skipped_component_deduction,
        stage=STAGE,
        reason="consistency checks were skipped",
        floor=config.confidence.floor,
    )
    record.status = ClaimStatus.CHECKED


def build_stage(policy: PolicyStore, config: AppConfig, checker) -> StageFn:
    async def consistency_checks(record: ClaimRecord) -> None:
        readable = [r for r in record.reads if not r.read_failed and bool(r.content)]
        unreadable_labels = []
        for read in record.reads:
            if read.read_failed or not read.content:
                doc = record.get_document(read.file_id)
                label = doc.file_name if doc and doc.file_name else read.file_id
                unreadable_labels.append(f"{label} ({read.doc_type.value})")

        if not readable:
            _degrade(
                record, config,
                "Consistency was not checked: no document content was readable.",
            )
            return

        asked, skipped = select_checks({r.doc_type for r in readable})
        for check_id, reason in skipped:
            record.add_trace(
                STAGE,
                f"check_not_applicable:{check_id}",
                TraceResult.SKIPPED,
                f"{check_id} not checked: {reason} (not a failure — the check does "
                "not apply to this claim).",
            )

        member = policy.get_member(record.submission.member_id)
        dependents = policy.get_dependents(record.submission.member_id)
        try:
            verdicts = await checker.check(
                reads=readable,
                submission=record.submission,
                member=member,
                dependents=dependents,
                checks=asked,
                unreadable_labels=unreadable_labels,
            )
        except AgentError as exc:
            _degrade(record, config, f"Consistency checker failed ({exc}).")
            return

        problems: list[Problem] = []
        warn_count = 0
        for verdict in verdicts:
            record.verdicts.append(verdict)
            check = verdict.check_id
            summary = f"{verdict.explanation}" + (f" [{verdict.evidence}]" if verdict.evidence else "")

            if check == "patient_identity":
                if verdict.result == VerdictResult.FAIL:
                    if verdict.confidence >= config.thresholds.name_mismatch_stop_confidence:
                        record.add_trace(STAGE, check, TraceResult.FAIL, summary)
                        problems.append(
                            Problem(
                                error_code="PATIENT_MISMATCH",
                                message=(
                                    f"{verdict.explanation.rstrip('.')}. All documents in "
                                    "one claim must be for the same patient (the member or "
                                    "a registered dependent)."
                                    + (f" Names found: {verdict.evidence}." if verdict.evidence else "")
                                ),
                                what_to_do_next=(
                                    "Submit the claim again with documents that all belong "
                                    "to one patient. If a wrong file was attached by "
                                    "mistake, replace it with the correct one."
                                ),
                            )
                        )
                    else:
                        # a mismatch verdict built on a shaky read must not bounce
                        # a legitimate claim — route it to a human instead
                        record.add_trace(
                            STAGE, check, TraceResult.WARN,
                            f"Possible patient mismatch, but verdict confidence "
                            f"{verdict.confidence:.2f} is below the stop threshold "
                            f"({config.thresholds.name_mismatch_stop_confidence}); routing "
                            f"to manual review instead of stopping. {summary}",
                        )
                        record.manual_review_required = True
                        record.manual_review_reasons.append(
                            f"possible patient mismatch read with low confidence: "
                            f"{verdict.evidence or verdict.explanation}"
                        )
                        warn_count += 1
                elif verdict.result == VerdictResult.MANUAL_REVIEW:
                    record.add_trace(STAGE, check, TraceResult.WARN, summary)
                    record.manual_review_required = True
                    record.manual_review_reasons.append(
                        f"patient identity requires human confirmation: "
                        f"{verdict.evidence or verdict.explanation}"
                    )
                    warn_count += 1
                elif verdict.result == VerdictResult.WARN:
                    record.add_trace(STAGE, check, TraceResult.WARN, summary)
                    record.soft_signals.append(f"[consistency:{check}] {verdict.explanation}")
                    warn_count += 1
                else:
                    record.add_trace(STAGE, check, TraceResult.PASS, summary)
                continue

            # every other check: problems are findings that travel forward, never stops
            result = verdict.result
            if result == VerdictResult.FAIL:
                # defensive: only patient_identity may stop a claim
                result = VerdictResult.WARN
                summary += " (recorded as WARN: only a patient mismatch stops a claim)"
            if result == VerdictResult.MANUAL_REVIEW:
                record.add_trace(STAGE, check, TraceResult.WARN, summary)
                record.manual_review_required = True
                record.manual_review_reasons.append(f"{check}: {verdict.explanation}")
                warn_count += 1
            elif result == VerdictResult.WARN:
                record.add_trace(STAGE, check, TraceResult.WARN, summary)
                record.soft_signals.append(f"[consistency:{check}] {verdict.explanation}")
                warn_count += 1
            else:
                record.add_trace(STAGE, check, TraceResult.PASS, summary)

        if warn_count:
            record.deduct_confidence(
                config.confidence.warn_deduction * warn_count,
                stage=STAGE,
                reason=f"{warn_count} consistency warning(s)",
                floor=config.confidence.floor,
            )

        if problems:
            record.problems = problems
            record.status = ClaimStatus.NEEDS_RESUBMISSION
            record.add_trace(
                STAGE, "consistency_complete", TraceResult.FAIL,
                "Documents belong to different patients; claim stopped for "
                "resubmission before any decision. No decision was made.",
            )
        else:
            record.status = ClaimStatus.CHECKED
            passed = sum(
                1 for v in verdicts if v.result == VerdictResult.PASS
            )
            record.add_trace(
                STAGE, "consistency_complete", TraceResult.PASS,
                f"Consistency checks complete: {passed} of {len(verdicts)} passed"
                + (f", {warn_count} warning(s) recorded for later stages" if warn_count else "")
                + ". Status moved to CHECKED.",
            )

    return consistency_checks
