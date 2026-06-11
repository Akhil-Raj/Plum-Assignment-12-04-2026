"""Eval harness (deliverable #4): feeds the 12 test cases through the same
in-process pipeline the API uses and writes eval_report.md.

For every case the report shows the decision the system produced, the full trace,
and whether it matched the expected outcome — including an automated check (with
quoted evidence) for each `system_must` requirement, so "the message must name the
uploaded and required type" is verified, not asserted.

Stub documents make the classifier and reader LLM-free by design; the consistency
checker, decision prep, and fraud assessor are live LLM calls, so a representative
run needs ANTHROPIC_API_KEY. Without it the semantic stages degrade per design and
the report is stamped with a warning.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from app.config import ROOT_DIR, AppConfig, load_config
from app.main import build_agents
from app.models import ClaimRecord, ClaimStatus, ClaimSubmission, DecisionType, format_inr
from app.pipeline import build_pipeline
from app.policy_store import PolicyStore
from app.service import ClaimService
from app.storage import ClaimRepository

Checker = Callable[[ClaimRecord], tuple[bool, str]]


# ------------------------------------------------------------- searchable text

def _problems_text(record: ClaimRecord) -> str:
    return " | ".join(
        f"{p.error_code}: {p.message} NEXT: {p.what_to_do_next}" for p in record.problems
    )


def _decision_text(record: ClaimRecord) -> str:
    d = record.decision
    if d is None:
        return ""
    parts = [f"{r.code}: {r.detail}" for r in d.reasons]
    if d.what_to_do_next:
        parts.append(f"NEXT: {d.what_to_do_next}")
    if d.eligibility_date:
        parts.append(f"eligible from {d.eligibility_date}")
    parts.extend(d.manual_review_notes)
    return " | ".join(parts)


def _snippet(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _has(text: str, *needles: str) -> tuple[bool, str]:
    ok = all(n.lower() in text.lower() for n in needles)
    return ok, _snippet(text) if text else "(empty)"


def _stopped_without_decision(record: ClaimRecord) -> tuple[bool, str]:
    ok = record.decision is None and record.status == ClaimStatus.NEEDS_RESUBMISSION
    decision = "None" if record.decision is None else record.decision.decision.value
    return ok, f"status={record.status.value}, decision={decision}"


# --------------------------------------------- system_must checkers, per case
# Aligned by index with each case's `system_must` list in test_cases.json.

def _tc002_reupload_specific(record: ClaimRecord) -> tuple[bool, str]:
    unreadable = [p for p in record.problems if p.error_code == "UNREADABLE_DOCUMENT"]
    if not unreadable:
        return False, "no UNREADABLE_DOCUMENT problem"
    p = unreadable[0]
    ok = p.file_name == "blurry_bill.jpg" and "clearer" in p.what_to_do_next.lower()
    return ok, _snippet(f"{p.message} NEXT: {p.what_to_do_next}")


def _tc006_itemized(record: ClaimRecord) -> tuple[bool, str]:
    items = record.decision.line_item_breakdown if record.decision else []
    outcomes = {i.outcome for i in items}
    ok = {"APPROVED", "REJECTED"} <= outcomes
    return ok, "; ".join(f"{i.description}: {i.outcome}" for i in items) or "(no breakdown)"


def _tc006_item_reasons(record: ClaimRecord) -> tuple[bool, str]:
    rejected = [
        i for i in (record.decision.line_item_breakdown if record.decision else [])
        if i.outcome == "REJECTED"
    ]
    ok = bool(rejected) and all(i.reason for i in rejected)
    return ok, "; ".join(f"{i.description}: {i.reason}" for i in rejected) or "(no rejected items)"


def _tc009_same_day_flagged(record: ClaimRecord) -> tuple[bool, str]:
    reasons = record.decision.reasons if record.decision else []
    hit = next((r for r in reasons if r.code == "SAME_DAY_CLAIMS"), None)
    return (hit is not None), (hit.detail if hit else "no SAME_DAY_CLAIMS reason in the output")


def _tc009_routed_not_rejected(record: ClaimRecord) -> tuple[bool, str]:
    d = record.decision
    ok = d is not None and d.decision == DecisionType.MANUAL_REVIEW and not d.rejection_reasons
    return ok, f"decision={d.decision.value if d else None}, rejection_reasons={d.rejection_reasons if d else None}"


def _tc009_signals_specific(record: ClaimRecord) -> tuple[bool, str]:
    reasons = record.decision.reasons if record.decision else []
    hit = next((r for r in reasons if r.code == "SAME_DAY_CLAIMS"), None)
    if hit is None:
        return False, "no SAME_DAY_CLAIMS reason"
    return _has(hit.detail, "4", "2")


def _tc010_discount_before_copay(record: ClaimRecord) -> tuple[bool, str]:
    steps = record.decision.money_breakdown if record.decision else []
    names = [s.step for s in steps]
    if "network_discount" not in names or "copay" not in names:
        return False, f"money steps: {names}"
    di, ci = names.index("network_discount"), names.index("copay")
    ok = di < ci and abs(steps[ci].amount_before - steps[di].amount_after) < 0.01
    return ok, (
        f"steps in order: {names}; co-pay applied on {format_inr(steps[ci].amount_before)} "
        f"(the discounted amount)"
    )


def _tc010_breakdown_shown(record: ClaimRecord) -> tuple[bool, str]:
    steps = record.decision.money_breakdown if record.decision else []
    ok = len(steps) >= 2
    return ok, "; ".join(s.description for s in steps) or "(no breakdown)"


def _tc011_no_crash(record: ClaimRecord) -> tuple[bool, str]:
    ok = record.status == ClaimStatus.FINALIZED and record.decision is not None
    return ok, f"pipeline completed; status={record.status.value}"


def _tc011_failure_visible(record: ClaimRecord) -> tuple[bool, str]:
    skipped = [e for e in record.trace if e.result.value == "SKIPPED"]
    ok = bool(record.skipped_components) and bool(skipped)
    return ok, _snippet(
        f"skipped_components={record.skipped_components}; trace: {skipped[0].detail if skipped else '—'}"
    )


def _tc011_confidence_lower(record: ClaimRecord) -> tuple[bool, str]:
    confidence = record.decision.confidence if record.decision else record.confidence
    ok = confidence <= 0.9  # clean full-pipeline approvals score 1.0
    return ok, f"confidence {confidence:.2f} vs 1.00 for a clean full-pipeline approval"


def _tc011_review_note(record: ClaimRecord) -> tuple[bool, str]:
    d = record.decision
    ok = d is not None and d.manual_review_recommended and bool(d.manual_review_notes)
    return ok, "; ".join(d.manual_review_notes) if d and d.manual_review_notes else "(no note)"


SYSTEM_MUST_CHECKERS: dict[str, list[Checker]] = {
    "TC001": [
        _stopped_without_decision,
        lambda r: _has(_problems_text(r), "prescription", "hospital bill"),
        lambda r: _has(_problems_text(r), "prescription", "hospital bill"),
    ],
    "TC002": [
        lambda r: _has(_problems_text(r), "UNREADABLE_DOCUMENT", "blurry_bill.jpg"),
        _tc002_reupload_specific,
        _stopped_without_decision,
    ],
    "TC003": [
        lambda r: _has(_problems_text(r), "PATIENT_MISMATCH"),
        lambda r: _has(_problems_text(r), "Rajesh Kumar", "Arjun Mehta"),
        _stopped_without_decision,
    ],
    "TC005": [
        lambda r: _has(_decision_text(r), "2024-11-30"),
    ],
    "TC006": [_tc006_itemized, _tc006_item_reasons],
    "TC007": [
        lambda r: _has(_decision_text(r), "pre-auth"),
        lambda r: _has(_decision_text(r), "resubmit"),
    ],
    "TC008": [
        lambda r: _has(_decision_text(r), "₹7,500", "₹5,000"),
    ],
    "TC009": [_tc009_same_day_flagged, _tc009_routed_not_rejected, _tc009_signals_specific],
    "TC010": [_tc010_discount_before_copay, _tc010_breakdown_shown],
    "TC011": [_tc011_no_crash, _tc011_failure_visible, _tc011_confidence_lower, _tc011_review_note],
}


# ------------------------------------------------------------------ evaluation

FieldRow = tuple[str, str, str, bool]  # label, expected, actual, ok
MustRow = tuple[str, bool, str]  # requirement, ok, evidence


@dataclass
class CaseResult:
    case: dict
    record: Optional[ClaimRecord]
    error: Optional[str]
    duration_s: float
    field_rows: list[FieldRow]
    must_rows: list[MustRow]

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and all(ok for *_, ok in self.field_rows)
            and all(ok for _, ok, _ in self.must_rows)
        )


def evaluate_case(case: dict, record: ClaimRecord) -> tuple[list[FieldRow], list[MustRow]]:
    expected = case["expected"]
    rows: list[FieldRow] = []

    if "decision" in expected:
        exp = expected["decision"]
        actual = record.decision.decision.value if record.decision else None
        if exp is None:
            ok, evidence = _stopped_without_decision(record)
            rows.append(("Decision", "null — stop before any decision", evidence, ok))
        else:
            rows.append(("Decision", exp, str(actual), actual == exp))

    if "approved_amount" in expected:
        exp_amount = float(expected["approved_amount"])
        actual_amount = record.decision.approved_amount if record.decision else None
        ok = actual_amount is not None and abs(actual_amount - exp_amount) < 0.01
        rows.append((
            "Approved amount",
            format_inr(exp_amount),
            format_inr(actual_amount) if actual_amount is not None else "—",
            ok,
        ))

    if "rejection_reasons" in expected:
        exp_reasons = list(expected["rejection_reasons"])
        actual_reasons = record.decision.rejection_reasons if record.decision else []
        rows.append((
            "Rejection reasons",
            ", ".join(exp_reasons),
            ", ".join(actual_reasons) or "—",
            actual_reasons == exp_reasons,
        ))

    if "confidence_score" in expected:
        match = re.search(r"above\s+([0-9.]+)", str(expected["confidence_score"]))
        if match:
            bound = float(match.group(1))
            actual_conf = record.decision.confidence if record.decision else record.confidence
            rows.append(("Confidence", f"above {bound}", f"{actual_conf:.2f}", actual_conf > bound))

    musts: list[MustRow] = []
    checkers = SYSTEM_MUST_CHECKERS.get(case["case_id"], [])
    for i, requirement in enumerate(expected.get("system_must", [])):
        if i < len(checkers):
            ok, evidence = checkers[i](record)
            musts.append((requirement, ok, evidence))
        else:
            musts.append((requirement, False, "no automated checker defined — verify manually"))
    return rows, musts


# --------------------------------------------------------------------- running

async def run_eval(
    config: Optional[AppConfig] = None, cases_path: Optional[Path] = None
) -> tuple[list[CaseResult], bool]:
    config = config or load_config()
    # eval runs get their own database so they never pollute the app's claims
    config.storage.db_path = "data/eval_claims.db"
    db_path = config.resolve(config.storage.db_path)
    if db_path.exists():
        db_path.unlink()

    key_present = bool(os.environ.get(config.llm.api_key_env))
    policy = PolicyStore(config.resolve(config.policy.policy_file))
    repo = ClaimRepository(db_path)
    runner = build_pipeline(policy, config, build_agents(config))
    service = ClaimService(config=config, policy=policy, repo=repo, runner=runner)

    cases = json.loads(
        (cases_path or ROOT_DIR / "test_cases.json").read_text()
    )["test_cases"]

    results: list[CaseResult] = []
    for case in cases:
        started = time.monotonic()
        record: Optional[ClaimRecord] = None
        error: Optional[str] = None
        try:
            submission = ClaimSubmission(
                **{**case["input"], "submission_date": case["input"]["treatment_date"]}
            )
            record = await service.submit(submission)
            field_rows, must_rows = evaluate_case(case, record)
        except Exception as exc:  # a crash IS a failed case — report it, keep going
            error = f"{type(exc).__name__}: {exc}"
            field_rows, must_rows = [], []
        duration = time.monotonic() - started
        result = CaseResult(case, record, error, duration, field_rows, must_rows)
        results.append(result)
        print(
            f"  {case['case_id']}  {'PASS' if result.passed else 'FAIL':4}  "
            f"{duration:5.1f}s  {case['case_name']}",
            flush=True,
        )
    return results, key_present


# ------------------------------------------------------------------- rendering

def _md(text: str) -> str:
    """Make a string safe inside a markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def render_report(results: list[CaseResult], config: AppConfig, key_present: bool) -> str:
    n_pass = sum(1 for r in results if r.passed)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    out = lines.append

    out("# Eval Report — 12 Test Cases")
    out("")
    out(f"**Result: {n_pass}/{len(results)} PASS** · generated {now} · "
        f"total runtime {sum(r.duration_s for r in results):.1f}s")
    out("")
    if not key_present:
        out("> ⚠️ **This run had no `ANTHROPIC_API_KEY`.** The deterministic stages "
            "(intake, document gate, extraction of stub content, rules engine, fraud "
            "thresholds) ran normally, but the semantic agents (consistency checker, "
            "decision prep, fraud assessor) degraded by design, so most decisions fall "
            "back to MANUAL_REVIEW. Re-run with a key for the representative report.")
        out("")
    out("Reproduce: `ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_eval.py`")
    out("")
    out("How the cases are fed: each `input` is POSTed through the same in-process "
        "service the API uses, with `submission_date` pinned to the treatment date so "
        "the 30-day submission window holds for the 2024-dated scenarios. Documents "
        "are test-case stubs, so the vision classifier and reader are bypassed by the "
        "stub adapter (zero LLM calls there, deterministic); the consistency checker, "
        "decision prep, and fraud assessor are live LLM calls. TC001/TC002 stop at "
        "the deterministic document gate and use no LLM at all.")
    out("")
    models = config.llm.models
    out(f"Models: classifier `{models.classifier}`, reader `{models.reader}`, "
        f"consistency `{models.consistency}`, prep `{models.prep}`, "
        f"fraud assessor `{models.fraud_assessor}`.")
    out("")

    out("| Case | Name | Expected | Actual | Result |")
    out("|---|---|---|---|---|")
    for r in results:
        expected = r.case["expected"]
        exp_decision = expected.get("decision")
        exp_text = "stop (no decision)" if exp_decision is None else exp_decision
        if "approved_amount" in expected:
            exp_text += f" {format_inr(float(expected['approved_amount']))}"
        if r.error:
            actual_text = f"ERROR: {r.error}"
        elif r.record and r.record.decision:
            actual_text = r.record.decision.decision.value
            if r.record.decision.decision in (DecisionType.APPROVED, DecisionType.PARTIAL):
                actual_text += f" {format_inr(r.record.decision.approved_amount)}"
        elif r.record:
            actual_text = f"{r.record.status.value} (no decision)"
        else:
            actual_text = "—"
        out(f"| {r.case['case_id']} | {_md(r.case['case_name'])} | {_md(exp_text)} "
            f"| {_md(actual_text)} | {'✅ PASS' if r.passed else '❌ FAIL'} |")
    out("")

    for r in results:
        out("---")
        out("")
        out(f"## {r.case['case_id']} — {r.case['case_name']} — "
            f"{'✅ PASS' if r.passed else '❌ FAIL'}")
        out("")
        out(f"*{r.case.get('description', '')}*")
        out("")
        if r.error:
            out(f"**The pipeline raised an exception (this alone fails the case):** `{r.error}`")
            out("")
            continue

        if r.field_rows:
            out("| Field | Expected | Actual | OK |")
            out("|---|---|---|---|")
            for label, expected_v, actual_v, ok in r.field_rows:
                out(f"| {label} | {_md(expected_v)} | {_md(actual_v)} | {'✅' if ok else '❌'} |")
            out("")
        if r.must_rows:
            out("**System must:**")
            out("")
            for requirement, ok, evidence in r.must_rows:
                out(f"- {'✅' if ok else '❌'} {requirement}")
                out(f"  - evidence: `{_md(evidence)}`")
            out("")
        if not r.passed:
            out("**Why it didn't match:**")
            out("")
            for label, expected_v, actual_v, ok in r.field_rows:
                if not ok:
                    out(f"- {label}: expected {_md(expected_v)}, got {_md(actual_v)}.")
            for requirement, ok, evidence in r.must_rows:
                if not ok:
                    out(f"- Unmet: \"{requirement}\" — {_md(evidence)}.")
            out("")
            out("See the decision output and full trace below for the step where the "
                "outcome diverged.")
            out("")

        record = r.record
        decision_json = (
            record.decision.model_dump_json(indent=2) if record.decision else "null"
        )
        out("<details><summary><b>Decision output</b></summary>")
        out("")
        out("```json")
        out(decision_json)
        out("```")
        out("</details>")
        out("")
        out(f"<details><summary><b>Full trace</b> ({len(record.trace)} events, "
            f"final confidence {record.confidence:.2f})</summary>")
        out("")
        out("```text")
        for event in record.trace:
            out(f"[{event.result.value:7}] {event.stage} / {event.check_name}")
            out(f"          {event.detail}")
        out("```")
        out("</details>")
        out("")

    return "\n".join(lines)
