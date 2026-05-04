"""MFA store (A6): factors, TOTP secrets, WebAuthn credentials, challenges,
recovery codes.

In-memory + Postgres adapters. Each adapter is a thin CRUD layer; the
business logic (replay guards, sign-count checks, single-use enforcement)
lives in ``mfa.py`` so unit tests can drive it without a database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.contracts import (
    MfaChallengeRecord,
    MfaFactorKind,
    MfaFactorRecord,
    MfaRecoveryCodeRecord,
    TotpSecretRecord,
    WebAuthnCredentialRecord,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MfaStore(Protocol):
    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # Factors -----------------------------------------------------------
    def create_factor(
        self, record: MfaFactorRecord, *, conn: Any | None = None
    ) -> MfaFactorRecord: ...
    def get_factor(self, *, factor_id: str) -> MfaFactorRecord | None: ...
    def list_factors(
        self, *, org_id: str, user_id: str, enabled_only: bool = False
    ) -> tuple[MfaFactorRecord, ...]: ...
    def enable_factor(
        self, *, factor_id: str, conn: Any | None = None
    ) -> MfaFactorRecord | None: ...
    def disable_factor(
        self, *, factor_id: str, conn: Any | None = None
    ) -> MfaFactorRecord | None: ...
    def touch_factor(
        self, *, factor_id: str, when: datetime, conn: Any | None = None
    ) -> None: ...

    # TOTP --------------------------------------------------------------
    def create_totp_secret(
        self, record: TotpSecretRecord, *, conn: Any | None = None
    ) -> TotpSecretRecord: ...
    def get_totp_secret_for_factor(
        self, *, factor_id: str
    ) -> TotpSecretRecord | None: ...
    def update_totp_last_step(
        self, *, secret_id: str, last_step: int, conn: Any | None = None
    ) -> None: ...

    # WebAuthn ----------------------------------------------------------
    def create_webauthn_credential(
        self, record: WebAuthnCredentialRecord, *, conn: Any | None = None
    ) -> WebAuthnCredentialRecord: ...
    def get_webauthn_credential_by_b64(
        self, *, credential_id_b64: str
    ) -> WebAuthnCredentialRecord | None: ...
    def list_webauthn_credentials_for_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[WebAuthnCredentialRecord, ...]: ...
    def update_webauthn_sign_count(
        self,
        *,
        credential_id_b64: str,
        new_sign_count: int,
        when: datetime,
        conn: Any | None = None,
    ) -> bool: ...

    # Challenges --------------------------------------------------------
    def create_challenge(
        self, record: MfaChallengeRecord, *, conn: Any | None = None
    ) -> MfaChallengeRecord: ...
    def consume_challenge(
        self, *, challenge_id: str, now: datetime, conn: Any | None = None
    ) -> MfaChallengeRecord | None:
        """Atomic: ``UPDATE...RETURNING`` with consumed_at IS NULL guard
        so two workers can't satisfy the same challenge twice."""

    # Recovery codes ----------------------------------------------------
    def store_recovery_codes(
        self, records: tuple[MfaRecoveryCodeRecord, ...], *, conn: Any | None = None
    ) -> None: ...
    def consume_recovery_code(
        self, *, code_hash: str, now: datetime, conn: Any | None = None
    ) -> MfaRecoveryCodeRecord | None: ...
    def list_active_recovery_codes(
        self, *, org_id: str, user_id: str
    ) -> tuple[MfaRecoveryCodeRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryMfaStore:
    factors: dict[str, MfaFactorRecord] = field(default_factory=dict)
    totp_secrets: dict[str, TotpSecretRecord] = field(default_factory=dict)
    webauthn: dict[str, WebAuthnCredentialRecord] = field(default_factory=dict)
    challenges: dict[str, MfaChallengeRecord] = field(default_factory=dict)
    recovery_codes: dict[str, MfaRecoveryCodeRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    # Factors -----------------------------------------------------------
    def create_factor(
        self, record: MfaFactorRecord, *, conn: Any | None = None
    ) -> MfaFactorRecord:
        del conn
        self.factors[record.factor_id] = record
        return record

    def get_factor(self, *, factor_id: str) -> MfaFactorRecord | None:
        return self.factors.get(factor_id)

    def list_factors(
        self, *, org_id: str, user_id: str, enabled_only: bool = False
    ) -> tuple[MfaFactorRecord, ...]:
        rows = [
            r
            for r in self.factors.values()
            if r.org_id == org_id
            and r.user_id == user_id
            and r.disabled_at is None
            and (not enabled_only or r.enabled)
        ]
        rows.sort(key=lambda r: r.enrolled_at)
        return tuple(rows)

    def enable_factor(
        self, *, factor_id: str, conn: Any | None = None
    ) -> MfaFactorRecord | None:
        del conn
        existing = self.factors.get(factor_id)
        if existing is None or existing.disabled_at is not None:
            return None
        updated = existing.model_copy(update={"enabled": True})
        self.factors[factor_id] = updated
        return updated

    def disable_factor(
        self, *, factor_id: str, conn: Any | None = None
    ) -> MfaFactorRecord | None:
        del conn
        existing = self.factors.get(factor_id)
        if existing is None or existing.disabled_at is not None:
            return None
        updated = existing.model_copy(update={"enabled": False, "disabled_at": _now()})
        self.factors[factor_id] = updated
        return updated

    def touch_factor(
        self, *, factor_id: str, when: datetime, conn: Any | None = None
    ) -> None:
        del conn
        existing = self.factors.get(factor_id)
        if existing is None:
            return
        self.factors[factor_id] = existing.model_copy(update={"last_used_at": when})

    # TOTP --------------------------------------------------------------
    def create_totp_secret(
        self, record: TotpSecretRecord, *, conn: Any | None = None
    ) -> TotpSecretRecord:
        del conn
        self.totp_secrets[record.secret_id] = record
        return record

    def get_totp_secret_for_factor(self, *, factor_id: str) -> TotpSecretRecord | None:
        for record in self.totp_secrets.values():
            if record.factor_id == factor_id:
                return record
        return None

    def update_totp_last_step(
        self, *, secret_id: str, last_step: int, conn: Any | None = None
    ) -> None:
        del conn
        existing = self.totp_secrets.get(secret_id)
        if existing is None:
            return
        self.totp_secrets[secret_id] = existing.model_copy(
            update={"last_step": last_step}
        )

    # WebAuthn ----------------------------------------------------------
    def create_webauthn_credential(
        self, record: WebAuthnCredentialRecord, *, conn: Any | None = None
    ) -> WebAuthnCredentialRecord:
        del conn
        self.webauthn[record.credential_id] = record
        return record

    def get_webauthn_credential_by_b64(
        self, *, credential_id_b64: str
    ) -> WebAuthnCredentialRecord | None:
        for record in self.webauthn.values():
            if record.credential_id_b64 == credential_id_b64:
                return record
        return None

    def list_webauthn_credentials_for_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[WebAuthnCredentialRecord, ...]:
        # Cross-reference factors to enforce tenant + user scope.
        owned_factor_ids = {
            f.factor_id
            for f in self.factors.values()
            if f.org_id == org_id
            and f.user_id == user_id
            and f.kind == MfaFactorKind.WEBAUTHN
            and f.disabled_at is None
        }
        return tuple(
            r for r in self.webauthn.values() if r.factor_id in owned_factor_ids
        )

    def update_webauthn_sign_count(
        self,
        *,
        credential_id_b64: str,
        new_sign_count: int,
        when: datetime,
        conn: Any | None = None,
    ) -> bool:
        del conn
        for cid, record in list(self.webauthn.items()):
            if record.credential_id_b64 != credential_id_b64:
                continue
            if new_sign_count <= record.sign_count:
                # Cloned-credential guard. Sign-count must strictly grow.
                return False
            self.webauthn[cid] = record.model_copy(
                update={"sign_count": new_sign_count, "last_used_at": when}
            )
            return True
        return False

    # Challenges --------------------------------------------------------
    def create_challenge(
        self, record: MfaChallengeRecord, *, conn: Any | None = None
    ) -> MfaChallengeRecord:
        del conn
        self.challenges[record.challenge_id] = record
        return record

    def consume_challenge(
        self, *, challenge_id: str, now: datetime, conn: Any | None = None
    ) -> MfaChallengeRecord | None:
        del conn
        existing = self.challenges.get(challenge_id)
        if existing is None:
            return None
        if existing.consumed_at is not None:
            return None
        if existing.expires_at <= now:
            return None
        consumed = existing.model_copy(update={"consumed_at": now})
        self.challenges[challenge_id] = consumed
        return consumed

    # Recovery codes ----------------------------------------------------
    def store_recovery_codes(
        self, records: tuple[MfaRecoveryCodeRecord, ...], *, conn: Any | None = None
    ) -> None:
        del conn
        for record in records:
            self.recovery_codes[record.code_id] = record

    def consume_recovery_code(
        self, *, code_hash: str, now: datetime, conn: Any | None = None
    ) -> MfaRecoveryCodeRecord | None:
        del conn
        for cid, record in list(self.recovery_codes.items()):
            if record.code_hash != code_hash or record.consumed_at is not None:
                continue
            consumed = record.model_copy(update={"consumed_at": now})
            self.recovery_codes[cid] = consumed
            return consumed
        return None

    def list_active_recovery_codes(
        self, *, org_id: str, user_id: str
    ) -> tuple[MfaRecoveryCodeRecord, ...]:
        return tuple(
            r
            for r in self.recovery_codes.values()
            if r.org_id == org_id and r.user_id == user_id and r.consumed_at is None
        )


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


class PostgresMfaStore:
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
        with self._pool.connection() as outer:
            with outer.cursor() as cur:
                yield cur

    # Factors -----------------------------------------------------------
    def create_factor(
        self, record: MfaFactorRecord, *, conn: Any | None = None
    ) -> MfaFactorRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO mfa_factors (
                    factor_id, org_id, user_id, kind, display_name,
                    enabled, enrolled_at, last_used_at, disabled_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.factor_id,
                    record.org_id,
                    record.user_id,
                    record.kind.value,
                    record.display_name,
                    record.enabled,
                    record.enrolled_at,
                    record.last_used_at,
                    record.disabled_at,
                ),
            )
        return record

    def get_factor(self, *, factor_id: str) -> MfaFactorRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM mfa_factors WHERE factor_id = %s",
                (factor_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return MfaFactorRecord.model_validate(dict(row))

    def list_factors(
        self, *, org_id: str, user_id: str, enabled_only: bool = False
    ) -> tuple[MfaFactorRecord, ...]:
        clause = "WHERE org_id = %s AND user_id = %s AND disabled_at IS NULL"
        params: list[Any] = [org_id, user_id]
        if enabled_only:
            clause += " AND enabled = TRUE"
        with self._cursor(None) as cur:
            cur.execute(
                f"SELECT * FROM mfa_factors {clause} ORDER BY enrolled_at",
                tuple(params),
            )
            rows = cur.fetchall()
        return tuple(MfaFactorRecord.model_validate(dict(row)) for row in rows)

    def enable_factor(
        self, *, factor_id: str, conn: Any | None = None
    ) -> MfaFactorRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE mfa_factors
                SET enabled = TRUE
                WHERE factor_id = %s AND disabled_at IS NULL
                RETURNING *
                """,
                (factor_id,),
            )
            row = cur.fetchone()
        return MfaFactorRecord.model_validate(dict(row)) if row else None

    def disable_factor(
        self, *, factor_id: str, conn: Any | None = None
    ) -> MfaFactorRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE mfa_factors
                SET enabled = FALSE, disabled_at = %s
                WHERE factor_id = %s AND disabled_at IS NULL
                RETURNING *
                """,
                (_now(), factor_id),
            )
            row = cur.fetchone()
        return MfaFactorRecord.model_validate(dict(row)) if row else None

    def touch_factor(
        self, *, factor_id: str, when: datetime, conn: Any | None = None
    ) -> None:
        with self._cursor(conn) as cur:
            cur.execute(
                "UPDATE mfa_factors SET last_used_at = %s WHERE factor_id = %s",
                (when, factor_id),
            )

    # TOTP --------------------------------------------------------------
    def create_totp_secret(
        self, record: TotpSecretRecord, *, conn: Any | None = None
    ) -> TotpSecretRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO totp_secrets (
                    secret_id, factor_id, encrypted_secret, last_step, created_at
                ) VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    record.secret_id,
                    record.factor_id,
                    record.encrypted_secret,
                    record.last_step,
                    record.created_at,
                ),
            )
        return record

    def get_totp_secret_for_factor(self, *, factor_id: str) -> TotpSecretRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM totp_secrets WHERE factor_id = %s",
                (factor_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return TotpSecretRecord.model_validate(dict(row))

    def update_totp_last_step(
        self, *, secret_id: str, last_step: int, conn: Any | None = None
    ) -> None:
        with self._cursor(conn) as cur:
            cur.execute(
                "UPDATE totp_secrets SET last_step = %s WHERE secret_id = %s",
                (last_step, secret_id),
            )

    # WebAuthn ----------------------------------------------------------
    def create_webauthn_credential(
        self, record: WebAuthnCredentialRecord, *, conn: Any | None = None
    ) -> WebAuthnCredentialRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO webauthn_credentials (
                    credential_id, factor_id, credential_id_b64,
                    public_key_cose, sign_count, transports, aaguid,
                    attestation_format, rp_id, created_at, last_used_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.credential_id,
                    record.factor_id,
                    record.credential_id_b64,
                    record.public_key_cose,
                    record.sign_count,
                    json.dumps(list(record.transports)),
                    record.aaguid,
                    record.attestation_format,
                    record.rp_id,
                    record.created_at,
                    record.last_used_at,
                ),
            )
        return record

    def get_webauthn_credential_by_b64(
        self, *, credential_id_b64: str
    ) -> WebAuthnCredentialRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT * FROM webauthn_credentials WHERE credential_id_b64 = %s",
                (credential_id_b64,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        record = dict(row)
        record["transports"] = tuple(_coerce_jsonb(record.get("transports")))
        return WebAuthnCredentialRecord.model_validate(record)

    def list_webauthn_credentials_for_user(
        self, *, org_id: str, user_id: str
    ) -> tuple[WebAuthnCredentialRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT wc.* FROM webauthn_credentials wc
                JOIN mfa_factors f ON f.factor_id = wc.factor_id
                WHERE f.org_id = %s AND f.user_id = %s
                  AND f.disabled_at IS NULL
                """,
                (org_id, user_id),
            )
            rows = cur.fetchall()
        out: list[WebAuthnCredentialRecord] = []
        for row in rows:
            record = dict(row)
            record["transports"] = tuple(_coerce_jsonb(record.get("transports")))
            out.append(WebAuthnCredentialRecord.model_validate(record))
        return tuple(out)

    def update_webauthn_sign_count(
        self,
        *,
        credential_id_b64: str,
        new_sign_count: int,
        when: datetime,
        conn: Any | None = None,
    ) -> bool:
        # Cloned-credential guard: ``new_sign_count > sign_count`` MUST
        # hold. The CAS via WHERE clause means two parallel verifies can
        # both submit the same value and only one wins (the other returns
        # rowcount=0 and the route fails the verify).
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE webauthn_credentials
                SET sign_count = %s, last_used_at = %s
                WHERE credential_id_b64 = %s AND %s > sign_count
                """,
                (new_sign_count, when, credential_id_b64, new_sign_count),
            )
            return cur.rowcount > 0

    # Challenges --------------------------------------------------------
    def create_challenge(
        self, record: MfaChallengeRecord, *, conn: Any | None = None
    ) -> MfaChallengeRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO mfa_challenges (
                    challenge_id, org_id, user_id, kind, nonce,
                    expected_factor_id, payload, expires_at,
                    consumed_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    record.challenge_id,
                    record.org_id,
                    record.user_id,
                    record.kind.value,
                    record.nonce,
                    record.expected_factor_id,
                    json.dumps(record.payload),
                    record.expires_at,
                    record.consumed_at,
                    record.created_at,
                ),
            )
        return record

    def consume_challenge(
        self, *, challenge_id: str, now: datetime, conn: Any | None = None
    ) -> MfaChallengeRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE mfa_challenges
                SET consumed_at = %s
                WHERE challenge_id = %s
                  AND consumed_at IS NULL
                  AND expires_at > %s
                RETURNING *
                """,
                (now, challenge_id, now),
            )
            row = cur.fetchone()
        if row is None:
            return None
        record = dict(row)
        record["payload"] = _coerce_jsonb(record.get("payload"))
        return MfaChallengeRecord.model_validate(record)

    # Recovery codes ----------------------------------------------------
    def store_recovery_codes(
        self, records: tuple[MfaRecoveryCodeRecord, ...], *, conn: Any | None = None
    ) -> None:
        if not records:
            return
        with self._cursor(conn) as cur:
            cur.executemany(
                """
                INSERT INTO mfa_recovery_codes (
                    code_id, org_id, user_id, code_hash, consumed_at, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        r.code_id,
                        r.org_id,
                        r.user_id,
                        r.code_hash,
                        r.consumed_at,
                        r.created_at,
                    )
                    for r in records
                ],
            )

    def consume_recovery_code(
        self, *, code_hash: str, now: datetime, conn: Any | None = None
    ) -> MfaRecoveryCodeRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE mfa_recovery_codes
                SET consumed_at = %s
                WHERE code_hash = %s AND consumed_at IS NULL
                RETURNING *
                """,
                (now, code_hash),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return MfaRecoveryCodeRecord.model_validate(dict(row))

    def list_active_recovery_codes(
        self, *, org_id: str, user_id: str
    ) -> tuple[MfaRecoveryCodeRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT * FROM mfa_recovery_codes
                WHERE org_id = %s AND user_id = %s AND consumed_at IS NULL
                """,
                (org_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(MfaRecoveryCodeRecord.model_validate(dict(row)) for row in rows)


def _coerce_jsonb(value: Any) -> Any:
    if value is None:
        return [] if isinstance(value, list) else value
    if isinstance(value, str):
        return json.loads(value)
    return value


__all__ = [
    "InMemoryMfaStore",
    "MfaStore",
    "PostgresMfaStore",
]
