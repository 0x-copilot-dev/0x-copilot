"""Loader for the tier-2 adapter allowlist (the JSON sibling of this module).

Single source of truth shared with the desktop's 6A AST scanner via
``packages/api-types/src/adapterAllowlist.ts``. Both runtimes load this at
module import; the data is treated as const after the process starts.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TypedDict


class AdapterAllowlist(TypedDict):
    schema_version: int
    allowed_imports: dict[str, list[str]]
    forbidden_globals: list[str]
    forbidden_syntax: list[str]
    budget_ms: int


class _AllowlistResource:
    """Where the JSON sibling lives inside the installed package."""

    PACKAGE: str = "copilot_service_contracts"
    FILENAME: str = "adapter_allowlist.json"


def load_adapter_allowlist() -> AdapterAllowlist:
    raw = (
        files(_AllowlistResource.PACKAGE)
        .joinpath(_AllowlistResource.FILENAME)
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)


__all__ = ["AdapterAllowlist", "load_adapter_allowlist"]
