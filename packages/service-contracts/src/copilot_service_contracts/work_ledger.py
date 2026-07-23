"""Loader + constants for the Work Ledger vocabulary (the JSON sibling of this module).

Single source of truth for the Generative Surfaces v2 ledger event vocabulary
(SDR §5). The ai-backend pydantic models
(``agent_runtime.surfaces_v2.ledger_models``) validate against this contract;
the TypeScript types + runtime guards in ``packages/api-types`` (``ledger.ts``)
mirror it. Cross-language parity tests pin both sides to this file so the three
mirrors cannot drift.

Follows the ``surface_spec`` / ``adapter_allowlist`` precedent: the JSON is
loaded on demand and treated as const after the process starts. A companion
golden-events fixture ships beside the contract; later PRDs (A3 server fold, B1
client fold) fold that exact file and must agree.
"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable

# Payload version carried by every ledger event's ``v`` field. Const 1 from day
# one (SDR §5); the vocabulary is additive-only until the E-wave freeze, so a
# bump is a contract amendment to PRD-A1, never a local edit.
LEDGER_PAYLOAD_VERSION: int = 1

# The 14 ledger event types, in contract order. Mirrors ``events`` insertion
# order in ``work_ledger.json``; a parity test pins this tuple, the pydantic
# ``LedgerEventType`` StrEnum, and the api-types ``LEDGER_EVENT_TYPES`` tuple to
# that order. Later waves append (never reorder) — SDR §12.
LEDGER_EVENT_TYPES: tuple[str, ...] = (
    "gate.opened",
    "gate.resolved",
    "action.classified",
    "read.executed",
    "surface.created",
    "view.derived",
    "view.preference",
    "shape.requested",
    "write.staged",
    "revision.added",
    "decision.recorded",
    "write.applied",
    "usage.recorded",
    "receipt.emitted",
)


class _ContractResource:
    """Where the JSON siblings live inside the installed package."""

    PACKAGE: str = "copilot_service_contracts"
    CONTRACT_FILENAME: str = "work_ledger.json"
    GOLDEN_EVENTS_FILENAME: str = "work_ledger_golden_events.json"


# Traversable handle to the contract file, resolvable whether the package is
# installed on disk or imported from source via ``PYTHONPATH``.
WORK_LEDGER_CONTRACT_PATH: Traversable = files(_ContractResource.PACKAGE).joinpath(
    _ContractResource.CONTRACT_FILENAME
)

# Traversable handle to the golden-events fixture (D5). Loadable py-side via
# ``load_ledger_golden_events``; the ts side imports it by relative path in its
# test file only.
LEDGER_GOLDEN_EVENTS_PATH: Traversable = files(_ContractResource.PACKAGE).joinpath(
    _ContractResource.GOLDEN_EVENTS_FILENAME
)


def load_work_ledger_contract() -> dict[str, object]:
    """Return the Work Ledger vocabulary contract as a parsed dict."""
    raw = WORK_LEDGER_CONTRACT_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


def load_ledger_golden_events() -> dict[str, object]:
    """Return the golden ledger-events fixture as a parsed dict."""
    raw = LEDGER_GOLDEN_EVENTS_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


__all__ = [
    "LEDGER_PAYLOAD_VERSION",
    "LEDGER_EVENT_TYPES",
    "WORK_LEDGER_CONTRACT_PATH",
    "LEDGER_GOLDEN_EVENTS_PATH",
    "load_work_ledger_contract",
    "load_ledger_golden_events",
]
