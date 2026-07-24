"""Repository gate: v2.1 event values are referenced through contract enums."""

from __future__ import annotations

from pathlib import Path
import re

from copilot_service_contracts.work_ledger import LEDGER_EVENT_TYPES


_REPO_ROOT = Path(__file__).resolve().parents[6]
_SOURCE_ROOTS = (
    _REPO_ROOT / "services",
    _REPO_ROOT / "packages",
    _REPO_ROOT / "apps",
)
_ALLOWED_MIRRORS = {
    _REPO_ROOT / "services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py",
    _REPO_ROOT / "packages/api-types/src/ledger.ts",
}
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs"}


def test_new_event_values_are_not_redeclared_outside_contract_mirrors() -> None:
    new_events = tuple(LEDGER_EVENT_TYPES[15:])
    pattern = re.compile(
        r"""(?P<quote>["'])(?:%s)(?P=quote)"""
        % "|".join(re.escape(value) for value in new_events)
    )
    violations: list[str] = []
    for root in _SOURCE_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in _SOURCE_SUFFIXES or not path.is_file():
                continue
            if (
                path in _ALLOWED_MIRRORS
                or "tests" in path.parts
                or ".test." in path.name
            ):
                continue
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{path.relative_to(_REPO_ROOT)}:{line}: {match.group(0)}"
                )

    assert violations == [], (
        "new Work Ledger event values must come from the SSOT/mirror enum; "
        f"inline duplicates found: {violations}"
    )
