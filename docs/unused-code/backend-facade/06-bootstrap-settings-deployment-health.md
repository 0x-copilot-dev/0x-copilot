# Cluster 06 — Bootstrap: settings, deployment profile, health, package surface

## Modules in scope

- [`services/backend-facade/src/backend_facade/settings.py`](../../../services/backend-facade/src/backend_facade/settings.py)
- [`services/backend-facade/src/backend_facade/deployment_profile.py`](../../../services/backend-facade/src/backend_facade/deployment_profile.py)
- [`services/backend-facade/src/backend_facade/routes/health.py`](../../../services/backend-facade/src/backend_facade/routes/health.py)
- [`services/backend-facade/src/backend_facade/__init__.py`](../../../services/backend-facade/src/backend_facade/__init__.py)
- [`services/backend-facade/src/backend_facade/routes/__init__.py`](../../../services/backend-facade/src/backend_facade/routes/__init__.py)

## Unused / unexercised (confidence)

| Item                                                                                         | Confidence      | Notes                                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `register_health_routes(..., readiness_checkers=None)` — actual **callers** passing checkers | **High**        | [`app.py`](../../../services/backend-facade/src/backend_facade/app.py) calls `register_health_routes(app)` with **no** `readiness_checkers`. The parameter supports upstream reachability probes but is **unwired** in production wiring — latent capability, not dead code inside `health.py`. |
| `Checker` type alias and `CheckResult`                                                       | **High** (used) | Used by `register_health_routes` signature and `readyz` implementation.                                                                                                                                                                                                                         |
| `FacadeSettings` fields                                                                      | **High** (used) | Loaded and read from `app.state` and mirror `settings_for` helpers.                                                                                                                                                                                                                             |
| `DeploymentProfileLoader`, `DeploymentProfile`, `resolve_or_exit`, `log_profile`             | **High** (used) | Startup path and tests (`test_deployment_profile.py`).                                                                                                                                                                                                                                          |
| `backend_facade/__init__.py`                                                                 | **N/A**         | Only package docstring; no re-exports. **Intentionally minimal** public package surface (callers import `backend_facade.app`, etc.).                                                                                                                                                            |
| `routes/__init__.py`                                                                         | **N/A**         | Empty file — common pattern to mark `routes` as a package; **no symbols** to be unused.                                                                                                                                                                                                         |

## Smells

1. **Readiness without checks** — `/readyz` always returns ready unless someone passes checkers. For a facade, optional upstream probes (backend / ai-backend) would align with the docstring on [`health.py`](../../../services/backend-facade/src/backend_facade/routes/health.py) (“Readiness checkers can probe upstream … if registered”).

2. **`resolve_or_exit` control flow** — After `fail_closed_at_boot` raises `SystemExit(78)`, a `raise` exists to satisfy the type checker (`deployment_profile.py`). Unreachable at runtime; minor clarity smell only.

3. **Deployment profile duplication across services** — Module comment notes the same pattern exists in other services per monorepo boundaries. Not unused; operational **consistency** burden when toggles change.

## Recommended follow-ups (optional)

- Register `readiness_checkers` from `create_app` (feature-flagged) with lightweight `httpx` HEAD/GET to `BACKEND_URL` and `AI_BACKEND_URL` health endpoints if those exist, or document a deliberate choice not to probe.
- If empty `routes/__init__.py` is noisy, add a one-line comment or re-export `register_health_routes` (style preference only).
