# Cluster 06 — agent_runtime.api (services + ports)

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Runtime service layer, synchronous and async port definitions, notifications, share/fork helpers, and related constants under [`src/agent_runtime/api/`](../../src/agent_runtime/api/).

## Entrypoints / wiring

- [`runtime_api/app.py`](../../src/runtime_api/app.py) constructs services (conversation/run/share/draft/etc.) using composed adapters.
- Worker uses async ports via adapter wrappers ([`runtime_adapters/async_wrappers.py`](../../src/runtime_adapters/async_wrappers.py)).

## Likely unused or low-value symbols

| Location                             | Symbol / issue                                                  | Evidence                                                                                                                                                                      | Confidence | Action                                                                                                                                                              |
| ------------------------------------ | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `api/ports.py`, `api/async_ports.py` | Queue/worker method parameter names (`command`, `worker_id`, …) | Protocol-style definitions with `...` bodies → Vulture “unused”.                                                                                                              | Low        | Keep; whitelist / ignore.                                                                                                                                           |
| `api/async_ports.py`                 | Same pattern on budgeting/pricing method stubs                  | Vulture noise on abstract params.                                                                                                                                             | Low        | Same                                                                                                                                                                |
| `api/share_service.py`               | `_collect_sources(..., sources_visible: bool)`                  | Parameter unused in body; caller already passes `share.sources_visible_to_viewer` but applies redaction **after** `_collect_sources` returns (see `recipient_view` assembly). | **High**   | Remove redundant parameter (keep redaction at call site) **or** fold redaction inside `_collect_sources` if hiding collection entirely was intended when invisible. |
| `api/mcp_discovery_service.py`       | `McpAuthSessionCreator` import                                  | `TYPE_CHECKING` block — Vulture false positive.                                                                                                                               | Low        | No change                                                                                                                                                           |

### Share service note

`_collect_sources` ignores `sources_visible` because visibility is enforced via `_redact_source` on the caller path — the parameter is redundant API surface, not hidden behavior.

## Test-only vs production

Share/fork services may have tests that do not exercise workspace-feed failures (defensive `except` branches).

## Code smells

- **`ports.py` / `async_ports.py` size:** Hard to navigate; coupling between HTTP features and persistence leaks through wide port types.
- **Unused formal parameters** on concrete methods (not Protocols) are higher signal than scanner noise — prioritize `share_service` finding above.

## Follow-ups

- Track `sources_visible` resolution in the same PR as any FE contract change to avoid drift.

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 29 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### High-signal

| Item                                                                                    | Notes                                                                                                                                                                                                                                                                                          |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`api/constants.py`](../../../services/ai-backend/src/agent_runtime/api/constants.py)   | Dozens of min-50 hits on nested `Keys.*` fields — **noise** (same pattern as README supplement).                                                                                                                                                                                               |
| [`membership.py`](../../../services/ai-backend/src/agent_runtime/api/membership.py)     | `HttpWorkspaceMembershipResolver` flagged unused class — **production implementation exists** but [`runtime_api/app.py`](../../../services/ai-backend/src/runtime_api/app.py) comments imply tests wire resolvers explicitly; confirm whether HTTP resolver is ever chosen in non-dev deploys. |
| [`presentation.py`](../../../services/ai-backend/src/agent_runtime/api/presentation.py) | `presentation_for_event` reported unused — verify event projector path before removal.                                                                                                                                                                                                         |

### Noise

- `notifications.py` duplicate-style methods (`notify_approval_resolved`) — may be overload / protocol compatibility.
