"""Work Ledger contracts for Generative Surfaces v2 and v2.1.

The typed event vocabulary of SDR §5 as executable pydantic contracts: payload
models + value enums (``ledger_models``), the projection entity twins
(``entities``), and the user-visible ledger-id codec (``ledger_ids``). All mirror
the single JSON source of truth in ``copilot_service_contracts.work_ledger`` and
the TypeScript types in ``packages/api-types``; cross-language parity tests pin
the three together.

This is a sibling of ``agent_runtime.capabilities.surfaces`` (the legacy
SurfaceSpec home), not a replacement. Existing v2 runtime code consumes these
contracts; the v2.1 A1 extension remains additive vocabulary only.
"""

from __future__ import annotations
