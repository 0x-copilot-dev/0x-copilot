"""OIDC-specific persistence (A3): authentication state, identities, refresh tokens, JWKS cache.

Kept in its own module so the IdentityStore Protocol stays readable and the
SAML/SCIM tracks can drop their own ``saml_store.py`` / ``scim_store.py``
without merge conflicts in the foundation file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    OidcAuthenticationRecord,
    OidcIdentityRecord,
    OidcJwksCacheRecord,
    OidcRefreshTokenRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OidcStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Authentications --------------------------------------------------
    def create_authentication(
        self, record: OidcAuthenticationRecord, *, conn: Any | None = None
    ) -> OidcAuthenticationRecord: ...

    def consume_authentication(
        self, *, state: str, conn: Any | None = None
    ) -> OidcAuthenticationRecord | None:
        """Atomic compare-and-set: pops the row by state and marks consumed.

        Returns ``None`` when the state is unknown, expired, or already
        consumed (replay defense). Implementations MUST be atomic so two
        concurrent callbacks for the same state can never both succeed.
        """

    # Identities --------------------------------------------------------
    def create_identity(
        self, record: OidcIdentityRecord, *, conn: Any | None = None
    ) -> OidcIdentityRecord: ...

    def get_identity_by_subject(
        self, *, provider_id: str, subject: str
    ) -> OidcIdentityRecord | None: ...

    def list_identities_by_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[OidcIdentityRecord, ...]:
        """All non-unlinked OIDC identities linked to a user, oldest first.

        Account-linking (PRD FR-L4): the profile's "Linked accounts" list needs
        every provider identity a user holds, not just a by-subject lookup.
        """
        ...

    def update_identity_claims(
        self,
        *,
        identity_id: str,
        claims_snapshot: dict[str, Any],
        email_at_link: str | None,
        conn: Any | None = None,
    ) -> bool: ...

    # Refresh tokens ----------------------------------------------------
    def revoke_active_refresh_tokens(
        self,
        *,
        org_id: str,
        user_id: str,
        provider_id: str,
        conn: Any | None = None,
    ) -> int: ...

    def store_refresh_token(
        self, record: OidcRefreshTokenRecord, *, conn: Any | None = None
    ) -> OidcRefreshTokenRecord: ...

    # JWKS --------------------------------------------------------------
    def get_jwks_cache(self, *, provider_id: str) -> OidcJwksCacheRecord | None: ...

    def upsert_jwks_cache(
        self, record: OidcJwksCacheRecord, *, conn: Any | None = None
    ) -> OidcJwksCacheRecord: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryOidcStore:
    authentications: dict[str, OidcAuthenticationRecord] = field(default_factory=dict)
    identities: dict[str, OidcIdentityRecord] = field(default_factory=dict)
    refresh_tokens: dict[str, OidcRefreshTokenRecord] = field(default_factory=dict)
    jwks_cache_by_provider: dict[str, OidcJwksCacheRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Authentications --------------------------------------------------
    def create_authentication(
        self, record: OidcAuthenticationRecord, *, conn: Any | None = None
    ) -> OidcAuthenticationRecord:
        del conn
        if any(
            row.state == record.state and row.consumed_at is None
            for row in self.authentications.values()
        ):
            raise ValueError(
                "active OIDC authentication with this state already exists"
            )
        self.authentications[record.auth_id] = record
        return record

    def consume_authentication(
        self, *, state: str, conn: Any | None = None
    ) -> OidcAuthenticationRecord | None:
        del conn
        for auth_id, row in list(self.authentications.items()):
            if row.state != state:
                continue
            if row.consumed_at is not None:
                # Replay attempt — refuse.
                return None
            if row.expires_at <= _now():
                return None
            consumed = row.model_copy(update={"consumed_at": _now()})
            self.authentications[auth_id] = consumed
            return consumed
        return None

    # Identities --------------------------------------------------------
    def create_identity(
        self, record: OidcIdentityRecord, *, conn: Any | None = None
    ) -> OidcIdentityRecord:
        del conn
        if any(
            ident.provider_id == record.provider_id
            and ident.subject == record.subject
            and ident.unlinked_at is None
            for ident in self.identities.values()
        ):
            raise ValueError("OIDC identity already linked for this provider/subject")
        self.identities[record.identity_id] = record
        return record

    def get_identity_by_subject(
        self, *, provider_id: str, subject: str
    ) -> OidcIdentityRecord | None:
        for ident in self.identities.values():
            if (
                ident.provider_id == provider_id
                and ident.subject == subject
                and ident.unlinked_at is None
            ):
                return ident
        return None

    def list_identities_by_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[OidcIdentityRecord, ...]:
        matches = [
            ident
            for ident in self.identities.values()
            if ident.org_id == org_id
            and ident.user_id == user_id
            and ident.unlinked_at is None
        ]
        return tuple(sorted(matches, key=lambda ident: ident.linked_at))

    def update_identity_claims(
        self,
        *,
        identity_id: str,
        claims_snapshot: dict[str, Any],
        email_at_link: str | None,
        conn: Any | None = None,
    ) -> bool:
        del conn
        existing = self.identities.get(identity_id)
        if existing is None or existing.unlinked_at is not None:
            return False
        update: dict[str, Any] = {"claims_snapshot": claims_snapshot}
        if email_at_link is not None:
            update["email_at_link"] = email_at_link
        self.identities[identity_id] = existing.model_copy(update=update)
        return True

    # Refresh tokens ----------------------------------------------------
    def revoke_active_refresh_tokens(
        self,
        *,
        org_id: str,
        user_id: str,
        provider_id: str,
        conn: Any | None = None,
    ) -> int:
        del conn
        count = 0
        for token_id, record in list(self.refresh_tokens.items()):
            if (
                record.org_id == org_id
                and record.user_id == user_id
                and record.provider_id == provider_id
                and record.revoked_at is None
            ):
                self.refresh_tokens[token_id] = record.model_copy(
                    update={"revoked_at": _now()}
                )
                count += 1
        return count

    def store_refresh_token(
        self, record: OidcRefreshTokenRecord, *, conn: Any | None = None
    ) -> OidcRefreshTokenRecord:
        del conn
        self.refresh_tokens[record.token_id] = record
        return record

    # JWKS --------------------------------------------------------------
    def get_jwks_cache(self, *, provider_id: str) -> OidcJwksCacheRecord | None:
        cached = self.jwks_cache_by_provider.get(provider_id)
        if cached is None or cached.expires_at <= _now():
            return None
        return cached

    def upsert_jwks_cache(
        self, record: OidcJwksCacheRecord, *, conn: Any | None = None
    ) -> OidcJwksCacheRecord:
        del conn
        self.jwks_cache_by_provider[record.provider_id] = record
        return record


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresOidcStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    # Authentications --------------------------------------------------
    def create_authentication(
        self, record: OidcAuthenticationRecord, *, conn: Any | None = None
    ) -> OidcAuthenticationRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO oidc_authentications (
                    auth_id, org_id, provider_id, state, nonce,
                    code_verifier, redirect_uri, return_to,
                    requested_at, expires_at, consumed_at, ip, user_agent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.auth_id,
                    record.org_id,
                    record.provider_id,
                    record.state,
                    record.nonce,
                    record.code_verifier,
                    record.redirect_uri,
                    record.return_to,
                    record.requested_at,
                    record.expires_at,
                    record.consumed_at,
                    record.ip,
                    record.user_agent,
                ),
            )
        return record

    def consume_authentication(
        self, *, state: str, conn: Any | None = None
    ) -> OidcAuthenticationRecord | None:
        # Atomic compare-and-set: UPDATE ... RETURNING populates consumed_at
        # only if the row is still pending and not expired.
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE oidc_authentications
                SET consumed_at = now()
                WHERE state = %s
                  AND consumed_at IS NULL
                  AND expires_at > now()
                RETURNING *
                """,
                (state,),
            )
            row = cur.fetchone()
        return _row_to_authentication(row) if row else None

    # Identities --------------------------------------------------------
    def create_identity(
        self, record: OidcIdentityRecord, *, conn: Any | None = None
    ) -> OidcIdentityRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO oidc_identities (
                    identity_id, org_id, user_id, provider_id, subject,
                    email_at_link, linked_at, unlinked_at, claims_snapshot
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.identity_id,
                    record.org_id,
                    record.user_id,
                    record.provider_id,
                    record.subject,
                    record.email_at_link,
                    record.linked_at,
                    record.unlinked_at,
                    json.dumps(record.claims_snapshot),
                ),
            )
        return record

    def get_identity_by_subject(
        self, *, provider_id: str, subject: str
    ) -> OidcIdentityRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM oidc_identities
                WHERE provider_id = %s AND subject = %s
                  AND unlinked_at IS NULL
                """,
                (provider_id, subject),
            )
            row = cur.fetchone()
        return _row_to_identity(row) if row else None

    def list_identities_by_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[OidcIdentityRecord, ...]:
        # oidc_identities has a user_id index (0006); a user holds at most a
        # handful of provider identities, so oldest-first in SQL is cheap.
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM oidc_identities
                WHERE org_id = %s AND user_id = %s
                  AND unlinked_at IS NULL
                ORDER BY linked_at ASC
                """,
                (org_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(_row_to_identity(row) for row in rows)

    def update_identity_claims(
        self,
        *,
        identity_id: str,
        claims_snapshot: dict[str, Any],
        email_at_link: str | None,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE oidc_identities
                SET claims_snapshot = %s,
                    email_at_link = COALESCE(%s, email_at_link)
                WHERE identity_id = %s AND unlinked_at IS NULL
                """,
                (json.dumps(claims_snapshot), email_at_link, identity_id),
            )
            return bool(cur.rowcount)

    # Refresh tokens ----------------------------------------------------
    def revoke_active_refresh_tokens(
        self,
        *,
        org_id: str,
        user_id: str,
        provider_id: str,
        conn: Any | None = None,
    ) -> int:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE oidc_refresh_tokens SET revoked_at = now()
                WHERE org_id = %s AND user_id = %s AND provider_id = %s
                  AND revoked_at IS NULL
                """,
                (org_id, user_id, provider_id),
            )
            return int(cur.rowcount or 0)

    def store_refresh_token(
        self, record: OidcRefreshTokenRecord, *, conn: Any | None = None
    ) -> OidcRefreshTokenRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO oidc_refresh_tokens (
                    token_id, org_id, user_id, provider_id,
                    encrypted_refresh_token, scope, expires_at,
                    created_at, revoked_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.token_id,
                    record.org_id,
                    record.user_id,
                    record.provider_id,
                    record.encrypted_refresh_token,
                    json.dumps(list(record.scope)),
                    record.expires_at,
                    record.created_at,
                    record.revoked_at,
                ),
            )
        return record

    # JWKS --------------------------------------------------------------
    def get_jwks_cache(self, *, provider_id: str) -> OidcJwksCacheRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM oidc_jwks_cache
                WHERE provider_id = %s AND expires_at > now()
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (provider_id,),
            )
            row = cur.fetchone()
        return _row_to_jwks(row) if row else None

    def upsert_jwks_cache(
        self, record: OidcJwksCacheRecord, *, conn: Any | None = None
    ) -> OidcJwksCacheRecord:
        # No native UPSERT key — replace strategy: delete previous row(s) for
        # the provider and insert the new one. The two statements are wrapped
        # in the caller's transaction or this method's implicit one.
        with self._cursor(conn) as cur:
            cur.execute(
                "DELETE FROM oidc_jwks_cache WHERE provider_id = %s",
                (record.provider_id,),
            )
            cur.execute(
                """
                INSERT INTO oidc_jwks_cache (
                    cache_id, provider_id, jwks, fetched_at, expires_at
                ) VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    record.cache_id,
                    record.provider_id,
                    json.dumps(record.jwks),
                    record.fetched_at,
                    record.expires_at,
                ),
            )
        return record


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _row_to_authentication(row: dict[str, Any]) -> OidcAuthenticationRecord:
    return OidcAuthenticationRecord.model_validate(row)


def _row_to_identity(row: dict[str, Any]) -> OidcIdentityRecord:
    return OidcIdentityRecord.model_validate(
        {**row, "claims_snapshot": _coerce_json(row.get("claims_snapshot"))}
    )


def _row_to_jwks(row: dict[str, Any]) -> OidcJwksCacheRecord:
    return OidcJwksCacheRecord.model_validate(
        {**row, "jwks": _coerce_json(row.get("jwks"))}
    )


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else {}
