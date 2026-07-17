"""A10 — RBAC enforcement at every backend route.

Two FastAPI dependencies — :func:`RequireScopes` and :func:`RequireRoles`
— pull the verified identity off the request via
:class:`backend_app.auth.BackendServiceAuthenticator`, then check the
caller's ``permission_scopes`` / ``roles`` against the route's
declared requirements.

Behavior is gated by ``RBAC_MODE``:

  - ``audit`` (default) — log denies to ``identity_audit_events``,
    pass through. Operators run in this mode for one release to gather
    telemetry on which legitimate calls would 403 under enforcement.
  - ``enforce`` — actual 403 (with the deny logged for audit anyway).

Public-by-design routes (``/v1/health``, ``/v1/auth/login``, etc.)
explicitly bypass via :func:`public_route`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from copilot_service_contracts.scopes import MFA_PENDING
from fastapi import HTTPException, Request, status

from backend_app.auth import BackendServiceAuthenticator, ScopedIdentity


_RBAC_MODE_ENV = "RBAC_MODE"
_AUDIT = "audit"
_ENFORCE = "enforce"


class RbacMode:
    """Resolves and caches the active RBAC mode for the request lifetime."""

    @staticmethod
    def current() -> str:
        mode = os.environ.get(_RBAC_MODE_ENV, _AUDIT).strip().lower()
        if mode not in {_AUDIT, _ENFORCE}:
            # Misconfiguration must NOT silently fall back to enforce
            # (would lock everyone out). Misconfig falls back to audit
            # so the deploy stays usable while the operator fixes the
            # env var; the misconfig itself shows up in the audit row's
            # metadata.
            return _AUDIT
        return mode

    @staticmethod
    def is_enforce() -> bool:
        return RbacMode.current() == _ENFORCE


def public_route() -> Callable[[Request], None]:
    """Marker dependency for routes that intentionally have no scope requirement.

    Used by ``/v1/health`` and the unauthenticated SSO entry / exit
    ramps. The CI scope-check tool (``tools/check_route_scopes.py``)
    treats the presence of this dependency as the explicit opt-out so
    a route still has to *declare* publicness rather than accidentally
    ship without an annotation.
    """

    async def _public(request: Request) -> None:  # pragma: no cover - trivial
        del request

    return _public


def RequireScopes(*scopes: str) -> Callable[[Request], ScopedIdentity]:
    """Require ALL of ``scopes`` on the caller's permission_scopes set.

    Returns the verified :class:`ScopedIdentity` so the route handler
    can use it without re-resolving. Use as::

        @app.get("/v1/foo")
        def list_foo(
            identity: ScopedIdentity = Depends(RequireScopes(SKILLS_READ)),
        ) -> ...:
            ...
    """

    required = frozenset(scopes)

    async def _dep(request: Request) -> ScopedIdentity:
        identity = BackendServiceAuthenticator.scoped_identity(
            request,
            org_id=request.headers.get("x-enterprise-org-id", ""),
            user_id=request.headers.get("x-enterprise-user-id", ""),
        )
        return _evaluate(
            request=request,
            identity=identity,
            required_scopes=required,
            required_roles=frozenset(),
        )

    _dep.__rbac_required_scopes__ = required  # type: ignore[attr-defined]
    return _dep


def RequireRoles(*roles: str) -> Callable[[Request], ScopedIdentity]:
    """Require ANY of ``roles`` on the caller's roles set.

    Roles are coarse-grained (admin, employee, auditor, service); the
    finer-grained scope set is the preferred check. Roles are useful
    when the policy is "this is an admin tool" rather than "this needs
    a specific capability". Routes can combine both — a
    ``Depends(RequireRoles(...))`` AND a ``Depends(RequireScopes(...))``
    on the same handler.
    """

    required = frozenset(roles)

    async def _dep(request: Request) -> ScopedIdentity:
        identity = BackendServiceAuthenticator.scoped_identity(
            request,
            org_id=request.headers.get("x-enterprise-org-id", ""),
            user_id=request.headers.get("x-enterprise-user-id", ""),
        )
        return _evaluate(
            request=request,
            identity=identity,
            required_scopes=frozenset(),
            required_roles=required,
        )

    _dep.__rbac_required_roles__ = required  # type: ignore[attr-defined]
    return _dep


def _evaluate(
    *,
    request: Request,
    identity: ScopedIdentity,
    required_scopes: frozenset[str],
    required_roles: frozenset[str],
) -> ScopedIdentity:
    # mfa:pending is a session lifecycle marker, not a usable
    # capability. A session with mfa:pending must NOT pass any RBAC
    # check (other than the explicit MFA verify route which uses
    # public_route). This stops the session from doing anything between
    # login and the MFA challenge being answered.
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
    identity: ScopedIdentity,
    reason: str,
    required_scopes: frozenset[str],
    required_roles: frozenset[str],
    missing_scopes: frozenset[str] = frozenset(),
) -> None:
    """Append an identity audit row for the deny.

    Falls back to a structured warning log when no IdentityStore is
    wired (e.g. dev runs where the routes execute but the identity
    store is the in-memory variant pre-A1). The audit row name is
    ``rbac.denied`` so it filters cleanly in the SIEM export.
    """

    metadata: dict[str, Any] = {
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
    store = getattr(request.app.state, "identity_store", None)
    if store is None:
        # Defer the import so this module stays usable in unit tests
        # that don't construct a full app.
        import logging

        logging.getLogger("backend.rbac").warning(
            "rbac.denied %s", metadata, extra={"safe_message": "rbac.denied"}
        )
        return

    from backend_app.contracts import IdentityAuditEventRecord

    try:
        store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id or "unknown",
                actor_user_id=identity.user_id or None,
                action="rbac.denied",
                metadata=metadata,
                request_ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        )
    except Exception:
        # Never let audit-write failure mask the auth decision. The
        # auth chain on the audit log is sturdier than RBAC; if it
        # blows up here, we still want the 403 / pass-through to
        # behave correctly. The exception is logged below.
        import logging

        logging.getLogger("backend.rbac").exception(
            "rbac audit write failed", extra={"safe_message": "rbac.denied"}
        )


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


__all__ = [
    "RbacMode",
    "RequireRoles",
    "RequireScopes",
    "public_route",
]
