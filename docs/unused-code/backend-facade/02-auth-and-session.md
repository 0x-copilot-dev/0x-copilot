# Cluster 02 — Auth and session

## Modules in scope

- [`services/backend-facade/src/backend_facade/auth.py`](../../../services/backend-facade/src/backend_facade/auth.py)
- [`services/backend-facade/src/backend_facade/auth_routes.py`](../../../services/backend-facade/src/backend_facade/auth_routes.py)

## Unused / unexercised (confidence)

| Item                                                                                                              | Confidence      | Notes                                                                                                                                                                                                    |
| ----------------------------------------------------------------------------------------------------------------- | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Module-level helpers in `auth.py` (`_has_sid_claim`, `_bearer_from_authorization_header`, `_parse_iso_timestamp`) | **High** (used) | Referenced from `FacadeAuthenticator` and related paths; not dead.                                                                                                                                       |
| `SessionRevoked`, `StepUpRequired`, `requires_recent_mfa`                                                         | **High** (used) | Used from `auth_routes` and [`tests/test_session_binding.py`](../../../services/backend-facade/tests/test_session_binding.py).                                                                           |
| `FacadeAuthenticator` public methods                                                                              | **High** (used) | `authenticate_request`, `verify_with_touch`, `service_headers`, `verify_identity_token`, touch cache helpers, etc., referenced from `app.py`, `auth_routes`, `me_routes`, `workspace_routes`, and tests. |

No unused top-level symbols were identified in `auth.py` or `auth_routes.py` from cross-reference grep at audit time.

### Vulture-only false positives in `auth.py`

[`_TouchCache`](../../../services/backend-facade/src/backend_facade/auth.py) exposes `hits` / `misses` counters. Vulture on **src only** marks them unused; [`tests/test_session_binding.py`](../../../services/backend-facade/tests/test_session_binding.py) asserts on them. `FacadeAuthenticator.touch_cache()` is similarly flagged at 60% when decorators/tests are excluded — it is used from tests and from cache mutation paths. See [07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md) (category C).

## Smells

1. **Duplicated `settings_for`** — [`auth_routes.py`](../../../services/backend-facade/src/backend_facade/auth_routes.py) defines a local `settings_for(app) -> FacadeSettings` mirroring [`app.py`](../../../services/backend-facade/src/backend_facade/app.py) to avoid import cycles (per inline comment). Same pattern in `me_routes`, `scim_routes`, `workspace_routes`. Not dead code; **DRY / drift risk** if `app.state.settings` semantics ever diverge.

2. **Parallel bearer extraction** — `auth_routes` uses `_bearer_from_request`; `scim_routes` defines its own `_bearer_from_request` with the same name and role. Different modules, same helper shape — consolidation would be a small hygiene win (optional).

3. **Size and responsibility of `auth_routes`** — SAML/OIDC/session/token flows in one module is appropriate for the facade, but it increases review surface; pair with tests (already present: `test_saml_facade.py`, `test_session_binding.py`, etc.).

## Recommended follow-ups (optional)

- Centralize `settings_for` in a tiny `backend_facade.app_state` (or similar) module imported only where safe, if cycle constraints can be satisfied.
- Extract shared `_bearer_from_request` to a small internal util used by `auth_routes` and `scim_routes` if duplication grows.
