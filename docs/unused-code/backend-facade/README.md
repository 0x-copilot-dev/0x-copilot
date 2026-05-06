# Backend facade: unused code and smells audit

This folder documents a **static and structural** review of [`services/backend-facade`](../../../services/backend-facade). It is not a substitute for runtime traffic analysis or frontend call-graph proof.

## Cluster index

| Doc                                                                                        | Scope                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [01-core-forwarding-and-agent-surface.md](./01-core-forwarding-and-agent-surface.md)       | [`app.py`](../../../services/backend-facade/src/backend_facade/app.py): forwarding helpers, MCP, agent, skills merge, usage, budgets, dev proxies, health stub, OTLP forward (cross-link to 05).                                                                                                  |
| [02-auth-and-session.md](./02-auth-and-session.md)                                         | [`auth.py`](../../../services/backend-facade/src/backend_facade/auth.py), [`auth_routes.py`](../../../services/backend-facade/src/backend_facade/auth_routes.py).                                                                                                                                 |
| [03-me-and-workspace-proxies.md](./03-me-and-workspace-proxies.md)                         | [`me_routes.py`](../../../services/backend-facade/src/backend_facade/me_routes.py), [`workspace_routes.py`](../../../services/backend-facade/src/backend_facade/workspace_routes.py).                                                                                                             |
| [04-scim.md](./04-scim.md)                                                                 | [`scim_routes.py`](../../../services/backend-facade/src/backend_facade/scim_routes.py).                                                                                                                                                                                                           |
| [05-observability.md](./05-observability.md)                                               | [`observability/`](../../../services/backend-facade/src/backend_facade/observability/); narrative home for browser OTLP passthrough (see 01 for code location).                                                                                                                                   |
| [06-bootstrap-settings-deployment-health.md](./06-bootstrap-settings-deployment-health.md) | [`settings.py`](../../../services/backend-facade/src/backend_facade/settings.py), [`deployment_profile.py`](../../../services/backend-facade/src/backend_facade/deployment_profile.py), [`routes/health.py`](../../../services/backend-facade/src/backend_facade/routes/health.py), package init. |
| [07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md)                       | Why Vulture reports **dozens of “unused”** route handlers and Pydantic fields, recommended flags, and the **short list** of real dead API.                                                                                                                                                        |
| [`vulture_whitelist.py`](./vulture_whitelist.py)                                           | Stub anchor for future whitelist entries (same idea as [`../ai-backend/vulture_whitelist.py`](../ai-backend/vulture_whitelist.py)); see 07 before growing it.                                                                                                                                     |

## Methodology

1. **Ruff** — `ruff check services/backend-facade/src services/backend-facade/tests` (ruff installed into the service `.venv` for this audit; default repo tooling may use pre-commit elsewhere). **Result:** all checks passed; no unused-import (`F401`) or assigned-but-unused (`F841`) findings reported for this tree at audit time.

2. **Vulture** — At `--min-confidence 60` on `src` alone, output is **very large** (~90+ lines) because nested FastAPI handlers and Pydantic fields look “unused” to static analysis. **This is expected noise.** Use `--ignore-decorators "@app.get,@app.post,@app.patch,@app.delete,@app.put,@app.api_route"` and read [07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md) for categorization. At `--min-confidence 100` without decorator ignores, `observability/otel.py` still reports `parent_context` / `timeout_millis` (protocol parameters — do not delete).

3. **Manual** — grep for module-level symbols vs. references in `src/` and `tests/`; compare [`product-api-surface.md`](../../../services/backend-facade/docs/specs/product-api-surface.md) to live routes for **spec drift** (documentation smell, not necessarily dead code).

## Limitations

- **FastAPI route handlers** are always “used” once registered; static tools rarely mark them dead. “Unused” here means **unused helpers**, **unwired optional APIs**, or **no test coverage** unless stated otherwise.
- **HTTP traffic** — proving no client calls a route requires `apps/frontend` (or other clients) analysis or production metrics; not done in this pass.
- **Dynamic registration** — routes under `_dev_idp_enabled()` exist only in some environments; they are not dead code.

## Clustering rationale (summary)

Clusters follow **physical modules** first, then **security blast radius** (auth isolated), then **product concern** (SCIM separate from workspace/me). The dominant file `app.py` remains one large cluster (01) with internal subsections in that doc. See the implementation plan for full tradeoff notes.
