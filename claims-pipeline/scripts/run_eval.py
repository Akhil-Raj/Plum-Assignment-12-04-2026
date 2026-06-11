"""Run all 12 test cases through the pipeline and write the eval report.

Usage:
    ANTHROPIC_API_KEY=sk-... .venv/bin/python scripts/run_eval.py [--output eval_report.md]

Stub documents keep the classifier/reader LLM-free; the consistency checker,
decision prep, and fraud assessor are live LLM calls — without a key they degrade
by design and the report is stamped with a warning.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.evaluation import render_report, run_eval  # noqa: E402


async def amain(output: Path) -> int:
    config = load_config()
    print(f"Running 12 test cases (models: prep={config.llm.models.prep}, "
          f"consistency={config.llm.models.consistency}) ...", flush=True)
    results, key_present = await run_eval(config)
    if not key_present:
        print("\n⚠️  No ANTHROPIC_API_KEY set — semantic stages degraded by design; "
              "this report is NOT representative. Re-run with a key.")
    report = render_report(results, config, key_present)
    output.write_text(report)
    n_pass = sum(1 for r in results if r.passed)
    print(f"\n{n_pass}/{len(results)} PASS — report written to {output}")
    return 0 if n_pass == len(results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "eval_report.md",
        help="where to write the report (default: eval_report.md at the project root)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(amain(args.output)))


if __name__ == "__main__":
    main()
