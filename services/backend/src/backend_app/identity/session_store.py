"""Session store (A2): dict-backed and Postgres-backed adapters.

Stores ``SessionRecord`` rows. The plaintext bearer never crosses this layer
— callers pass ``token_hash = sha256(token signature)`` for lookups.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import SessionRecord


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def create_session(
        self, record: SessionRecord, *, conn: Any | None = None
    ) -> SessionRecord: ...

    def get_session(self, *, session_id: str) -> SessionRecord | None: ...

    def get_active_by_token_hash(
        self, *, session_id: str, token_hash: str
    ) -> SessionRecord | None: ...

    def touch_session(
        self, *, session_id: str, now: datetime, conn: Any | None = None
    ) -> bool: ...

    def revoke_session(
        self,
        *,
        org_id: str,
        session_id: str,
        reason: str | None = None,
        conn: Any | None = None,
    ) -> bool: ...

    def mark_mfa_satisfied(
        self,
        *,
        session_id: str,
        when: datetime,
        promoted_scopes: tuple[str, ...] | None = None,
        conn: Any | None = None,
    ) -> bool:
        """Stamp ``mfa_satisfied_at`` on the session and (optionally) replace
        the ``permission_scopes`` JSONB so the ``mfa:pending`` placeholder
        gets swapped for the session's real scopes. Returns ``True`` when a
        row was updated."""

    def list_active_sessions(
        self, *, org_id: str, user_id: str
    ) -> tuple[SessionRecord, ...]: ...

    def sweep_expired(self, *, before: datetime) -> int:
        """Hard-delete expired+revoked rows older than ``before``. Returns count."""
        ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemorySessionStore:
    sessions: dict[str, SessionRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def create_session(
        self, record: SessionRecord, *, conn: Any | None = None
    ) -> SessionRecord:
        del conn
        # Mirror the partial unique index: (token_hash) WHERE revoked_at IS NULL
        for existing in self.sessions.values():
            if existing.token_hash == record.token_hash and existing.revoked_at is None:
                raise ValueError("active session with this token already exists")
        self.sessions[record.session_id] = record
        return record

    def get_session(self, *, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)

    def get_active_by_token_hash(
        self, *, session_id: str, token_hash: str
    ) -> SessionRecord | None:
        record = self.sessions.get(session_id)
        if record is None:
            return None
        if record.revoked_at is not None:
            return None
        if record.token_hash != token_hash:
            return None
        if record.expires_at <= _now():
            return None
        return record

    def touch_session(
        self, *, session_id: str, now: datetime, conn: Any | None = None
    ) -> bool:
        del conn
        record = self.sessions.get(session_id)
        if record is None or record.revoked_at is not None:
            return False
        self.sessions[session_id] = record.model_copy(update={"last_seen_at": now})
        return True

    def revoke_session(
        self,
        *,
        org_id: str,
        session_id: str,
        reason: str | None = None,
        conn: Any | None = None,
    ) -> bool:
        del conn
        record = self.sessions.get(session_id)
        if record is None:
            return False
        # Cross-tenant guard: caller must own the session.
        if record.org_id != org_id:
            return False
        if record.revoked_at is not None:
            # Idempotent — already revoked.
            return True
        self.sessions[session_id] = record.model_copy(
            update={"revoked_at": _now(), "revocation_reason": reason}
        )
        return True

    def mark_mfa_satisfied(
        self,
        *,
        session_id: str,
        when: datetime,
        promoted_scopes: tuple[str, ...] | None = None,
        conn: Any | None = None,
    ) -> bool:
        del conn
        record = self.sessions.get(session_id)
        if record is None or record.revoked_at is not None:
            return False
        update: dict[str, object] = {"mfa_satisfied_at": when}
        if promoted_scopes is not None:
            update["permission_scopes"] = promoted_scopes
        self.sessions[session_id] = record.model_copy(update=update)
        return True

    def list_active_sessions(
        self, *, org_id: str, user_id: str
    ) -> tuple[SessionRecord, ...]:
        now = _now()
        return tuple(
            sorted(
                (
                    record
                    for record in self.sessions.values()
                    if record.org_id == org_id
                    and record.user_id == user_id
                    and record.revoked_at is None
                    and record.expires_at > now
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )
        )

    def sweep_expired(self, *, before: datetime) -> int:
        purgable = [
            session_id
            for session_id, record in self.sessions.items()
            if record.expires_at < before
        ]
        for session_id in purgable:
            self.sessions.pop(session_id)
        return len(purgable)


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresSessionStore:
    """psycopg-backed session store. Uses the shared backend pool."""

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

    def create_session(
        self, record: SessionRecord, *, conn: Any | None = None
    ) -> SessionRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO sessions (
                    session_id, org_id, user_id, token_hash,
                    roles, permission_scopes, connector_scopes,
                    auth_provider_id, mfa_satisfied_at,
                    client_ip, user_agent, device_label,
                    created_at, last_seen_at, expires_at,
                    revoked_at, revocation_reason
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.session_id,
                    record.org_id,
                    record.user_id,
                    record.token_hash,
                    json.dumps(list(record.roles)),
                    json.dumps(list(record.permission_scopes)),
                    json.dumps(_serialize_connector_scopes(record.connector_scopes)),
                    record.auth_provider_id,
                    record.mfa_satisfied_at,
                    record.client_ip,
                    record.user_agent,
                    record.device_label,
                    record.created_at,
                    record.last_seen_at,
                    record.expires_at,
                    record.revoked_at,
                    record.revocation_reason,
                ),
            )
        return record

    def get_session(self, *, session_id: str) -> SessionRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM sessions WHERE session_id = %s",
                (session_id,),
            )
            row = cur.fetchone()
        return _row_to_session(row) if row else None

    def get_active_by_token_hash(
        self, *, session_id: str, token_hash: str
    ) -> SessionRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM sessions
                WHERE session_id = %s AND token_hash = %s
                  AND revoked_at IS NULL AND expires_at > now()
                """,
                (session_id, token_hash),
            )
            row = cur.fetchone()
        return _row_to_session(row) if row else None

    def touch_session(
        self, *, session_id: str, now: datetime, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE sessions SET last_seen_at = %s
                WHERE session_id = %s AND revoked_at IS NULL
                """,
                (now, session_id),
            )
            return bool(cur.rowcount)

    def revoke_session(
        self,
        *,
        org_id: str,
        session_id: str,
        reason: str | None = None,
        conn: Any | None = None,
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE sessions SET
                    revoked_at = COALESCE(revoked_at, %s),
                    revocation_reason = COALESCE(revocation_reason, %s)
                WHERE session_id = %s AND org_id = %s
                """,
                (_now(), reason, session_id, org_id),
            )
            return bool(cur.rowcount)

    def mark_mfa_satisfied(
        self,
        *,
        session_id: str,
        when: datetime,
        promoted_scopes: tuple[str, ...] | None = None,
        conn: Any | None = None,
    ) -> bool:
        # The COALESCE on the JSONB column lets the caller skip the swap
        # by passing ``promoted_scopes=None`` (e.g. step-up reverify on a
        # session that already had its real scopes).
        scopes_arg = (
            json.dumps(list(promoted_scopes)) if promoted_scopes is not None else None
        )
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE sessions SET
                    mfa_satisfied_at = %s,
                    permission_scopes = COALESCE(%s::jsonb, permission_scopes)
                WHERE session_id = %s AND revoked_at IS NULL
                """,
                (when, scopes_arg, session_id),
            )
            return bool(cur.rowcount)

    def list_active_sessions(
        self, *, org_id: str, user_id: str
    ) -> tuple[SessionRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM sessions
                WHERE org_id = %s AND user_id = %s
                  AND revoked_at IS NULL AND expires_at > now()
                ORDER BY created_at DESC
                """,
                (org_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(_row_to_session(row) for row in rows)

    def sweep_expired(self, *, before: datetime) -> int:
        with self._cursor(None) as cur:
            cur.execute(
                "DELETE FROM sessions WHERE expires_at < %s",
                (before,),
            )
            return int(cur.rowcount or 0)


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _row_to_session(row: dict[str, Any]) -> SessionRecord:
    return SessionRecord.model_validate(
        {
            **row,
            "roles": tuple(_coerce_json(row.get("roles")) or ()),
            "permission_scopes": tuple(
                _coerce_json(row.get("permission_scopes")) or ()
            ),
            "connector_scopes": _deserialize_connector_scopes(
                _coerce_json(row.get("connector_scopes"))
            ),
        }
    )


def _serialize_connector_scopes(
    value: dict[str, tuple[str, ...]],
) -> dict[str, list[str]]:
    return {connector: list(scopes) for connector, scopes in value.items()}


def _deserialize_connector_scopes(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    return {str(connector): tuple(scopes or ()) for connector, scopes in value.items()}


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else {}
