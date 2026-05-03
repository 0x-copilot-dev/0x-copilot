#!/usr/bin/env python3
"""Enforce the canary -> early -> general staged rollout policy for production.

Queries the backend deploy audit endpoint via a read-side helper (today: the audit
events store is in-memory per backend instance, so this script SHOULD be backed by a
durable audit query API once the postgres adapter lands — see ci-assurance-spec.md
'Known gaps'). For now, the script accepts a JSON file of prior deploy events
(supplied by the workflow via a GET /internal/v1/audit/deploys?release_sha=... call,
or stubbed during dry-run) and applies the gap rule.

Inputs (env or args):
  --tenant-tier               this tenant's tier (canary|early|general)
  --release-sha               the release SHA being promoted
  --prior-deploys-json        path to JSON list of {tier, environment, completed_at, outcome}
  --stage-gap-hours           required gap between tiers (default 4)
  --force                     if true, log a warning and exit 0

Exits 0 if rollout is permitted, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TIER_ORDER = ["canary", "early", "general"]


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-tier", required=True, choices=TIER_ORDER)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--prior-deploys-json", required=True)
    parser.add_argument("--stage-gap-hours", type=float, default=4.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    tier_index = TIER_ORDER.index(args.tenant_tier)
    if tier_index == 0:
        print("OK: canary tier — no upstream gate")
        return 0

    prior_path = Path(args.prior_deploys_json)
    if not prior_path.exists():
        print(f"FAIL: prior deploys file missing at {prior_path}", file=sys.stderr)
        return 1

    prior = json.loads(prior_path.read_text())

    required_upstream = TIER_ORDER[:tier_index]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.stage_gap_hours)
    issues: list[str] = []

    for upstream in required_upstream:
        successes = [
            d
            for d in prior
            if d.get("tier") == upstream
            and d.get("environment") == "production"
            and d.get("outcome") == "success"
        ]
        if not successes:
            issues.append(
                f"no successful production deploy of release_sha={args.release_sha} "
                f"in upstream tier {upstream!r}"
            )
            continue
        latest_completion = max(_parse_dt(s["completed_at"]) for s in successes)
        if latest_completion > cutoff:
            wait_hours = (latest_completion - cutoff).total_seconds() / 3600
            issues.append(
                f"upstream tier {upstream!r} completed at {latest_completion.isoformat()}; "
                f"need {wait_hours:.2f}h more before promoting to {args.tenant_tier!r}"
            )

    if issues:
        if args.force:
            print("WARN: staged-rollout policy bypassed via --force", file=sys.stderr)
            for i in issues:
                print(f"  - {i}", file=sys.stderr)
            return 0
        print("FAIL: staged-rollout policy violation:", file=sys.stderr)
        for i in issues:
            print(f"  - {i}", file=sys.stderr)
        return 1

    print(
        f"OK: tier {args.tenant_tier!r} permitted to deploy release_sha={args.release_sha}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
