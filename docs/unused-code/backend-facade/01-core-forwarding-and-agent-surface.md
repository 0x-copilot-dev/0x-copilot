# Cluster 01 — Core forwarding and agent surface

## Modules in scope

- [`services/backend-facade/src/backend_facade/app.py`](../../../services/backend-facade/src/backend_facade/app.py) (primary)

Related code documented elsewhere:

- Browser OTLP passthrough: `POST /v1/telemetry/otlp/v1/traces` in this file — full narrative in [05-observability.md](./05-observability.md).

## Internal subsections (for reading long diffs)

1. **Bootstrap** — `create_app`, module-level `app = create_app()`, middleware and OTEL hooks.
2. **Health / session / telemetry** — `GET /v1/health`, `GET /v1/session`, OTLP route.
3. **Dev IdP** — `GET /v1/dev/personas`, `POST /v1/dev/identity/mint` when `_dev_idp_enabled()`.
4. **MCP + OAuth** — `/v1/mcp/*`, callback.
5. **Agent / conversations / shares / drafts / workspace defaults / retention / export** — `/v1/agent/*`, `/v1/retention/effective`.
6. **Skills** — CRUD plus merged list handler.
7. **Runs / stream / cancel / approvals / history** — run lifecycle.
8. **Usage and budgets** — `/v1/usage/*`, `/v1/budgets/*`.
9. **Forwarding helpers** — `forward_json`, `_forward_json`, `_proxy_dev`, `_outbound_headers`, `_coerce_skill_list`, `_upstream_error_detail`, `settings_for`.

## Unused / unexercised (confidence)

| Item                                                   | Confidence         | Notes                                                                                                                                                                        |
| ------------------------------------------------------ | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create_app(..., configure_logging_on_create=False)`   | **Low**            | Keyword exists; no test or caller passes `False` (grep across `services/backend-facade`). API surface for tests/embeddings that want to avoid double logging configuration.  |
| `create_app(..., configure_telemetry_on_create=False)` | **Low**            | Same as above.                                                                                                                                                               |
| `create_app(..., deployment=...)`                      | **Low**            | Optional injection of `DeploymentProfile`; **never passed** in repo tests. Default path always uses `resolve_or_exit()`. Useful for focused tests but currently unexercised. |
| `register_health_routes(..., readiness_checkers=...)`  | **High** (unwired) | See [06-bootstrap-settings-deployment-health.md](./06-bootstrap-settings-deployment-health.md). Call site uses defaults only.                                                |

No other module-level helpers in `app.py` appeared **unreferenced** from `src/` + `tests/` at audit time (`forward_json`, `_forward_json`, `_proxy_dev`, `_coerce_skill_list`, `_upstream_error_detail`, `settings_for`, `_dev_idp_enabled`, `_outbound_headers` are all used).

### Pydantic models (`FacadeConversationRequest`, `FacadeRunRequest`) and static “unused field” reports

Vulture (and similar tools) flag almost every field on these models as an **unused variable**. Fields are consumed by **Pydantic** and `model_dump(exclude_none=True)` when forwarding to ai-backend — they are **not** dead. See [07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md), category A.

## Smells

1. **God module** — The majority of product routes live in one file (~1.3k lines). Harder to review, blame, and partition in code review; not “unused” but a maintainability hotspot.

2. **`forward_json` return typing vs. one route** — `usage_me_conversations` returns `list[dict[str, object]]` but calls `forward_json` which is annotated as `dict[str, object]`, requiring `# type: ignore[return-value]`. Smell: forwarder typing does not match all upstream JSON shapes (spec says object-only for “expected JSON” in some places; this route is an exception).

3. **Intentional internal upstream call** — `GET /v1/skills` merges backend `GET /v1/skills` with ai-backend `GET /internal/v1/skills/system`. [`ARCHITECTURE.md`](../../../services/backend-facade/ARCHITECTURE.md) states apps must not use `/internal/v1/*`; the **facade** is allowed to call it as a trusted forwarder. [`product-api-surface.md`](../../../services/backend-facade/docs/specs/product-api-surface.md) lists `GET /v1/skills` as backend-owned only — **spec drift** vs implementation (should mention aggregation + system skills source).

4. **Spec drift (surface doc incomplete)** — [`product-api-surface.md`](../../../services/backend-facade/docs/specs/product-api-surface.md) route table covers a **subset** of paths actually registered in `app.py`. Examples missing from the spec table at audit time include (non-exhaustive): `GET /v1/health`, `GET /v1/session`, `POST /v1/telemetry/otlp/v1/traces`, dev proxies, list conversations, conversation context/connectors/lifecycle/restore, share and fork endpoints, drafts, subagents/sources/models, retention/effective, workspace export/delete-all, usage and budgets families. Keeping the spec current reduces integration surprises.

5. **Streaming client lifecycle** — `stream_run` creates an `httpx.AsyncClient(timeout=None)` and relies on `finally` in the generator to close; correct but easy to get wrong on future edits (document-only note).

## Recommended follow-ups (optional)

- Extend `product-api-surface.md` to list every app-facing path (or link to OpenAPI export) and correct the `GET /v1/skills` owner column.
- Narrow `forward_json` typing (overload or generic) or normalize usage responses to a wrapper object if the product contract allows it.
- Split `app.py` by route family into `routes/*.py` with `register_*` functions when the team is ready for a larger refactor (not required for unused-code cleanup alone).
