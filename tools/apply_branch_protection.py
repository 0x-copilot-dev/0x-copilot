"""Reconcile deploy/branch-protection.json against the repository's rulesets.

Run by .github/workflows/apply-branch-protection.yml. Lives here rather than in a
YAML heredoc on purpose: the previous inline version carried a module-level
``return`` and died with ``SyntaxError: 'return' outside function`` on every
dispatch, so the rulesets it described were never applied and ``main`` sat
unprotected. A heredoc is invisible to ruff, to pytest, and to review. A module
is not, and ``test_apply_branch_protection.py`` exercises the planner without
touching the network.

Usage:
    python tools/apply_branch_protection.py --dry-run
    python tools/apply_branch_protection.py --apply
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "deploy/branch-protection.json"

# Server-side metadata that the API returns but the config never sets. Diffing
# without stripping these reports a change on every run and makes a no-op look
# like drift.
_SERVER_ONLY_KEYS = (
    "id",
    "node_id",
    "_links",
    "source",
    "source_type",
    "current_user_can_bypass",
    "created_at",
    "updated_at",
)


class RulesetConfigError(ValueError):
    """The on-disk config is not a shape this script can apply."""


class GitHubCli:
    """Thin seam over ``gh api`` so tests can substitute a recorded transcript."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def list_rulesets(self) -> list[dict[str, Any]]:
        """Return every ruleset defined on the repository."""
        return self._json(["gh", "api", f"repos/{self.repo}/rulesets"])

    def get_ruleset(self, ruleset_id: int) -> dict[str, Any]:
        """Return one ruleset, including its rules (the list endpoint omits them)."""
        return self._json(["gh", "api", f"repos/{self.repo}/rulesets/{ruleset_id}"])

    def create_ruleset(self, desired: dict[str, Any]) -> None:
        """Create a ruleset from ``desired``."""
        self._send(["gh", "api", f"repos/{self.repo}/rulesets", "-X", "POST"], desired)

    def update_ruleset(self, ruleset_id: int, desired: dict[str, Any]) -> None:
        """Replace ruleset ``ruleset_id`` with ``desired``."""
        self._send(
            ["gh", "api", f"repos/{self.repo}/rulesets/{ruleset_id}", "-X", "PUT"],
            desired,
        )

    @staticmethod
    def _json(command: list[str]) -> Any:
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        return json.loads(completed.stdout)

    @staticmethod
    def _send(command: list[str], payload: dict[str, Any]) -> None:
        subprocess.run(
            [*command, "--input", "-"],
            input=json.dumps(payload),
            text=True,
            check=True,
        )


class RulesetPlanner:
    """Decides what would change, without performing any of it.

    Kept free of I/O so the decision logic is unit-testable: every method takes
    the current state as an argument rather than fetching it.
    """

    @classmethod
    def load_desired(cls, config_path: Path = CONFIG_PATH) -> list[dict[str, Any]]:
        """Read the config and return the rulesets it declares, annotations stripped."""
        raw = json.loads(config_path.read_text())
        if not isinstance(raw, dict) or "rulesets" not in raw:
            raise RulesetConfigError(
                "branch-protection.json must be an object containing a 'rulesets' list"
            )
        rulesets = raw["rulesets"]
        if not isinstance(rulesets, list) or not rulesets:
            raise RulesetConfigError("'rulesets' must be a non-empty list")
        for ruleset in rulesets:
            if not isinstance(ruleset, dict) or not ruleset.get("name"):
                raise RulesetConfigError("every ruleset needs a 'name'")
        names = [ruleset["name"] for ruleset in rulesets]
        if len(names) != len(set(names)):
            raise RulesetConfigError(f"duplicate ruleset names: {sorted(names)}")
        return [cls._strip_annotations(ruleset) for ruleset in rulesets]

    @staticmethod
    def _strip_annotations(ruleset: dict[str, Any]) -> dict[str, Any]:
        """Drop ``$``-prefixed documentation keys the API would reject."""
        return {key: value for key, value in ruleset.items() if not key.startswith("$")}

    @staticmethod
    def comparable(ruleset: dict[str, Any]) -> dict[str, Any]:
        """Return ``ruleset`` without server-managed fields, for diffing."""
        return {
            key: value for key, value in ruleset.items() if key not in _SERVER_ONLY_KEYS
        }

    @classmethod
    def diff(cls, current: dict[str, Any], desired: dict[str, Any]) -> str:
        """Return a unified diff of current vs desired, or '' when they match."""
        left = json.dumps(
            cls.comparable(current), indent=2, sort_keys=True
        ).splitlines()
        right = json.dumps(
            cls.comparable(desired), indent=2, sort_keys=True
        ).splitlines()
        if left == right:
            return ""
        return "\n".join(
            difflib.unified_diff(
                left, right, fromfile="repo", tofile="desired", lineterm=""
            )
        )


class RulesetReconciler:
    """Applies (or previews) the plan for every declared ruleset."""

    def __init__(self, cli: GitHubCli, *, dry_run: bool) -> None:
        self.cli = cli
        self.dry_run = dry_run

    def run(self, desired_rulesets: list[dict[str, Any]]) -> int:
        """Reconcile each ruleset; return the number that changed (or would)."""
        existing = {
            ruleset.get("name"): ruleset for ruleset in self.cli.list_rulesets()
        }
        changed = 0
        for desired in desired_rulesets:
            if self._reconcile_one(desired, existing.get(desired["name"])):
                changed += 1
        if self.dry_run and changed:
            print(f"\nDRY RUN: {changed} ruleset(s) would change. Re-run with --apply.")
        elif not changed:
            print("\nAll rulesets already match deploy/branch-protection.json. No-op.")
        return changed

    def _reconcile_one(
        self, desired: dict[str, Any], match: dict[str, Any] | None
    ) -> bool:
        """Create or update one ruleset; return whether anything changed."""
        name = desired["name"]
        if match is None:
            print(f"\n=== {name}: does not exist -> CREATE")
            if not self.dry_run:
                self.cli.create_ruleset(desired)
                print(f"CREATED {name}")
            return True

        ruleset_id = match["id"]
        current = self.cli.get_ruleset(ruleset_id)
        diff = RulesetPlanner.diff(current, desired)
        if not diff:
            print(f"\n=== {name}: already matches (id={ruleset_id})")
            return False

        print(f"\n=== {name}: drift detected (id={ruleset_id})")
        print(diff)
        if not self.dry_run:
            self.cli.update_ruleset(ruleset_id, desired)
            print(f"UPDATED {name} (id={ruleset_id})")
        return True


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args, load config, reconcile."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff without applying (default)",
    )
    group.add_argument(
        "--apply", action="store_true", help="apply the config to the repository"
    )
    args = parser.parse_args(argv)
    dry_run = not args.apply

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        print("GITHUB_REPOSITORY is not set", file=sys.stderr)
        return 2

    try:
        desired = RulesetPlanner.load_desired()
    except (RulesetConfigError, json.JSONDecodeError) as error:
        print(f"deploy/branch-protection.json is invalid: {error}", file=sys.stderr)
        return 2

    print(
        f"Reconciling {len(desired)} ruleset(s) against {repo} "
        f"({'DRY RUN' if dry_run else 'APPLY'})"
    )
    RulesetReconciler(GitHubCli(repo), dry_run=dry_run).run(desired)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
