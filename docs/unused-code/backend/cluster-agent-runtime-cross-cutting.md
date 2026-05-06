# Cluster: cross-cutting (`settings`, `validation`, package root)

## Cluster boundary

- **Paths:**
  - [`services/ai-backend/src/agent_runtime/settings.py`](../../../services/ai-backend/src/agent_runtime/settings.py)
  - [`services/ai-backend/src/agent_runtime/validation.py`](../../../services/ai-backend/src/agent_runtime/validation.py)
  - [`services/ai-backend/src/agent_runtime/__init__.py`](../../../services/ai-backend/src/agent_runtime/__init__.py)
- **Catch-all:** Any top-level `agent_runtime` modules not covered by other cluster docs (currently minimal).

## Static signals

| Tool                          | Scope                                                                       | Result (2026-05-06)                                                                                                                                                                                                                             |
| ----------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ruff `F401`, `F841`           | these files                                                                 | No findings                                                                                                                                                                                                                                     |
| Vulture `--min-confidence 60` | [`settings.py`](../../../services/ai-backend/src/agent_runtime/settings.py) | Unused-looking settings fields (`max_parallel_subagents`, `cache_ttl_seconds`, `default_timeout_seconds`, env enum `TEST`) — likely **Pydantic settings** populated from env and **read dynamically** via attribute access Vulture does not see |

## Wiring-checked

- **Settings fields** — grep `Settings(` / `get_settings` / attribute reads across `src/` before marking dead; settings objects often use indirect access.

## Test-only usage

- Validation helpers may be referenced primarily from tests — grep each export.

## Likely dead / high-confidence candidates

- None confirmed without env-driven settings analysis.

## Smells

- **Latent configuration** — unused settings keys can imply **unfinished features** or **deprecated env vars**; inventory periodically against `.env.example` / deployment docs.

## Cross-cluster links

- All clusters ultimately consume settings — treat changes here as **high blast radius**.

## Extended vulture inventory

Verbatim [Vulture](https://github.com/jendrikseipp/vulture) lines for this cluster’s paths (`vulture src --min-confidence 60` from `services/ai-backend`; **4** lines):

- [`artifacts/cluster-agent-runtime-cross-cutting-vulture.txt`](./artifacts/cluster-agent-runtime-cross-cutting-vulture.txt)

Merged output for all of `src/` (**634** lines): [`artifacts/vulture-min60-src-only.txt`](./artifacts/vulture-min60-src-only.txt).

These lists are **candidate** unused symbols — many entries are Pydantic validators, Protocol signatures, OTEL hooks, or FastAPI/RBAC decorators. Use as a triage queue, not an automatic delete list. Regenerate: [`README.md`](./README.md), [`artifacts/README.md`](./artifacts/README.md).
