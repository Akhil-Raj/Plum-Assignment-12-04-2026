"""Intake — cheap, fast checks only: things we can know without opening any document.

Each check writes a trace event whether it passes or fails. All failures are
collected and returned together so the member fixes everything in one round trip.

The last two checks (submission window, minimum amount) are policy rules, but they
are rules about the submission itself, so checking them at the front door gives the
member instant feedback. The values still come from PolicyStore, not code.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from app.config import AppConfig
from app.models import ClaimSubmission, Problem, TraceEvent, TraceResult, format_inr
from app.policy_store import PolicyStore

STAGE = "intake"


def run_intake_checks(
    submission: ClaimSubmission, policy: PolicyStore, config: AppConfig
) -> tuple[list[TraceEvent], list[Problem]]:
    events: list[TraceEvent] = []
    problems: list[Problem] = []
    submission_date = submission.submission_date or date.today()

    def record(check_name: str, ok: bool, detail: str, problem: Problem | None = None) -> None:
        events.append(
            TraceEvent(
                stage=STAGE,
                check_name=check_name,
                result=TraceResult.PASS if ok else TraceResult.FAIL,
                detail=detail,
            )
        )
        if not ok and problem is not None:
            problems.append(problem)

    # 1. member exists in the roster
    member = policy.get_member(submission.member_id)
    record(
        "member_exists",
        member is not None,
        f"Member {submission.member_id} found in roster: {member.name}."
        if member
        else f"Member ID {submission.member_id} is not on this policy's member list.",
        Problem(
            error_code="MEMBER_NOT_FOUND",
            message=f"Member ID {submission.member_id} is not on this policy's member list.",
            what_to_do_next="Check the member ID on your insurance card (e.g. EMP001) and resubmit with a valid ID.",
        ),
    )

    # 2. policy ID matches the loaded policy
    policy_ok = submission.policy_id == policy.policy_id
    record(
        "policy_matches",
        policy_ok,
        f"Policy ID matches active policy {policy.policy_id}."
        if policy_ok
        else f"Submitted policy ID '{submission.policy_id}' does not match the active policy '{policy.policy_id}'.",
        Problem(
            error_code="POLICY_MISMATCH",
            message=f"Policy ID '{submission.policy_id}' was submitted, but the active policy is '{policy.policy_id}'.",
            what_to_do_next=f"Resubmit with policy ID '{policy.policy_id}'.",
        ),
    )

    # 3. claim category is one the policy defines
    category_ok = policy.is_valid_category(submission.claim_category)
    valid_categories = ", ".join(policy.claim_categories())
    record(
        "category_valid",
        category_ok,
        f"Claim category {submission.claim_category.upper()} is covered by the policy."
        if category_ok
        else f"Unknown claim category '{submission.claim_category}'. Valid categories: {valid_categories}.",
        Problem(
            error_code="UNKNOWN_CATEGORY",
            message=f"'{submission.claim_category}' is not a claim category under this policy. "
            f"Valid categories are: {valid_categories}.",
            what_to_do_next="Pick the category that matches your treatment and resubmit.",
        ),
    )

    # 4. amount is a positive number
    amount_ok = submission.claimed_amount > 0
    record(
        "amount_positive",
        amount_ok,
        f"Claimed amount {format_inr(submission.claimed_amount)} is a positive number."
        if amount_ok
        else f"Claimed amount must be a positive number; received {submission.claimed_amount}.",
        Problem(
            error_code="INVALID_AMOUNT",
            message=f"Claimed amount must be a positive number; received {submission.claimed_amount}.",
            what_to_do_next="Enter the amount on your bill (a positive number) and resubmit.",
        ),
    )

    # 5. treatment date is valid and not in the future
    date_ok = submission.treatment_date <= submission_date
    record(
        "treatment_date_valid",
        date_ok,
        f"Treatment date {submission.treatment_date.isoformat()} is not in the future."
        if date_ok
        else f"Treatment date {submission.treatment_date.isoformat()} is in the future "
        f"(today is {submission_date.isoformat()}).",
        Problem(
            error_code="INVALID_DATE",
            message=f"Treatment date {submission.treatment_date.isoformat()} is in the future "
            f"(today is {submission_date.isoformat()}).",
            what_to_do_next="Enter the date the treatment actually happened and resubmit.",
        ),
    )

    # 6. at least one file; allowed types; size cap
    has_documents = len(submission.documents) > 0
    record(
        "documents_present",
        has_documents,
        f"{len(submission.documents)} document(s) attached."
        if has_documents
        else "No documents were attached to the claim.",
        Problem(
            error_code="NO_DOCUMENTS",
            message="No documents were attached. A claim needs supporting documents "
            "(e.g. prescription, bills) to be processed.",
            what_to_do_next="Attach the documents required for your claim category and resubmit.",
        ),
    )
    allowed = [e.lower() for e in config.files.allowed_extensions]
    max_bytes = int(config.files.max_file_mb * 1024 * 1024)
    for doc in submission.documents:
        # Stub documents (test cases) may carry no file name or bytes; extension and
        # size checks apply only to what is actually present.
        if doc.file_name and "." in doc.file_name:
            ext = Path(doc.file_name).suffix.lstrip(".").lower()
            ext_ok = ext in allowed
            record(
                f"file_type:{doc.file_id}",
                ext_ok,
                f"File '{doc.file_name}' has allowed type '{ext}'."
                if ext_ok
                else f"File '{doc.file_name}' has unsupported type '{ext}'.",
                Problem(
                    error_code="BAD_FILE",
                    message=f"File '{doc.file_name}' is a .{ext} file. Only "
                    f"{', '.join(allowed)} files are accepted.",
                    what_to_do_next=f"Re-save or photograph '{doc.file_name}' as one of: "
                    f"{', '.join(allowed)}, then resubmit.",
                    file_id=doc.file_id,
                    file_name=doc.file_name,
                ),
            )
        if doc.size_bytes is not None:
            size_ok = doc.size_bytes <= max_bytes
            record(
                f"file_size:{doc.file_id}",
                size_ok,
                f"File '{doc.file_name or doc.file_id}' is within the {config.files.max_file_mb} MB limit."
                if size_ok
                else f"File '{doc.file_name or doc.file_id}' is {doc.size_bytes / 1024 / 1024:.1f} MB, "
                f"over the {config.files.max_file_mb} MB limit.",
                Problem(
                    error_code="BAD_FILE",
                    message=f"File '{doc.file_name or doc.file_id}' is "
                    f"{doc.size_bytes / 1024 / 1024:.1f} MB; the limit is {config.files.max_file_mb} MB.",
                    what_to_do_next="Compress or re-photograph the document at a smaller size and resubmit.",
                    file_id=doc.file_id,
                    file_name=doc.file_name,
                ),
            )

    # 7. submission window (policy submission_rules) — only meaningful for a valid date
    deadline_days = int(policy.submission_rules["deadline_days_from_treatment"])
    if date_ok:
        deadline = submission.treatment_date + timedelta(days=deadline_days)
        window_ok = submission_date <= deadline
        record(
            "submission_window",
            window_ok,
            f"Submitted within {deadline_days} days of treatment (deadline was {deadline.isoformat()})."
            if window_ok
            else f"Submission deadline {deadline.isoformat()} was missed "
            f"({deadline_days} days from treatment on {submission.treatment_date.isoformat()}).",
            Problem(
                error_code="SUBMISSION_TOO_LATE",
                message=f"Claims must be submitted within {deadline_days} days of treatment. "
                f"Your treatment was on {submission.treatment_date.isoformat()}, so the deadline "
                f"was {deadline.isoformat()}.",
                what_to_do_next="Contact support if you believe you have grounds for a late-submission exception.",
            ),
        )

    # 8. minimum claim amount (policy submission_rules)
    minimum = float(policy.submission_rules["minimum_claim_amount"])
    min_ok = submission.claimed_amount >= minimum
    record(
        "minimum_amount",
        min_ok,
        f"Claimed amount {format_inr(submission.claimed_amount)} meets the {format_inr(minimum)} minimum."
        if min_ok
        else f"Claimed amount {format_inr(submission.claimed_amount)} is below the "
        f"{format_inr(minimum)} minimum claim amount.",
        Problem(
            error_code="BELOW_MINIMUM_AMOUNT",
            message=f"The minimum claim amount under this policy is {format_inr(minimum)}; "
            f"you claimed {format_inr(submission.claimed_amount)}.",
            what_to_do_next=f"Claims under {format_inr(minimum)} cannot be reimbursed. If you have "
            "other bills from the same treatment, combine them into one claim.",
        ),
    )

    return events, problems
