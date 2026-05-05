# Cluster: `agent_runtime/api/`

**Total: 3,617 LOC across 8 files.** Service / presentation layer for the runtime API. Sits between the FastAPI HTTP routes ([`runtime_api/`](../../../services/ai-backend/src/runtime_api/)) and the persistence ports ([`runtime_adapters/`](../runtime-adapters/_index.md)). Owns the port protocols, the event-producer state machine (sequence numbering + presentation polish), the deterministic-template ladder for UI cards, and the usage-query service.

## Role in the request lifecycle

When an HTTP route receives a runtime command, it calls into [`api/service.py`](../../../services/ai-backend/src/agent_runtime/api/service.py) which orchestrates the persistence ports + queue producer. As the worker emits events, [`api/events.py`](../../../services/ai-backend/src/agent_runtime/api/events.py) produces the persisted envelope (sequence number, timestamps, presentation metadata). [`api/presentation.py`](../../../services/ai-backend/src/agent_runtime/api/presentation.py) runs the 4-step resolution chain (deterministic template → tool template → payload projector → minimal fallback), and [`api/presentation_templates.py`](../../../services/ai-backend/src/agent_runtime/api/presentation_templates.py) holds the deterministic templates per event kind. [`api/usage_service.py`](../../../services/ai-backend/src/agent_runtime/api/usage_service.py) answers `/v1/usage/*` queries from the daily rollup table.

## Files in this cluster

| File                                                                                                                                                                                       | LOC | Doc                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --: | ---------------------------------------------------------------------- |
| [`presentation.py`](../../../services/ai-backend/src/agent_runtime/api/presentation.py) — Card presentation metadata for user-facing runtime events with resolution chain.                 | 776 | [presentation.md](presentation.md) (standalone, L)                     |
| [`service.py`](../../../services/ai-backend/src/agent_runtime/api/service.py) — Thin application service for the FastAPI runtime API.                                                      | 743 | [service.md](service.md) (standalone, L)                               |
| [`presentation_templates.py`](../../../services/ai-backend/src/agent_runtime/api/presentation_templates.py) — Deterministic presentation templates that render UI cards without LLM calls. | 646 | [presentation-templates.md](presentation-templates.md) (standalone, L) |
| [`events.py`](../../../services/ai-backend/src/agent_runtime/api/events.py) — Runtime event producer helpers for preliminary presentation and LLM polish.                                  | 472 | [events.md](events.md) (standalone — promoted, projection rules)       |
| [`async_ports.py`](../../../services/ai-backend/src/agent_runtime/api/async_ports.py) — Async port protocols mirroring sync ports for persistence and queueing.                            | 337 | [api-bundle.md](api-bundle.md)                                         |
| [`usage_service.py`](../../../services/ai-backend/src/agent_runtime/api/usage_service.py) — Usage query service for period parsing and rollup arithmetic.                                  | 255 | [api-bundle.md](api-bundle.md)                                         |
| [`constants.py`](../../../services/ai-backend/src/agent_runtime/api/constants.py) — Constants and public messages for the FastAPI runtime API.                                             | 212 | [api-bundle.md](api-bundle.md)                                         |
| [`ports.py`](../../../services/ai-backend/src/agent_runtime/api/ports.py) — Port protocols for runtime API persistence, event replay, and queueing.                                        | 173 | [api-bundle.md](api-bundle.md)                                         |

## Doc layout

- [presentation.md](presentation.md) — `presentation.py` (L, 776)
- [service.md](service.md) — `service.py` (L, 743)
- [presentation-templates.md](presentation-templates.md) — `presentation_templates.py` (L, 646)
- [events.md](events.md) — `events.py` (M, 472, promoted)
- [api-bundle.md](api-bundle.md) — `constants.py`, `ports.py`, `async_ports.py`, `usage_service.py`

## Cross-cluster dependencies

**Imports from:**

- [`agent_runtime/persistence/`](../persistence/_index.md) — record types
- [`agent_runtime/execution/contracts.py`](../execution/contracts.md) — RuntimeEventEnvelope
- `service-contracts` (constants-only)
- Pydantic v2

**Imported by:**

- `services/ai-backend/src/runtime_api/` — FastAPI route layer
- [`runtime_worker/`](../runtime-worker/_index.md) — event production helpers
- [`runtime_adapters/`](../runtime-adapters/_index.md) — port implementations

## Use-case relevance

- [02-simple-greeting-no-tools.md](../../use-cases/02-simple-greeting-no-tools.md) — `events.py` model_delta projection.
- [03-tool-call-with-approval.md](../../use-cases/03-tool-call-with-approval.md), [04-ask-a-question-single.md](../../use-cases/04-ask-a-question-single.md) — `presentation_templates.py` approval card + `presentation.py` resolution chain.
- [12-stream-disconnect-and-resume.md](../../use-cases/12-stream-disconnect-and-resume.md) — `service.py` event-replay endpoint.
