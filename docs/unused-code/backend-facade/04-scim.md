# Cluster 04 — SCIM

## Modules in scope

- [`services/backend-facade/src/backend_facade/scim_routes.py`](../../../services/backend-facade/src/backend_facade/scim_routes.py)

## Unused / unexercised (confidence)

| Item                                                               | Confidence      | Notes                                                  |
| ------------------------------------------------------------------ | --------------- | ------------------------------------------------------ |
| `register_scim_routes`                                             | **High** (used) | Called from `create_app`.                              |
| `_proxy`, `_bearer_from_request`, `_service_token`, `settings_for` | **High** (used) | Internal wiring for SCIM handlers.                     |
| `__all__` export                                                   | **High**        | Documents public surface; tests import module by name. |

No dead code identified in this small module at audit time.

## Smells

1. **Duplicate bearer parsing** — `_bearer_from_request` duplicates the pattern in [`auth_routes.py`](../../../services/backend-facade/src/backend_facade/auth_routes.py) (different header semantics vs SCIM token, but structurally similar). Optional consolidation.

2. **Placeholder identity headers** — `_proxy` sends `x-enterprise-org-id` / `x-enterprise-user-id` as `"-"` with a service token and SCIM bearer in `x-scim-bearer-token`. This is **by design** (documented in module docstring: backend re-validates). Flag for security reviewers so it is not mistaken for a bug.

3. **SCIM not in `product-api-surface.md`** — The product API spec under `services/backend-facade/docs/specs/` focuses on `/v1/*`. SCIM lives at `/scim/v2/*`. If the spec is meant to be exhaustive for “everything browsers and IdPs hit,” add a SCIM subsection or a pointer doc.

## Recommended follow-ups (optional)

- Add a one-line pointer in `product-api-surface.md` to SCIM paths and upstream owner (`services/backend` internal SCIM resource routes).
