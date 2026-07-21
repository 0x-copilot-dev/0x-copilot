"""Principal helpers (principal/tenant separation, ADR 0001).

The single home for the ``prn_<user_id>`` convention so the identity store,
the auth-identity edge stores (wallet / OIDC / SAML), and the migration
backfills all agree on one deterministic id. See docs/adr/0001-principal-tenant-separation.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend_app.contracts import (
        OidcIdentityRecord,
        SamlIdentityRecord,
        WalletIdentityRecord,
    )

    IdentityEdgeRecord = WalletIdentityRecord | OidcIdentityRecord | SamlIdentityRecord


def default_principal_id(user_id: str) -> str:
    """The 1:1 expand-stage principal id for a user (ADR 0001).

    Deterministic ``prn_<user_id>`` so an app-created row and the migration
    backfill (which copies ``users.principal_id``) resolve to the same
    principal — the dual-writes are therefore idempotent across the migration
    boundary. In the current single-org-per-user model every identity for a
    user shares that user's principal, so an auth-identity edge can derive its
    principal from ``user_id`` without a cross-store lookup.
    """
    return f"prn_{user_id}"


def with_default_principal(record: IdentityEdgeRecord) -> IdentityEdgeRecord:
    """Dual-write an auth-identity edge's ``principal_id`` (ADR 0001, stage 2a).

    Fill it from the owning user when unset; return the record unchanged when a
    caller already supplied one (the future explicit-link path). Idempotent.
    The FK always resolves because the user — and thus its principal — is
    created before any identity edge that points at it.
    """
    if record.principal_id is None:
        return record.model_copy(
            update={"principal_id": default_principal_id(record.user_id)}
        )
    return record
