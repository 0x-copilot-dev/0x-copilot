"""E2 D9 release/conformance CLI.  Read-only; no rollout is changed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (
    REPO_ROOT / "services/ai-backend/src",
    REPO_ROOT / "packages/service-contracts/src",
):
    sys.path.insert(0, str(path))

from agent_runtime.release.e2_final_conformance import E2FinalConformanceRunner  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_e2_final_conformance")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="exit non-zero unless every D9 condition passes",
    )
    args = parser.parse_args(argv)
    report = E2FinalConformanceRunner().run()
    if args.as_json:
        print(json.dumps(report.as_json(), sort_keys=True))
    else:
        for item in report.conditions:
            print(f"[{item.status.value.upper()}] D9.{item.number} {item.title}")
            for evidence in item.evidence:
                print(f"  - {evidence}")
        print("READY" if report.ready else "NOT READY")
    return 0 if (report.ready or not args.require_all) else 1


if __name__ == "__main__":
    raise SystemExit(main())
