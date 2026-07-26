#!/usr/bin/env python3
"""Emit a versioned D11 capacity report, then run the deterministic gate.

The command is intentionally dependency-light and calls pytest as a child so
the JSON/Markdown artifact remains useful in a release drill even when the gate
fails.  Elapsed time is observational; only the operation limits are gating.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LIMITS_PATH = Path(__file__).with_name("limits.json")
TEST_PATH = ROOT / "services/ai-backend/tests/performance/e2"


def _report(*, started: float, completed: float, exit_code: int) -> dict[str, object]:
    limits = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "gate": "e2-performance-capacity",
        "limits_version": limits["version"],
        "status": "pass" if exit_code == 0 else "fail",
        "wall_clock_is_non_gating": limits["policy"]["wall_clock_is_non_gating"],
        "elapsed_seconds_observational": round(completed - started, 3),
        "fixtures": limits["fixtures"],
        "operation_limits": limits["operation_limits"],
        "postgres_explain": {
            "environment": limits["policy"]["postgres_explain_env"],
            "required_in_release_drill": limits["policy"][
                "postgres_explain_is_required_in_release_drill"
            ],
        },
    }


def _markdown(report: dict[str, object]) -> str:
    fixtures = report["fixtures"]
    limits = report["operation_limits"]
    return "\n".join(
        [
            "# E2 performance and capacity gate",
            "",
            f"- Status: **{report['status']}**",
            f"- Limits version: `{report['limits_version']}`",
            f"- Observed gate duration (non-gating): `{report['elapsed_seconds_observational']}s`",
            f"- Replay fixtures: `{fixtures['replay_events']}` events; CSV rows: `{fixtures['csv_rows']}`",
            "",
            "## Enforced deterministic bounds",
            "",
            *[f"- `{key}`: `{value}`" for key, value in limits.items()],
            "",
            "PostgreSQL EXPLAIN is intentionally opt-in for ordinary CI and required in the release drill via "
            f"`{report['postgres_explain']['environment']}`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/e2-performance"
    )
    parser.add_argument("--with-postgres", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pytest", str(TEST_PATH), "-q"]
    if not args.with_postgres:
        command.extend(["-m", "not postgres"])
    started = time.perf_counter()
    completed_process = subprocess.run(
        command, cwd=ROOT / "services/ai-backend", check=False
    )
    completed = time.perf_counter()
    report = _report(
        started=started, completed=completed, exit_code=completed_process.returncode
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "report.md").write_text(_markdown(report), encoding="utf-8")
    return completed_process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
