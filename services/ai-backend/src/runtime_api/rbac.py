"""RBAC enforcement for every ai-backend route.

Mirrors the RBAC contract from the backend service (no cross-import — service boundary).

  - :func:`RequireScopes(*scopes)` — ALL of ``scopes`` must be present.
  - :func:`RequireAnyScope(*scopes)` — ANY of ``scopes`` must be present.
  - :func:`RequireRoles(*roles)` — ANY of ``roles`` must be present.
  - :func:`public_route()` — explicit opt-out marker for CI scope checks.
  - ``RBAC_MODE=audit`` (default) → log denies, pass through;
    ``RBAC_MODE=enforce`` → 403.

Denies are written to the runtime audit log when the store is wired; otherwise
they fall back to a structured warning log.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from enterprise_service_contracts.scopes import MFA_PENDING
from fastapi import HTTPException, Request, status

from runtime_api.auth import (
    RuntimeServiceAuthenticator,
    TrustedRequestIdentity,
)


_LOGGER = logging.getLogger("ai_backend.rbac")
_RBAC_MODE_ENV = "RBAC_MODE"
_AUDIT = "audit"
_ENFORCE = "enforce"


class RbacMode:
    """Reads and exposes the current RBAC enforcement mode from the environment."""

    @staticmethod
    def current() -> str:
        """Return the normalized RBAC mode, defaulting to ``audit`` for unknown values."""
        mode = os.environ.get(_RBAC_MODE_ENV, _AUDIT).strip().lower()
        if mode not in {_AUDIT, _ENFORCE}:
            return _AUDIT
        return mode

    @staticmethod
    def is_enforce() -> bool:
        """Return True iff the current mode is ``enforce``."""
        return RbacMode.current() == _ENFORCE


def public_route() -> Callable[[Request], None]:
    """Marker dependency for routes that intentionally have no scope requirement."""

    async def _public(request: Request) -> None:  # pragma: no cover - trivial
        del request

    return _public


def RequireScopes(*scopes: str) -> Callable[[Request], TrustedRequestIdentity]:
    """Return a FastAPI dependency requiring ALL listed scopes on the caller identity."""
    required = frozenset(scopes)

    async def _dep(request: Request) -> TrustedRequestIdentity:
        identity = _resolve_identity(request)
        return _evaluate(
            request=request,
            identity=identity,
            required_scopes=required,
            required_roles=frozenset(),
        )

    _dep.__rbac_required_scopes__ = required  # type: ignore[attr-defined]
    return _dep


def RequireAnyScope(*scopes: str) -> Callable[[Request], TrustedRequestIdentity]:
    """Require ANY of ``scopes`` on the caller's permission_scopes set.

    Useful when a route is reachable by multiple disjoint roles — e.g.
    ``/v1/usage/org`` accepts ``audit:read`` (the auditor role) OR
    ``admin:users`` (the admin role). The default :func:`RequireScopes`
    requires ALL listed scopes, which would force a single role to
    carry both.
    """

    required = frozenset(scopes)

    async def _dep(request: Request) -> TrustedRequestIdentity:
        identity = _resolve_identity(request)
        # An empty intersection means the caller has none of the required
        # scopes — render that as "all required, all missing" so the
        # _evaluate audit row reads consistently with RequireScopes.
        present = required & frozenset(identity.permission_scopes)
        if present:
            return _evaluate(
                request=request,
                identity=identity,
                required_scopes=frozenset(),
                required_roles=frozenset(),
            )
        return _evaluate(
            request=request,
            identity=identity,
            required_scopes=required,
            required_roles=frozenset(),
        )

    _dep.__rbac_required_any_scopes__ = required  # type: ignore[attr-defined]
    return _dep


def RequireRoles(*roles: str) -> Callable[[Request], TrustedRequestIdentity]:
    """Return a FastAPI dependency requiring ANY of the listed roles on the caller."""
    required = frozenset(roles)

    async def _dep(request: Request) -> TrustedRequestIdentity:
        identity = _resolve_identity(request)
        return _evaluate(
            request=request,
            identity=identity,
            required_scopes=frozenset(),
            required_roles=required,
        )

    _dep.__rbac_required_roles__ = required  # type: ignore[attr-defined]
    return _dep


def _resolve_identity(request: Request) -> TrustedRequestIdentity:
    """Resolve the caller's identity, synthesizing an empty-permissions identity in dev when absent."""
    identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
    if identity is None:
        # Dev-only path (no service token configured + not production).
        # Synthesize an empty-permissions identity so RBAC can still
        # decide based on what's missing.
        return TrustedRequestIdentity(
            org_id=request.headers.get("x-enterprise-org-id", "anonymous"),
            user_id=request.headers.get("x-enterprise-user-id", "anonymous"),
            roles=("anonymous",),
            permission_scopes=(),
            connector_scopes=None,
        )
    return identity


def _evaluate(
    *,
    request: Request,
    identity: TrustedRequestIdentity,
    required_scopes: frozenset[str],
    required_roles: frozenset[str],
) -> TrustedRequestIdentity:
    """Enforce scopes/roles, log any denial, and raise 403 in enforce mode."""
    if MFA_PENDING in identity.permission_scopes:
        _record_deny(
            request=request,
            identity=identity,
            reason="mfa_pending",
            required_scopes=required_scopes,
            required_roles=required_roles,
        )
        if RbacMode.is_enforce():
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Session requires MFA verification before this resource.",
            )
        return identity

    missing_scopes = required_scopes - frozenset(identity.permission_scopes)
    role_match = not required_roles or bool(required_roles & frozenset(identity.roles))
    if not missing_scopes and role_match:
        return identity

    _record_deny(
        request=request,
        identity=identity,
        reason="rbac_denied",
        required_scopes=required_scopes,
        required_roles=required_roles,
        missing_scopes=missing_scopes,
    )
    if RbacMode.is_enforce():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Caller is missing the required RBAC scopes/roles for this route.",
        )
    return identity


def _record_deny(
    *,
    request: Request,
    identity: TrustedRequestIdentity,
    reason: str,
    required_scopes: frozenset[str],
    required_roles: frozenset[str],
    missing_scopes: frozenset[str] = frozenset(),
) -> None:
    """Emit a structured warning log and best-effort audit row for a denied request."""
    metadata = {
        "reason": reason,
        "required_scopes": sorted(required_scopes),
        "required_roles": sorted(required_roles),
        "missing_scopes": sorted(missing_scopes),
        "caller_scopes": sorted(identity.permission_scopes),
        "caller_roles": sorted(identity.roles),
        "route": request.url.path,
        "method": request.method,
        "rbac_mode": RbacMode.current(),
    }
    _LOGGER.warning(
        "rbac.denied", extra={"safe_message": "rbac.denied", "metadata": metadata}
    )
    # Persistent audit row best-effort: the ai-backend store has its
    # own runtime_audit_log; route the deny through if the runtime API
    # service exposes its append helper. Falling back silently is OK
    # because the structured log above is the load-bearing record;
    # the chained audit log is a defense-in-depth bonus when wired.
    store = getattr(request.app.state, "runtime_audit_appender", None)
    if store is None:
        return
    try:
        store(
            org_id=identity.org_id or "unknown",
            event_type="rbac.denied",
            data={
                "user_id": identity.user_id,
                "actor_type": "session",
                "resource_type": "rbac",
                "resource_id": request.url.path,
                "outcome": "denied",
                "metadata": metadata,
            },
        )
    except Exception:
        _LOGGER.exception(
            "rbac audit-log append failed",
            extra={"safe_message": "rbac.denied"},
        )


__all__ = [
    "RbacMode",
    "RequireAnyScope",
    "RequireRoles",
    "RequireScopes",
    "public_route",
]
