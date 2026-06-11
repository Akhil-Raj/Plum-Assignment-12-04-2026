"""Rules Engine — the policy, applied in a fixed order, in pure code. No LLM.

Every value comes from PolicyStore or the prep output; nothing is hardcoded. The
evaluation order is fixed and documented because order changes outcomes; the claim
is REJECTED at the first rule it trips (so TC007 reports PRE_AUTH_MISSING, not
also the per-claim breach; TC012 reports EXCLUDED_CONDITION even though a waiting
period also applies — an excluded condition is permanently not covered, so that is
the truthful headline reason).

Order:
 1. membership timing (join date, initial waiting period)
 2. exclusions, claim level (outrank waiting periods deliberately)
 3. condition waiting periods (rejection states the exact eligibility date)
 4. pre-authorization (threshold from the category rules)
 5. line-item filtering (excluded items drop with a per-item reason)
 6. payable base = min(claimed, documented covered total)
 7. per-claim ceiling = max(policy per_claim_limit, category sub_limit), tested
    against the payable base
 8. money math, fixed order: network discount FIRST, co-pay SECOND (the co-pay
    percentage applies to the discounted amount — TC010 exists to catch this),
    then annual OPD headroom
 9. manual-review overrides, applied last

A documented resolution of a policy ambiguity lives in step 7: the test cases are
inconsistent under a naive reading (TC008 rejects a ₹7,500 consultation on the
₹5,000 per_claim_limit, while TC006 partially approves a ₹12,000 dental claim and
TC010 approves ₹4,500 despite the ₹2,000 consultation sub_limit). The one rule
consistent with all twelve cases: each category's effective per-claim ceiling is
max(per_claim_limit, sub_limit), tested against the covered documented base — and
sub_limit never caps the money math.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.config import AppConfig
from app.models import (
    ClaimRecord,
    Coverage,
    Decision,
    DecisionType,
    LineItemOutcome,
    MoneyStep,
    PrepResult,
    Reason,
    TraceResult,
    format_inr,
)
from app.policy_store import PolicyStore

STAGE = "policy_decision"


def _reject(record: ClaimRecord, policy: PolicyStore, *, code: str, detail: str,
            what_to_do_next: str | None = None, eligibility_date: str | None = None) -> Decision:
    record.add_trace(STAGE, "decision", TraceResult.FAIL, f"REJECTED ({code}): {detail}")
    return Decision(
        decision=DecisionType.REJECTED,
        approved_amount=0.0,
        currency=policy.currency,
        reasons=[Reason(code=code, detail=detail)],
        rejection_reasons=[code],
        confidence=record.confidence,
        eligibility_date=eligibility_date,
        what_to_do_next=what_to_do_next,
    )


def evaluate_rules(
    record: ClaimRecord, prep: PrepResult, policy: PolicyStore, config: AppConfig
) -> Decision:
    submission = record.submission
    category = submission.claim_category.upper()
    rules = policy.get_category_rules(category)
    treatment_date = submission.treatment_date
    member = policy.get_member(submission.member_id)

    decision = _evaluate_policy(record, prep, policy, config, rules, category, treatment_date, member)
    return _apply_overrides(record, decision)


def _evaluate_policy(record, prep, policy, config, rules, category, treatment_date, member) -> Decision:
    submission = record.submission

    # 1 ---------------------------------------------------------- membership timing
    if member is None or member.join_date is None:
        record.add_trace(
            STAGE, "membership_timing", TraceResult.WARN,
            "Membership timing not evaluated: no join date on the roster entry.",
        )
    else:
        if treatment_date < member.join_date:
            return _reject(
                record, policy, code="MEMBERSHIP_NOT_ACTIVE",
                detail=f"Treatment on {treatment_date.isoformat()} predates the member's "
                f"cover start date {member.join_date.isoformat()}.",
                what_to_do_next="Only treatment received after your cover started can be claimed.",
            )
        initial_days = int(policy.waiting_periods.get("initial_waiting_period_days", 0))
        initial_until = member.join_date + timedelta(days=initial_days)
        if treatment_date < initial_until:
            detail = (
                f"Treatment on {treatment_date.isoformat()} falls inside the initial "
                f"{initial_days}-day waiting period (joined {member.join_date.isoformat()}). "
                f"You are eligible for claims from {initial_until.isoformat()}."
            )
            record.add_trace(STAGE, "membership_timing", TraceResult.FAIL, detail)
            return _reject(
                record, policy, code="WAITING_PERIOD", detail=detail,
                eligibility_date=initial_until.isoformat(),
                what_to_do_next=f"Claims for treatment on or after {initial_until.isoformat()} will be accepted.",
            )
        record.add_trace(
            STAGE, "membership_timing", TraceResult.PASS,
            f"Treatment {treatment_date.isoformat()} is "
            f"{(treatment_date - member.join_date).days} days after joining "
            f"({member.join_date.isoformat()}); past the initial waiting period.",
        )

    # 2 -------------------------------------------------- exclusions (claim level)
    diagnosis = prep.diagnosis
    if diagnosis.excluded_condition:
        detail = (
            f"The diagnosis/treatment ('{diagnosis.raw_diagnosis}') falls under the policy "
            f"exclusion '{diagnosis.excluded_condition}', which is permanently not covered."
        )
        record.add_trace(
            STAGE, "exclusion_check", TraceResult.FAIL,
            detail + f" (mapping confidence {diagnosis.confidence:.2f})",
        )
        return _reject(
            record, policy, code="EXCLUDED_CONDITION", detail=detail,
            what_to_do_next="Excluded conditions cannot be claimed under this policy.",
        )
    record.add_trace(
        STAGE, "exclusion_check", TraceResult.PASS,
        f"Diagnosis ('{diagnosis.raw_diagnosis or 'not stated'}') maps to no excluded condition.",
    )

    # 3 --------------------------------------------------- condition waiting periods
    specific = policy.waiting_periods.get("specific_conditions", {})
    if diagnosis.waiting_period_key:
        key = diagnosis.waiting_period_key
        if key not in specific:
            record.add_trace(
                STAGE, "waiting_period", TraceResult.WARN,
                f"Prep mapped the diagnosis to unknown waiting-period key '{key}'; ignored.",
            )
        elif member is not None and member.join_date is not None:
            days = int(specific[key])
            eligible_from = member.join_date + timedelta(days=days)
            if treatment_date < eligible_from:
                detail = (
                    f"'{diagnosis.raw_diagnosis}' is subject to a {days}-day waiting period for "
                    f"'{key}'. The member joined on {member.join_date.isoformat()}, so "
                    f"{key}-related claims are eligible from {eligible_from.isoformat()}; "
                    f"this treatment was on {treatment_date.isoformat()}."
                )
                record.add_trace(STAGE, "waiting_period", TraceResult.FAIL, detail)
                return _reject(
                    record, policy, code="WAITING_PERIOD", detail=detail,
                    eligibility_date=eligible_from.isoformat(),
                    what_to_do_next=f"Claims for {key}-related treatment on or after "
                    f"{eligible_from.isoformat()} will be accepted.",
                )
            record.add_trace(
                STAGE, "waiting_period", TraceResult.PASS,
                f"The {days}-day waiting period for '{key}' ended on "
                f"{eligible_from.isoformat()}; treatment on {treatment_date.isoformat()} is past it.",
            )
    else:
        record.add_trace(
            STAGE, "waiting_period", TraceResult.PASS,
            "Diagnosis maps to no condition-specific waiting period.",
        )

    # 4 ------------------------------------------------------------ pre-authorization
    pre_auth_items = [i for i in prep.line_items if i.coverage == Coverage.REQUIRES_PRE_AUTH]
    threshold = rules.get("pre_auth_threshold")
    for item in pre_auth_items:
        needs_pre_auth = True if threshold is None else item.amount > float(threshold)
        if not needs_pre_auth:
            record.add_trace(
                STAGE, "pre_authorization", TraceResult.PASS,
                f"'{item.description}' ({format_inr(item.amount)}) matches "
                f"'{item.matched_policy_entry}' but is at or under the "
                f"{format_inr(float(threshold))} pre-auth threshold; treated as covered.",
            )
            continue
        if prep.pre_auth_reference_found:
            record.add_trace(
                STAGE, "pre_authorization", TraceResult.PASS,
                f"'{item.description}' requires pre-authorization and a pre-auth "
                "reference is present in the documents.",
            )
            continue
        validity = policy.pre_authorization.get("validity_days", 30)
        threshold_text = f" above {format_inr(float(threshold))}" if threshold is not None else ""
        detail = (
            f"'{item.description}' ({format_inr(item.amount)}) requires pre-authorization"
            f"{threshold_text} ({item.matched_policy_entry}), and no pre-authorization "
            "was obtained before the treatment."
        )
        record.add_trace(STAGE, "pre_authorization", TraceResult.FAIL, detail)
        return _reject(
            record, policy, code="PRE_AUTH_MISSING", detail=detail,
            what_to_do_next=(
                "Ask your treating doctor or the hospital to request pre-authorization "
                f"from the insurer for this procedure (approval stays valid {validity} "
                "days), then resubmit the claim with the approval reference attached."
            ),
        )
    if not pre_auth_items:
        record.add_trace(
            STAGE, "pre_authorization", TraceResult.PASS,
            "No line item requires pre-authorization.",
        )

    # 5 --------------------------------------------------------- line-item filtering
    if not prep.line_items:
        record.add_trace(
            STAGE, "line_item_filtering", TraceResult.WARN,
            "The documents establish no billed amounts; an automatic decision is not "
            "safe. Routing to manual review.",
        )
        return Decision(
            decision=DecisionType.MANUAL_REVIEW,
            currency=policy.currency,
            reasons=[Reason(
                code="NO_DOCUMENTED_AMOUNTS",
                detail="No billed amounts could be established from the documents.",
            )],
            confidence=record.confidence,
            manual_review_recommended=True,
            manual_review_notes=["no documented amounts — human review needed"],
        )

    breakdown: list[LineItemOutcome] = []
    covered_total = 0.0
    for item in prep.line_items:
        if item.coverage == Coverage.EXCLUDED:
            reason = item.matched_policy_entry or "excluded by policy"
            breakdown.append(LineItemOutcome(
                description=item.description, amount=item.amount,
                outcome="REJECTED", reason=reason,
            ))
            record.add_trace(
                STAGE, f"line_item:{item.description}", TraceResult.FAIL,
                f"'{item.description}' ({format_inr(item.amount)}) rejected: {reason} "
                f"(mapping confidence {item.confidence:.2f}).",
            )
        else:
            covered_total += item.amount
            breakdown.append(LineItemOutcome(
                description=item.description, amount=item.amount, outcome="APPROVED",
            ))
            record.add_trace(
                STAGE, f"line_item:{item.description}", TraceResult.PASS,
                f"'{item.description}' ({format_inr(item.amount)}) is covered.",
            )
    excluded_items = [b for b in breakdown if b.outcome == "REJECTED"]
    if covered_total <= 0:
        detail = (
            "Every billed item falls under a policy exclusion: "
            + "; ".join(f"'{b.description}' ({b.reason})" for b in excluded_items)
        )
        decision = _reject(
            record, policy, code="ALL_ITEMS_EXCLUDED", detail=detail,
            what_to_do_next="None of the billed services are covered, so there is nothing to reimburse.",
        )
        decision.line_item_breakdown = breakdown
        return decision

    # 6 ---------------------------------------------------------------- payable base
    claimed = record.claimed_amount
    base = round(min(claimed, covered_total), 2)
    record.add_trace(
        STAGE, "payable_base", TraceResult.PASS,
        f"Payable base = min(claimed {format_inr(claimed)}, documented covered total "
        f"{format_inr(covered_total)}) = {format_inr(base)} — a member can't be paid more "
        "than the documents support; claiming less pays the claimed amount.",
        data={"claimed": claimed, "covered_total": covered_total, "base": base},
    )

    # 7 ----------------------------------------------------------- per-claim ceiling
    per_claim_limit = float(policy.coverage.get("per_claim_limit", 0) or 0)
    sub_limit = float(rules.get("sub_limit", 0) or 0)
    ceiling = max(per_claim_limit, sub_limit)
    if ceiling > 0 and base > ceiling:
        detail = (
            f"Your claim of {format_inr(claimed)} exceeds the per-claim limit of "
            f"{format_inr(ceiling)} for {category} claims."
        )
        record.add_trace(
            STAGE, "per_claim_ceiling", TraceResult.FAIL,
            f"Covered base {format_inr(base)} exceeds the per-claim ceiling "
            f"max(per_claim_limit {format_inr(per_claim_limit)}, {category} sub-limit "
            f"{format_inr(sub_limit)}) = {format_inr(ceiling)}.",
            data={"base": base, "per_claim_limit": per_claim_limit, "sub_limit": sub_limit},
        )
        decision = _reject(
            record, policy, code="PER_CLAIM_EXCEEDED", detail=detail,
            what_to_do_next=f"Claims above {format_inr(ceiling)} cannot be reimbursed "
            "under this policy's outpatient cover.",
        )
        decision.line_item_breakdown = breakdown
        return decision
    record.add_trace(
        STAGE, "per_claim_ceiling", TraceResult.PASS,
        f"Covered base {format_inr(base)} is within the per-claim ceiling "
        f"max(per_claim_limit {format_inr(per_claim_limit)}, {category} sub-limit "
        f"{format_inr(sub_limit)}) = {format_inr(ceiling)}.",
        data={"base": base, "ceiling": ceiling},
    )

    # 8 -------------------------------------------- money math (order is graded)
    amount = base
    money: list[MoneyStep] = []
    reasons: list[Reason] = []

    discount_percent = float(rules.get("network_discount_percent", 0) or 0)
    if prep.hospital.matched_network_hospital and discount_percent > 0:
        after = round(amount * (1 - discount_percent / 100), 2)
        money.append(MoneyStep(
            step="network_discount",
            description=f"Network discount {discount_percent:.0f}% "
            f"({prep.hospital.matched_network_hospital}): {format_inr(amount)} → {format_inr(after)}",
            amount_before=amount, amount_after=after,
        ))
        record.add_trace(
            STAGE, "network_discount", TraceResult.PASS,
            f"Network discount {discount_percent:.0f}% applied FIRST "
            f"({prep.hospital.hospital_name_found or 'hospital'} matched network entry "
            f"'{prep.hospital.matched_network_hospital}'): {format_inr(amount)} → {format_inr(after)}.",
            data={"before": amount, "after": after, "percent": discount_percent},
        )
        amount = after
    else:
        record.add_trace(
            STAGE, "network_discount", TraceResult.PASS,
            "No network discount: "
            + ("the category defines none." if discount_percent <= 0
               else f"'{prep.hospital.hospital_name_found or 'the hospital'}' is not a network hospital."),
        )

    copay_percent = float(rules.get("copay_percent", 0) or 0)
    if copay_percent > 0:
        after = round(amount * (1 - copay_percent / 100), 2)
        member_share = round(amount - after, 2)
        money.append(MoneyStep(
            step="copay",
            description=f"Co-pay {copay_percent:.0f}% applied on {format_inr(amount)} = "
            f"{format_inr(member_share)} deducted: {format_inr(amount)} → {format_inr(after)}",
            amount_before=amount, amount_after=after,
        ))
        record.add_trace(
            STAGE, "copay", TraceResult.PASS,
            f"Co-pay {copay_percent:.0f}% applied SECOND, on the discounted amount "
            f"{format_inr(amount)}: member pays {format_inr(member_share)}, payable → {format_inr(after)}.",
            data={"before": amount, "after": after, "percent": copay_percent},
        )
        amount = after
        reasons.append(Reason(
            code="COPAY_APPLIED",
            detail=f"{copay_percent:.0f}% co-pay applied on {category.lower()} category "
            f"({format_inr(member_share)} deducted).",
        ))
    else:
        record.add_trace(
            STAGE, "copay", TraceResult.PASS, f"No co-pay for {category} claims.",
        )

    annual_limit = float(policy.coverage.get("annual_opd_limit", 0) or 0)
    if annual_limit > 0:
        headroom = round(annual_limit - record.submission.ytd_claims_amount, 2)
        if headroom <= 0:
            detail = (
                f"The annual OPD limit of {format_inr(annual_limit)} is exhausted "
                f"(₹{record.submission.ytd_claims_amount:,.0f} already claimed this year)."
            )
            record.add_trace(STAGE, "annual_opd_limit", TraceResult.FAIL, detail)
            decision = _reject(record, policy, code="ANNUAL_LIMIT_EXHAUSTED", detail=detail)
            decision.line_item_breakdown = breakdown
            decision.money_breakdown = money
            return decision
        if amount > headroom:
            money.append(MoneyStep(
                step="annual_opd_cap",
                description=f"Capped at remaining annual OPD headroom: {format_inr(amount)} → {format_inr(headroom)}",
                amount_before=amount, amount_after=headroom,
            ))
            record.add_trace(
                STAGE, "annual_opd_limit", TraceResult.WARN,
                f"Payable {format_inr(amount)} exceeds remaining annual OPD headroom "
                f"{format_inr(headroom)} (limit {format_inr(annual_limit)}, YTD "
                f"{format_inr(record.submission.ytd_claims_amount)}); capped.",
                data={"before": amount, "after": headroom},
            )
            reasons.append(Reason(
                code="ANNUAL_LIMIT_CAPPED",
                detail=f"Capped at the remaining annual OPD headroom of {format_inr(headroom)}.",
            ))
            amount = headroom
        else:
            record.add_trace(
                STAGE, "annual_opd_limit", TraceResult.PASS,
                f"Payable {format_inr(amount)} fits the remaining annual OPD headroom "
                f"{format_inr(headroom)} (limit {format_inr(annual_limit)}, YTD "
                f"{format_inr(record.submission.ytd_claims_amount)}).",
            )

    # ------------------------------------------------------------------- assembly
    if excluded_items:
        decision_type = DecisionType.PARTIAL
        for b in excluded_items:
            reasons.insert(0, Reason(
                code="LINE_ITEM_EXCLUDED",
                detail=f"'{b.description}' ({format_inr(b.amount)}) was not approved: {b.reason}.",
            ))
        reasons.insert(0, Reason(
            code="PARTIAL_APPROVAL",
            detail=f"{format_inr(amount)} approved for the covered items; "
            f"{len(excluded_items)} item(s) excluded.",
        ))
    else:
        decision_type = DecisionType.APPROVED
        reasons.insert(0, Reason(
            code="APPROVED",
            detail=f"Claim covered under {category}; {format_inr(amount)} approved.",
        ))

    record.add_trace(
        STAGE, "decision", TraceResult.PASS,
        f"{decision_type.value}: {format_inr(amount)} of claimed "
        f"{format_inr(record.claimed_amount)}.",
        data={"approved_amount": amount},
    )
    return Decision(
        decision=decision_type,
        approved_amount=amount,
        currency=policy.currency,
        reasons=reasons,
        line_item_breakdown=breakdown,
        money_breakdown=money,
        confidence=record.confidence,
    )


def _apply_overrides(record: ClaimRecord, decision: Decision) -> Decision:
    """Step 9 — applied last, never skipped.

    - Step 4's identity flag forces the final decision to MANUAL_REVIEW, with the
      computed policy outcome attached for the reviewer.
    - A degraded pipeline (skipped components, read-failed documents) keeps the
      computed decision but lowers nothing further here — confidence already took
      its deductions — and attaches an explicit 'manual review recommended' note
      (TC011's expected shape: APPROVED + lower confidence + recommendation).
    """
    degradations: list[str] = []
    if record.skipped_components:
        degradations.append(
            "component(s) skipped during processing: " + ", ".join(record.skipped_components)
        )
    failed_reads = [r.file_id for r in record.reads if r.read_failed]
    if failed_reads:
        degradations.append("document(s) could not be read: " + ", ".join(failed_reads))

    if record.manual_review_required:
        record.add_trace(
            STAGE, "manual_review_override", TraceResult.WARN,
            "Final decision overridden to MANUAL_REVIEW (patient-identity doubt from "
            "consistency checks); the computed policy outcome is attached for the "
            "reviewer: " + "; ".join(record.manual_review_reasons),
        )
        return Decision(
            decision=DecisionType.MANUAL_REVIEW,
            approved_amount=0.0,
            currency=decision.currency,
            reasons=[Reason(code="MANUAL_REVIEW_REQUIRED", detail=r) for r in record.manual_review_reasons]
            or [Reason(code="MANUAL_REVIEW_REQUIRED", detail="flagged during consistency checks")],
            line_item_breakdown=decision.line_item_breakdown,
            money_breakdown=decision.money_breakdown,
            confidence=record.confidence,
            manual_review_recommended=True,
            manual_review_notes=record.manual_review_reasons,
            computed_policy_outcome=decision,
        )

    if degradations:
        decision.manual_review_recommended = True
        decision.manual_review_notes = [
            f"manual review recommended due to incomplete processing: {d}" for d in degradations
        ]
        record.add_trace(
            STAGE, "manual_review_recommendation", TraceResult.WARN,
            f"The {decision.decision.value} decision stands, but manual review is "
            "recommended due to incomplete processing: " + "; ".join(degradations),
        )
        decision.confidence = record.confidence
    return decision
