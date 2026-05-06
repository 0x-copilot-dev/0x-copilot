# Cluster: `agent_runtime.api` (domain services)

This is the **application/service layer** under `agent_runtime/api`, distinct from HTTP [`runtime_api`](../../../services/ai-backend/src/runtime_api/).

## Cluster boundary

- **Paths:** [`services/ai-backend/src/agent_runtime/api/`](../../../services/ai-backend/src/agent_runtime/api/).
- **Primary entrypoints:** [`service.py`](../../../services/ai-backend/src/agent_runtime/api/service.py), [`share_service.py`](../../../services/ai-backend/src/agent_runtime/api/share_service.py), [`draft_service.py`](../../../services/ai-backend/src/agent_runtime/api/draft_service.py), [`membership.py`](../../../services/ai-backend/src/agent_runtime/api/membership.py).

## Static signals

| Tool                          | Scope                   | Result (2026-05-06)                                                                                                                                                                                                            |
| ----------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Ruff `F401`, `F841`           | `src/agent_runtime/api` | No findings                                                                                                                                                                                                                    |
| Vulture `--min-confidence 80` | same                    | **100%:** [`share_service.py`](../../../services/ai-backend/src/agent_runtime/api/share_service.py) `_collect_sources` — parameter `sources_visible` flagged unused in the inner body (~623); see Likely dead — false positive |
| Vulture `--min-confidence 60` | same                    | Constants classes, presentation helpers, notification stubs                                                                                                                                                                    |

## Wiring-checked

- **`HttpWorkspaceMembershipResolver`** — Documented as the production implementation in [`membership.py`](../../../services/ai-backend/src/agent_runtime/api/membership.py), but **as of this audit** it appears **only in tests** (`tests/unit/runtime_api/test_approval_forwarding_hardening.py`). Default wiring in [`agent_runtime/api/service.py`](../../../services/ai-backend/src/agent_runtime/api/service.py) uses **`InMemoryWorkspaceMembershipResolver`** when callers omit `membership_resolver`. **Smell:** HTTP-backed membership may not be injected from [`runtime_api/app.py`](../../../services/ai-backend/src/runtime_api/app.py); confirm whether production approval forwarding is intended to trust in-memory behavior.

- **`notify_approval_resolved`** variants on notification adapters — may be optional integrations (Slack, etc.); “unused” may mean **feature disabled by config**.

## Test-only usage

- **`HttpWorkspaceMembershipResolver`** — imported/instantiated from tests only unless facade/backend wiring lives outside this grep snapshot.

## Likely dead / high-confidence candidates

1. **`sources_visible` on `_collect_sources`** — **False positive.** Vulture flags the parameter name on the inner method, but the flag is read by callers via `share.sources_visible_to_viewer` to gate redaction (see [`share_service.py`](../../../services/ai-backend/src/agent_runtime/api/share_service.py) ~line 468 `if not share.sources_visible_to_viewer:` and the call site at ~line 459). No remediation needed; rename the parameter to `_sources_visible` only if linter quiet is desired.

## Smells

- **`append_stream_events` / `flush_pending_enrichment`** on events helper — flagged 60%; confirm single code path for streaming enrichment to avoid duplicate logic.

## Cross-cluster links

- Called from [`runtime_api`](../../../services/ai-backend/src/runtime_api/) HTTP modules — [cluster-runtime-api.md](./cluster-runtime-api.md).

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **29** lines):

- [`artifacts/cluster-agent-runtime-domain-services-vulture.txt`](./artifacts/cluster-agent-runtime-domain-services-vulture.txt)

Merged output for all of `src/` (**639** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
