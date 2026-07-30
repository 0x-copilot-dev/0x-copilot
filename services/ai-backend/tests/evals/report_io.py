"""Shared report writer for every eval family under ``tests/evals``.

The harness is one thing with several families (``surfaces``, ``publication``).
They score different pipelines, but a report is a report: pretty, key-sorted
JSON, stable byte-for-byte across runs so a committed baseline diffs cleanly.
Keeping the writer here is what makes "same harness, another family" true
rather than aspirational.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(report: dict[str, Any], path: Path) -> None:
    """Write the report as pretty, sorted JSON (stable across runs)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


__all__ = ["write_report"]
