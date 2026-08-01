"""Ratchet for tools/ai-backend-smells/orphans.py — the orphan set may shrink,
never grow unnoticed.

An orphan is a module in ``src`` that nothing in ``src`` imports: built, usually
tested, and never reached. Ten such modules sat invisible for up to 2.5 months
because "landed, not yet wired" is a state this repo had no ledger for
(docs/plan/ai-backend-consolidation/HANDOFF.md §4-5). This test freezes the known
set in ``orphan_ratchet_baseline.txt``: wiring an orphan up (or deleting it) is
welcome and never fails here; a NEW unwired module fails until it is either wired
or consciously recorded in the baseline — the review moment that keeps the debt
visible.

The scanner is a repo-root CLI (stdlib only), so this drives its real
entrypoint. If the tool tree is absent (a service-only checkout) the ratchet
cannot run and says so loudly rather than passing — a skip is a distinct
outcome, not a green. ``make verify-ai-backend`` runs from the full checkout,
which is where the ratchet enforces.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_AI_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCANNER = _REPO_ROOT / "tools" / "ai-backend-smells" / "orphans.py"
_BASELINE = Path(__file__).resolve().parent / "orphan_ratchet_baseline.txt"


@pytest.fixture(scope="module")
def scanner_report() -> dict:
    """Run the real scanner CLI once and return its parsed report.

    Skips loudly when the repo tool tree is absent; enforces from a full
    checkout. A ``returncode`` other than zero fails rather than passes — a
    ratchet that cannot run must not read as green.
    """

    if not _SCANNER.is_file():
        pytest.skip(
            f"orphan scanner absent at {_SCANNER}; the ratchet runs from a full "
            "checkout (make verify-ai-backend), not a service-only tree"
        )
    proc = subprocess.run(
        [sys.executable, str(_SCANNER), str(_AI_BACKEND_ROOT)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, (
        f"orphan scanner exited {proc.returncode}; a ratchet that cannot run "
        f"must fail, not pass.\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def _orphan_modules(report: dict) -> set[str]:
    return {
        entry["module"]
        for bucket in ("orphan_tested", "orphan_untested")
        for entry in report.get(bucket, ())
    }


def _baseline_modules() -> set[str]:
    lines = _BASELINE.read_text(encoding="utf-8").splitlines()
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_scanner_actually_inspected_the_source_tree(scanner_report: dict) -> None:
    """Prove the scan looked, so a broken scanner cannot pass the ratchet.

    A scan that silently walked nothing would satisfy "no new orphans" while
    inspecting zero modules — the exact failure the context-origin gate warns of
    ("a gate that returns () is indistinguishable from one that is not looking").
    Pin that it walked a substantial tree and still finds orphans.
    """

    assert scanner_report["totals"]["src_modules"] > 500
    assert _orphan_modules(scanner_report), (
        "scanner found no orphans at all — either every module is now wired (a "
        "milestone: empty the baseline and update this test) or the scan broke"
    )


def test_no_new_orphans_beyond_baseline(scanner_report: dict) -> None:
    """No module outside the baseline may be an orphan."""

    new = _orphan_modules(scanner_report) - _baseline_modules()
    assert not new, (
        f"{len(new)} module(s) became orphaned without being recorded: "
        f"{sorted(new)}.\n"
        "An orphan is built but nothing in src imports it. Either wire it in "
        "(preferred), or — if it is deliberately landing ahead of its wiring — "
        f"add it to {_BASELINE.name} AND record what it waits on in "
        "docs/audit/ai-backend-smells/PENDING-WIRINGS.md (T1.1). Adding it to "
        "the baseline with no ledger entry just re-hides the debt."
    )
