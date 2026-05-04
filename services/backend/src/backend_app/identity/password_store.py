"""Local password store (A4): credentials, policy, reset tokens.

In-memory + Postgres adapters. Per the project boundary rule, this lives
inside the identity package so subsequent PRs (A5/A6/A7/A8) can compose
against it without bloating the foundation file.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    LocalCredentialRecord,
    PasswordPolicyRecord,
    PasswordResetTokenRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PasswordStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Credentials -------------------------------------------------------
    def upsert_credential(
        self, record: LocalCredentialRecord, *, conn: Any | None = None
    ) -> LocalCredentialRecord: ...

    def get_credential(
        self, *, org_id: str, user_id: str
    ) -> LocalCredentialRecord | None: ...

    def update_credential_last_used(
        self, *, credential_id: str, when: datetime, conn: Any | None = None
    ) -> bool: ...

    def soft_delete_credential(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool: ...

    # Policy ------------------------------------------------------------
    def get_policy(self, *, org_id: str) -> PasswordPolicyRecord | None: ...
    def upsert_policy(
        self, record: PasswordPolicyRecord, *, conn: Any | None = None
    ) -> PasswordPolicyRecord: ...

    # Reset tokens ------------------------------------------------------
    def create_reset_token(
        self, record: PasswordResetTokenRecord, *, conn: Any | None = None
    ) -> PasswordResetTokenRecord: ...

    def consume_reset_token(
        self, *, token_hash: str, conn: Any | None = None
    ) -> PasswordResetTokenRecord | None: ...

    def sweep_expired_reset_tokens(self, *, before: datetime) -> int: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryPasswordStore:
    credentials: dict[str, LocalCredentialRecord] = field(default_factory=dict)
    policies_by_org: dict[str, PasswordPolicyRecord] = field(default_factory=dict)
    reset_tokens: dict[str, PasswordResetTokenRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Credentials -------------------------------------------------------
    def upsert_credential(
        self, record: LocalCredentialRecord, *, conn: Any | None = None
    ) -> LocalCredentialRecord:
        del conn
        # Find existing active credential for the user (if any) and replace.
        for credential_id, existing in list(self.credentials.items()):
            if (
                existing.org_id == record.org_id
                and existing.user_id == record.user_id
                and existing.deleted_at is None
            ):
                self.credentials.pop(credential_id)
        self.credentials[record.credential_id] = record
        return record

    def get_credential(
        self, *, org_id: str, user_id: str
    ) -> LocalCredentialRecord | None:
        for record in self.credentials.values():
            if (
                record.org_id == org_id
                and record.user_id == user_id
                and record.deleted_at is None
            ):
                return record
        return None

    def update_credential_last_used(
        self, *, credential_id: str, when: datetime, conn: Any | None = None
    ) -> bool:
        del conn
        existing = self.credentials.get(credential_id)
        if existing is None or existing.deleted_at is not None:
            return False
        self.credentials[credential_id] = existing.model_copy(
            update={"last_used_at": when}
        )
        return True

    def soft_delete_credential(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool:
        del conn
        for credential_id, existing in list(self.credentials.items()):
            if (
                existing.org_id == org_id
                and existing.user_id == user_id
                and existing.deleted_at is None
            ):
                self.credentials[credential_id] = existing.model_copy(
                    update={"deleted_at": _now()}
                )
                return True
        return False

    # Policy ------------------------------------------------------------
    def get_policy(self, *, org_id: str) -> PasswordPolicyRecord | None:
        return self.policies_by_org.get(org_id)

    def upsert_policy(
        self, record: PasswordPolicyRecord, *, conn: Any | None = None
    ) -> PasswordPolicyRecord:
        del conn
        self.policies_by_org[record.org_id] = record
        return record

    # Reset tokens ------------------------------------------------------
    def create_reset_token(
        self, record: PasswordResetTokenRecord, *, conn: Any | None = None
    ) -> PasswordResetTokenRecord:
        del conn
        if any(
            r.token_hash == record.token_hash and r.consumed_at is None
            for r in self.reset_tokens.values()
        ):
            raise ValueError("active reset token with this hash already exists")
        self.reset_tokens[record.token_id] = record
        return record

    def consume_reset_token(
        self, *, token_hash: str, conn: Any | None = None
    ) -> PasswordResetTokenRecord | None:
        del conn
        for token_id, row in list(self.reset_tokens.items()):
            if row.token_hash != token_hash:
                continue
            if row.consumed_at is not None:
                return None
            if row.expires_at <= _now():
                return None
            consumed = row.model_copy(update={"consumed_at": _now()})
            self.reset_tokens[token_id] = consumed
            return consumed
        return None

    def sweep_expired_reset_tokens(self, *, before: datetime) -> int:
        purgable = [
            token_id
            for token_id, record in self.reset_tokens.items()
            if record.expires_at < before
        ]
        for token_id in purgable:
            self.reset_tokens.pop(token_id)
        return len(purgable)


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresPasswordStore:
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

    # Credentials -------------------------------------------------------
    def upsert_credential(
        self, record: LocalCredentialRecord, *, conn: Any | None = None
    ) -> LocalCredentialRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE local_credentials
                SET deleted_at = now()
                WHERE org_id = %s AND user_id = %s AND deleted_at IS NULL
                """,
                (record.org_id, record.user_id),
            )
            cur.execute(
                """
                INSERT INTO local_credentials (
                    credential_id, org_id, user_id, password_hash,
                    password_set_at, must_rotate_at, last_used_at,
                    previous_hashes, created_at, updated_at, deleted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.credential_id,
                    record.org_id,
                    record.user_id,
                    record.password_hash,
                    record.password_set_at,
                    record.must_rotate_at,
                    record.last_used_at,
                    json.dumps(list(record.previous_hashes)),
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                ),
            )
        return record

    def get_credential(
        self, *, org_id: str, user_id: str
    ) -> LocalCredentialRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM local_credentials
                WHERE org_id = %s AND user_id = %s AND deleted_at IS NULL
                """,
                (org_id, user_id),
            )
            row = cur.fetchone()
        return _row_to_credential(row) if row else None

    def update_credential_last_used(
        self, *, credential_id: str, when: datetime, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE local_credentials
                SET last_used_at = %s, updated_at = now()
                WHERE credential_id = %s AND deleted_at IS NULL
                """,
                (when, credential_id),
            )
            return bool(cur.rowcount)

    def soft_delete_credential(
        self, *, org_id: str, user_id: str, conn: Any | None = None
    ) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE local_credentials
                SET deleted_at = now(), updated_at = now()
                WHERE org_id = %s AND user_id = %s AND deleted_at IS NULL
                """,
                (org_id, user_id),
            )
            return bool(cur.rowcount)

    # Policy ------------------------------------------------------------
    def get_policy(self, *, org_id: str) -> PasswordPolicyRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM password_policies WHERE org_id = %s",
                (org_id,),
            )
            row = cur.fetchone()
        return _row_to_policy(row) if row else None

    def upsert_policy(
        self, record: PasswordPolicyRecord, *, conn: Any | None = None
    ) -> PasswordPolicyRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO password_policies (
                    policy_id, org_id, min_length, require_upper, require_lower,
                    require_digit, require_symbol, rotation_days, reuse_window,
                    updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (org_id) DO UPDATE SET
                    min_length = EXCLUDED.min_length,
                    require_upper = EXCLUDED.require_upper,
                    require_lower = EXCLUDED.require_lower,
                    require_digit = EXCLUDED.require_digit,
                    require_symbol = EXCLUDED.require_symbol,
                    rotation_days = EXCLUDED.rotation_days,
                    reuse_window = EXCLUDED.reuse_window,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    record.policy_id,
                    record.org_id,
                    record.min_length,
                    record.require_upper,
                    record.require_lower,
                    record.require_digit,
                    record.require_symbol,
                    record.rotation_days,
                    record.reuse_window,
                    record.updated_at,
                ),
            )
        return record

    # Reset tokens ------------------------------------------------------
    def create_reset_token(
        self, record: PasswordResetTokenRecord, *, conn: Any | None = None
    ) -> PasswordResetTokenRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO password_reset_tokens (
                    token_id, org_id, user_id, token_hash,
                    created_at, expires_at, consumed_at, request_ip
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.token_id,
                    record.org_id,
                    record.user_id,
                    record.token_hash,
                    record.created_at,
                    record.expires_at,
                    record.consumed_at,
                    record.request_ip,
                ),
            )
        return record

    def consume_reset_token(
        self, *, token_hash: str, conn: Any | None = None
    ) -> PasswordResetTokenRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE password_reset_tokens
                SET consumed_at = now()
                WHERE token_hash = %s
                  AND consumed_at IS NULL
                  AND expires_at > now()
                RETURNING *
                """,
                (token_hash,),
            )
            row = cur.fetchone()
        return _row_to_reset_token(row) if row else None

    def sweep_expired_reset_tokens(self, *, before: datetime) -> int:
        with self._cursor(None) as cur:
            cur.execute(
                "DELETE FROM password_reset_tokens WHERE expires_at < %s",
                (before,),
            )
            return int(cur.rowcount or 0)


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _row_to_credential(row: dict[str, Any]) -> LocalCredentialRecord:
    return LocalCredentialRecord.model_validate(
        {
            **row,
            "previous_hashes": tuple(_coerce_json(row.get("previous_hashes")) or ()),
        }
    )


def _row_to_policy(row: dict[str, Any]) -> PasswordPolicyRecord:
    return PasswordPolicyRecord.model_validate(row)


def _row_to_reset_token(row: dict[str, Any]) -> PasswordResetTokenRecord:
    return PasswordResetTokenRecord.model_validate(row)


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value if value is not None else []
