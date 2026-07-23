"""Work Ledger contracts (Generative Surfaces v2, SDR §3) — nothing wired until PRD-A3.

The typed event vocabulary of SDR §5 as executable pydantic contracts: payload
models + value enums (``ledger_models``), the projection entity twins
(``entities``), and the user-visible ledger-id codec (``ledger_ids``). All mirror
the single JSON source of truth in ``copilot_service_contracts.work_ledger`` and
the TypeScript types in ``packages/api-types``; cross-language parity tests pin
the three together.

This is a sibling of ``agent_runtime.capabilities.surfaces`` (the v1 SurfaceSpec
home), not a replacement — v1 is untouched. Everything here is dead code at
runtime by design: no producer constructs these models and the ``SURFACES_V2``
flag is read by nothing until emission lands in PRD-A3.
"""

from __future__ import annotations
