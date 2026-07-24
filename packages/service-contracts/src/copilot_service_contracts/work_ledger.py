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


class _ContractResource:
    """Where the JSON siblings live inside the installed package."""

    PACKAGE: str = "copilot_service_contracts"
    CONTRACT_FILENAME: str = "work_ledger.json"
    GOLDEN_EVENTS_FILENAME: str = "work_ledger_golden_events.json"
    # PRD-E1: the expected run receipt for the golden events above. It is
    # ``ReceiptFold.fold_raw`` applied to those events — regenerated from the
    # fold, never hand-authored — and lives here (beside the golden events, NOT
    # in a py-only test dir) so BOTH the ai-backend py fold and the chat-surface
    # ts fold consume the same referee.
    EXPECTED_RECEIPT_FILENAME: str = "work_ledger_expected_receipt.json"
    # PRD-A1 v2.1: deterministic, independent journeys that exercise the
    # operation/artifact/effect vocabulary without requiring a runtime.
    GOLDEN_JOURNEYS_FILENAME: str = "work_ledger_v2_1_golden_journeys.json"
    # Cross-language canonical-JSON, digest, identifier, and reference vectors.
    CONTRACT_VECTORS_FILENAME: str = "work_ledger_v2_1_vectors.json"


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

# Traversable handle to the expected-receipt fixture (E1). Loadable py-side via
# ``load_ledger_expected_receipt``; the ts parity test imports it by relative
# path in its test file only (the ``adapter_allowlist`` precedent).
LEDGER_EXPECTED_RECEIPT_PATH: Traversable = files(_ContractResource.PACKAGE).joinpath(
    _ContractResource.EXPECTED_RECEIPT_FILENAME
)

LEDGER_GOLDEN_JOURNEYS_PATH: Traversable = files(_ContractResource.PACKAGE).joinpath(
    _ContractResource.GOLDEN_JOURNEYS_FILENAME
)

LEDGER_CONTRACT_VECTORS_PATH: Traversable = files(_ContractResource.PACKAGE).joinpath(
    _ContractResource.CONTRACT_VECTORS_FILENAME
)


def load_work_ledger_contract() -> dict[str, object]:
    """Return the Work Ledger vocabulary contract as a parsed dict."""
    raw = WORK_LEDGER_CONTRACT_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


def load_ledger_golden_events() -> dict[str, object]:
    """Return the golden ledger-events fixture as a parsed dict."""
    raw = LEDGER_GOLDEN_EVENTS_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


def load_ledger_expected_receipt() -> dict[str, object]:
    """Return the expected run-receipt fixture (E1) as a parsed dict."""
    raw = LEDGER_EXPECTED_RECEIPT_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


def load_ledger_golden_journeys() -> dict[str, object]:
    """Return the deterministic v2.1 operation/artifact/effect journeys."""
    raw = LEDGER_GOLDEN_JOURNEYS_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


def load_ledger_contract_vectors() -> dict[str, object]:
    """Return shared canonicalization, digest, identifier, and ref vectors."""
    raw = LEDGER_CONTRACT_VECTORS_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


# Read the JSON once at import, then expose stable immutable values. This keeps
# the contract file as the sole source of event values while avoiding dynamic
# JSON parsing on request paths.
_WORK_LEDGER_CONTRACT = load_work_ledger_contract()
_EVENTS = _WORK_LEDGER_CONTRACT.get("events")
if not isinstance(_EVENTS, dict):  # pragma: no cover - package corruption
    raise RuntimeError("work_ledger.json must define an object-valued 'events' key")
LEDGER_EVENT_TYPES: tuple[str, ...] = tuple(str(key) for key in _EVENTS)


__all__ = [
    "LEDGER_PAYLOAD_VERSION",
    "LEDGER_EVENT_TYPES",
    "WORK_LEDGER_CONTRACT_PATH",
    "LEDGER_GOLDEN_EVENTS_PATH",
    "LEDGER_EXPECTED_RECEIPT_PATH",
    "LEDGER_GOLDEN_JOURNEYS_PATH",
    "LEDGER_CONTRACT_VECTORS_PATH",
    "load_work_ledger_contract",
    "load_ledger_golden_events",
    "load_ledger_expected_receipt",
    "load_ledger_golden_journeys",
    "load_ledger_contract_vectors",
]
