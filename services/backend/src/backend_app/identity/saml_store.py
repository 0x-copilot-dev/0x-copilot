"""SAML-specific persistence (A5): authn-request rows + ``(provider, name_id)`` linkings.

Mirrors :mod:`backend_app.identity.oidc_store` so the SAML service can
compose stores the same way OIDC does.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    SamlAuthenticationRecord,
    SamlAuthenticationStatus,
    SamlIdentityRecord,
)
from backend_app.identity.principals import with_default_principal


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SamlStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Authentications --------------------------------------------------
    def create_authentication(
        self, record: SamlAuthenticationRecord, *, conn: Any | None = None
    ) -> SamlAuthenticationRecord: ...

    def consume_authentication(
        self,
        *,
        provider_id: str,
        assertion_id: str,
        request_id: str | None,
        conn: Any | None = None,
    ) -> SamlAuthenticationRecord:
        """Atomic compare-and-set: marks an assertion consumed.

        Behavior:

        - For SP-initiated (``request_id`` provided): looks up the existing
          pending row matching ``(provider_id, request_id)``. The
          ``assertion_id`` is stamped on UPDATE so a replay of the same
          assertion against a different ``request_id`` still trips the unique
          index.
        - For IdP-initiated (``request_id is None``): inserts a fresh
          consumed row with ``request_id=NULL`` and ``status='consumed'``.

        Either way the implementation MUST raise ``SamlReplayDetected`` if
        the assertion id was already recorded. Raises
        ``SamlAuthenticationNotFound`` when SP-initiated lookup misses.
        """

    def reject_authentication(
        self,
        *,
        auth_id: str,
        conn: Any | None = None,
    ) -> bool: ...

    # Identities --------------------------------------------------------
    def create_identity(
        self, record: SamlIdentityRecord, *, conn: Any | None = None
    ) -> SamlIdentityRecord: ...

    def get_identity_by_name_id(
        self, *, provider_id: str, name_id: str
    ) -> SamlIdentityRecord | None: ...

    def update_identity_attributes(
        self,
        *,
        identity_id: str,
        attributes_snapshot: dict[str, Any],
        conn: Any | None = None,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SamlReplayDetected(RuntimeError):
    """A SAML assertion id was presented twice — refuse the second one."""


class SamlAuthenticationNotFound(RuntimeError):
    """SP-initiated consume couldn't find a pending row for the request_id."""


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemorySamlStore:
    authentications: dict[str, SamlAuthenticationRecord] = field(default_factory=dict)
    identities: dict[str, SamlIdentityRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Authentications --------------------------------------------------
    def create_authentication(
        self, record: SamlAuthenticationRecord, *, conn: Any | None = None
    ) -> SamlAuthenticationRecord:
        del conn
        self.authentications[record.auth_id] = record
        return record

    def consume_authentication(
        self,
        *,
        provider_id: str,
        assertion_id: str,
        request_id: str | None,
        conn: Any | None = None,
    ) -> SamlAuthenticationRecord:
        del conn
        # Replay defense — the unique index in Postgres trips here too.
        for row in self.authentications.values():
            if row.assertion_id == assertion_id and row.status != (
                SamlAuthenticationStatus.PENDING
            ):
                raise SamlReplayDetected(f"assertion {assertion_id!r} already consumed")

        if request_id is not None:
            for auth_id, row in self.authentications.items():
                if (
                    row.provider_id == provider_id
                    and row.request_id == request_id
                    and row.status == SamlAuthenticationStatus.PENDING
                ):
                    consumed = row.model_copy(
                        update={
                            "status": SamlAuthenticationStatus.CONSUMED,
                            "assertion_id": assertion_id,
                            "consumed_at": _now(),
                        }
                    )
                    self.authentications[auth_id] = consumed
                    return consumed
            raise SamlAuthenticationNotFound(
                f"no pending SAML authn request {request_id!r} for {provider_id!r}"
            )

        # IdP-initiated — write a fresh consumed row. Caller is responsible
        # for asserting the provider's `allow_idp_initiated` flag is on.
        synthetic = SamlAuthenticationRecord(
            org_id=self._provider_org_id(provider_id),
            provider_id=provider_id,
            request_id=None,
            assertion_id=assertion_id,
            status=SamlAuthenticationStatus.CONSUMED,
            expires_at=_now(),
            consumed_at=_now(),
        )
        self.authentications[synthetic.auth_id] = synthetic
        return synthetic

    def _provider_org_id(self, provider_id: str) -> str:
        # Best-effort — used only by the IdP-initiated synthetic row in
        # the in-memory adapter (Postgres has the org from the existing
        # provider lookup the service performs first).
        for row in self.authentications.values():
            if row.provider_id == provider_id:
                return row.org_id
        return "org_unknown"

    def reject_authentication(
        self,
        *,
        auth_id: str,
        conn: Any | None = None,
    ) -> bool:
        del conn
        existing = self.authentications.get(auth_id)
        if existing is None:
            return False
        self.authentications[auth_id] = existing.model_copy(
            update={"status": SamlAuthenticationStatus.REJECTED}
        )
        return True

    # Identities --------------------------------------------------------
    def create_identity(
        self, record: SamlIdentityRecord, *, conn: Any | None = None
    ) -> SamlIdentityRecord:
        del conn
        record = with_default_principal(record)
        if any(
            ident.provider_id == record.provider_id
            and ident.name_id == record.name_id
            and ident.unlinked_at is None
            for ident in self.identities.values()
        ):
            raise ValueError("SAML identity already linked for this provider/name_id")
        self.identities[record.identity_id] = record
        return record

    def get_identity_by_name_id(
        self, *, provider_id: str, name_id: str
    ) -> SamlIdentityRecord | None:
        for ident in self.identities.values():
            if (
                ident.provider_id == provider_id
                and ident.name_id == name_id
                and ident.unlinked_at is None
            ):
                return ident
        return None

    def update_identity_attributes(
        self,
        *,
        identity_id: str,
        attributes_snapshot: dict[str, Any],
        conn: Any | None = None,
    ) -> bool:
        del conn
        existing = self.identities.get(identity_id)
        if existing is None or existing.unlinked_at is not None:
            return False
        self.identities[identity_id] = existing.model_copy(
            update={"attributes_snapshot": attributes_snapshot}
        )
        return True


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresSamlStore:
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
        self, record: SamlAuthenticationRecord, *, conn: Any | None = None
    ) -> SamlAuthenticationRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO saml_authentications (
                    auth_id, org_id, provider_id, request_id, assertion_id,
                    relay_state, status, requested_at, expires_at,
                    consumed_at, ip, user_agent
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.auth_id,
                    record.org_id,
                    record.provider_id,
                    record.request_id,
                    record.assertion_id,
                    record.relay_state,
                    record.status.value,
                    record.requested_at,
                    record.expires_at,
                    record.consumed_at,
                    record.ip,
                    record.user_agent,
                ),
            )
        return record

    def consume_authentication(
        self,
        *,
        provider_id: str,
        assertion_id: str,
        request_id: str | None,
        conn: Any | None = None,
    ) -> SamlAuthenticationRecord:
        if request_id is not None:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    UPDATE saml_authentications
                    SET status = 'consumed',
                        assertion_id = %s,
                        consumed_at = now()
                    WHERE provider_id = %s
                      AND request_id = %s
                      AND status = 'pending'
                      AND expires_at > now()
                    RETURNING *
                    """,
                    (assertion_id, provider_id, request_id),
                )
                row = cur.fetchone()
            if row is None:
                raise SamlAuthenticationNotFound(
                    f"no pending SAML request {request_id!r} for {provider_id!r}"
                )
            return _row_to_authentication(row)

        # IdP-initiated path: insert a fresh consumed row. The unique index
        # on assertion_id refuses the second attempt — translate the
        # IntegrityError into ``SamlReplayDetected``.
        try:
            from psycopg import errors as pg_errors  # type: ignore

            integrity_error: type = pg_errors.UniqueViolation
        except Exception:  # pragma: no cover — psycopg always present in prod
            integrity_error = Exception
        synthetic = SamlAuthenticationRecord(
            org_id=self._lookup_provider_org_id(provider_id, conn=conn),
            provider_id=provider_id,
            request_id=None,
            assertion_id=assertion_id,
            status=SamlAuthenticationStatus.CONSUMED,
            expires_at=_now(),
            consumed_at=_now(),
        )
        try:
            self.create_authentication(synthetic, conn=conn)
        except integrity_error as exc:
            raise SamlReplayDetected(
                f"assertion {assertion_id!r} already consumed"
            ) from exc
        return synthetic

    def _lookup_provider_org_id(self, provider_id: str, *, conn: Any | None) -> str:
        with self._cursor(conn) as cur:
            cur.execute(
                "SELECT org_id FROM auth_providers WHERE provider_id = %s",
                (provider_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise SamlAuthenticationNotFound(f"unknown SAML provider {provider_id!r}")
        return row["org_id"] if isinstance(row, dict) else row[0]

    def reject_authentication(
        self,
        *,
        auth_id: str,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE saml_authentications
                SET status = 'rejected'
                WHERE auth_id = %s AND status = 'pending'
                """,
                (auth_id,),
            )
            return bool(cur.rowcount)

    # Identities --------------------------------------------------------
    def create_identity(
        self, record: SamlIdentityRecord, *, conn: Any | None = None
    ) -> SamlIdentityRecord:
        record = with_default_principal(record)
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO saml_identities (
                    identity_id, org_id, user_id, provider_id,
                    name_id, name_id_format, linked_at, unlinked_at,
                    attributes_snapshot, principal_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.identity_id,
                    record.org_id,
                    record.user_id,
                    record.provider_id,
                    record.name_id,
                    record.name_id_format,
                    record.linked_at,
                    record.unlinked_at,
                    json.dumps(record.attributes_snapshot),
                    record.principal_id,
                ),
            )
        return record

    def get_identity_by_name_id(
        self, *, provider_id: str, name_id: str
    ) -> SamlIdentityRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM saml_identities
                WHERE provider_id = %s AND name_id = %s
                  AND unlinked_at IS NULL
                """,
                (provider_id, name_id),
            )
            row = cur.fetchone()
        return _row_to_identity(row) if row else None

    def update_identity_attributes(
        self,
        *,
        identity_id: str,
        attributes_snapshot: dict[str, Any],
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE saml_identities
                SET attributes_snapshot = %s
                WHERE identity_id = %s AND unlinked_at IS NULL
                """,
                (json.dumps(attributes_snapshot), identity_id),
            )
            return bool(cur.rowcount)


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _row_to_authentication(row: dict[str, Any]) -> SamlAuthenticationRecord:
    return SamlAuthenticationRecord.model_validate(row)


def _row_to_identity(row: dict[str, Any]) -> SamlIdentityRecord:
    return SamlIdentityRecord.model_validate(
        {**row, "attributes_snapshot": _coerce_json(row.get("attributes_snapshot"))}
    )


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else {}


__all__ = [
    "InMemorySamlStore",
    "PostgresSamlStore",
    "SamlAuthenticationNotFound",
    "SamlReplayDetected",
    "SamlStore",
]
