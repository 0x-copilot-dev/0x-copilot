#!/usr/bin/env python3
"""Score a benchmark arm from the run store the app actually wrote.

The first cut of `recursion_ceiling_ab.py` counted `usage.recorded` off the
events API and got zero for every task — the matcher was wrong, and a broken
instrument reporting 0 looks exactly like a cheap run. This scorer reads the
file-native store instead, which is the same data the product bills from:

    <userData>/agent-data/v1/state/run_usage.jsonl        one row per run
    <userData>/agent-data/v1/state/tool_invocations.jsonl one row per tool call

Rescoring is offline and free, so an arm never has to be re-run against a paid
model to fix a measurement mistake.

    python tools/harness-bench/rescore.py arm-25 arm-500

`tool_rounds` counts COMPLETED tool invocations, which is a lower bound on the
graph's super-step spend, not the spend itself: a super-step is a graph node
execution and a turn spends several without calling a tool. It is the honest
proxy available from the store, and it is the right shape for the question —
if the lower bound is nowhere near 25, the ceiling was never binding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "0xCopilot"
HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


def session_dir(arm: str) -> Path | None:
    """Newest userData dir for this arm's DriverSession name."""

    stem = f"journey-bench-recursion-{arm.removeprefix('arm-')}-"
    candidates = sorted(
        (p for p in APP_SUPPORT.glob(f"{stem}*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_state(directory: Path, name: str) -> list[dict]:
    path = directory / "agent-data" / "v1" / "state" / name
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line).get("record")
        if isinstance(record, dict):
            rows.append(record)
    return rows


def score(arm: str) -> dict | None:
    report_path = RUNS / f"{arm}.json"
    if not report_path.is_file():
        print(f"  no report at {report_path}")
        return None
    report = json.loads(report_path.read_text())
    directory = session_dir(arm)
    if directory is None:
        print(f"  no session dir for {arm}")
        return None

    usage = {r.get("run_id"): r for r in load_state(directory, "run_usage.jsonl")}
    tools: dict[str, list[dict]] = {}
    for row in load_state(directory, "tool_invocations.jsonl"):
        tools.setdefault(str(row.get("run_id")), []).append(row)

    for task in report["tasks"]:
        run_id = task.get("run_id")
        u = usage.get(run_id, {})
        calls = tools.get(run_id, [])
        done = [c for c in calls if c.get("status") == "completed"]
        failed = [c for c in calls if c.get("status") == "failed"]
        task["input_tokens"] = u.get("input_tokens", 0)
        task["output_tokens"] = u.get("output_tokens", 0)
        task["cached_input_tokens"] = u.get("cached_input_tokens", 0)
        task["total_tokens"] = u.get("total_tokens", 0)
        task["duration_ms"] = u.get("duration_ms", 0)
        task["tool_rounds"] = len(done)
        task["tool_failures"] = [
            f"{c.get('tool_name')}:{c.get('safe_error_code')}" for c in failed
        ]
    report["session_dir"] = str(directory)
    report_path.write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    arms = sys.argv[1:] or ["arm-25", "arm-500"]
    scored = [r for r in (score(a) for a in arms) if r]
    if not scored:
        return 2

    print(
        f"\n{'arm':<8}{'task':<18}{'status':<11}{'rounds':>7}{'in':>9}"
        f"{'cached':>9}{'out':>7}{'total':>8}{'ms':>8}  failures"
    )
    peak = 0
    for report in scored:
        arm = report["recursion_limit"]
        for task in report["tasks"]:
            peak = max(peak, task["tool_rounds"])
            print(
                f"{arm:<8}{task['task']:<18}{str(task['status']):<11}"
                f"{task['tool_rounds']:>7}{task['input_tokens']:>9}"
                f"{task['cached_input_tokens']:>9}{task['output_tokens']:>7}"
                f"{task['total_tokens']:>8}{task['duration_ms']:>8}  "
                f"{','.join(task['tool_failures']) or '-'}"
            )

    print()
    for report in scored:
        done = sum(1 for t in report["tasks"] if t["status"] == "completed")
        total_tokens = sum(t["total_tokens"] for t in report["tasks"])
        print(
            f"  limit={report['recursion_limit']}: {done}/{len(report['tasks'])} "
            f"completed, {total_tokens} total tokens"
        )

    print(f"\n  peak COMPLETED tool rounds in any task: {peak}")
    if peak <= 25:
        print(
            "  → the inherited ceiling of 25 was never approached, so raising it\n"
            "    to 500 bought nothing on this task set. Do not claim a\n"
            "    completion-rate win from this data."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
