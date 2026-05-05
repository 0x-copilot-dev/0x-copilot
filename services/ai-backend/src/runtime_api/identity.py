"""W0.1 — single FastAPI identity dependency for runtime API routes.

Every route that needs ``org_id`` / ``user_id`` declares::

    from runtime_api.identity import Identity

    @router.get("/something")
    async def handler(identity: Identity, ...): ...

The dependency is non-optional — a missing or invalid identity raises 401
before the handler runs. This collapses the two pre-W0.1 helpers
(``RuntimeApiRoutes.scoped_identity`` query-param fallback for legacy
routes; ``RuntimeServiceAuthenticator.trusted_identity_from_request``
header-only path for the new workspace / drafts routes) into one path.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from runtime_api.auth import RuntimeServiceAuthenticator, TrustedRequestIdentity


RuntimeIdentity = TrustedRequestIdentity
"""Alias for the identity passed to handler functions."""


async def get_identity(request: Request) -> RuntimeIdentity:
    """FastAPI dependency that resolves to the request's identity or 401."""

    return RuntimeServiceAuthenticator.require_identity(request)


Identity = Annotated[RuntimeIdentity, Depends(get_identity)]
"""``Annotated`` dependency alias used as a route parameter type hint."""
