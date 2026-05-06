# Cluster 03 — Me and workspace proxies

## Modules in scope

- [`services/backend-facade/src/backend_facade/me_routes.py`](../../../services/backend-facade/src/backend_facade/me_routes.py)
- [`services/backend-facade/src/backend_facade/workspace_routes.py`](../../../services/backend-facade/src/backend_facade/workspace_routes.py)

## Unused / unexercised (confidence)

| Item                                               | Confidence      | Notes                                                                                                                                                                     |
| -------------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `register_me_routes` / `register_workspace_routes` | **High** (used) | Wired from `create_app` in [`app.py`](../../../services/backend-facade/src/backend_facade/app.py).                                                                        |
| Local `settings_for` in each module                | **High** (used) | Same mirror pattern as auth cluster; see [02-auth-and-session.md](./02-auth-and-session.md).                                                                              |
| Inner route handlers                               | **High** (used) | Registered on the FastAPI app; exercised by [`tests/test_me_routes.py`](../../../services/backend-facade/tests/test_me_routes.py) and workspace-related tests if present. |

No dead module-level functions were found at audit time.

## Smells

1. **Backend internal paths on the public facade** — These modules intentionally proxy to `services/backend` paths under `/internal/v1/...`. That is consistent with “apps never call internal routes directly” but means the **facade** is the trusted bridge. Document in runbooks who may call which workspace routes; compliance reviewers often ask this explicitly.

2. **Duplicated forward patterns** — Both modules hand-build `httpx.AsyncClient`, call `verify_with_touch` where applicable, and map errors to `HTTPException`. Similar to `auth_routes` forwarding. A shared thin helper could reduce drift (optional).

3. **Timeout choices** — `me_routes` uses a 10s client timeout for the workspaces list; `workspace_routes` uses its own timeouts in `_forward`. Not unused; worth periodic alignment with backend SLAs.

4. **Duplicated `_raise_for_upstream`** — [`me_routes.py`](../../../services/backend-facade/src/backend_facade/me_routes.py) and [`workspace_routes.py`](../../../services/backend-facade/src/backend_facade/workspace_routes.py) each carry a private copy of the same helper as [`auth_routes.py`](../../../services/backend-facade/src/backend_facade/auth_routes.py) (me/workspace comment: avoid circular import). **Three** near-identical implementations if you count auth + me + workspace.

## Recommended follow-ups (optional)

- Add or extend tests that assert **org/user headers** on upstream calls for every workspace route (tenant isolation story); [`test_tenant_isolation_facade.py`](../../../services/backend-facade/tests/test_tenant_isolation_facade.py) focuses on `app.py` forwarding — workspace/me may deserve parallel cases if not already covered.
