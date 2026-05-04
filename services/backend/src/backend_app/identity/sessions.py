"""Session service (A2): mint / touch / revoke / list / dev-mint.

The bearer-token format mirrors the facade's compact HMAC scheme so a token
issued here is verifiable by the facade with the same shared secret. We do
not import facade code (hard service boundary) — the cryptographic primitive
is duplicated.

Token wire shape:
    base64url(json_payload).base64url(hmac_sha256(json_payload))

Stored in the DB as ``token_hash = sha256(signature)`` so a leaked DB dump
is unusable without the auth secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from enterprise_service_contracts.auth_claims import (
    CLAIM_CONNECTOR_SCOPES,
    CLAIM_EXPIRES_AT,
    CLAIM_ORG_ID,
    CLAIM_PERMISSION_SCOPES,
    CLAIM_ROLES,
    CLAIM_SID,
    CLAIM_USER_ID,
)

from backend_app.contracts import (
    IdentityAuditEventRecord,
    SessionMintResult,
    SessionRecord,
    SessionTouchResult,
)
from backend_app.identity.session_store import SessionStore


_LOGGER = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 8 * 60 * 60  # 8 hours
_MAX_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days hard cap
_DEFAULT_RETENTION_AFTER_EXPIRY_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionAuthSecretMissing(RuntimeError):
    """Raised when ENTERPRISE_AUTH_SECRET is required but unset.

    The session service signs and verifies bearers; without the secret we
    cannot mint or validate. Mirror the facade's fail-closed behavior.
    """


class SessionInvalidToken(RuntimeError):
    """Raised when a presented bearer is malformed or signature-invalid."""


class SessionNotActive(RuntimeError):
    """Raised when a presented session is revoked / expired / unknown."""


class DevMintNotAllowed(RuntimeError):
    """Raised when dev-mint is invoked under a deployment profile that forbids it."""


@dataclass(frozen=True)
class _TokenComponents:
    payload: dict[str, Any]
    payload_b64: str
    signature_b64: str

    @property
    def signature_hash(self) -> str:
        return hashlib.sha256(self.signature_b64.encode("ascii")).hexdigest()


class _BearerCodec:
    """Thin wrapper around the existing HMAC-SHA256 token shape.

    Kept inside the service module so callers don't need to know the wire
    layout. Mirrors backend_facade.auth.FacadeAuthenticator._sign /
    _b64encode without importing across the service boundary.
    """

    @classmethod
    def encode(cls, payload: dict[str, Any], secret: str) -> str:
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        payload_b64 = cls._b64encode(canonical.encode("utf-8"))
        signature = hmac.new(
            secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        signature_b64 = cls._b64encode(signature)
        return f"{payload_b64}.{signature_b64}"

    @classmethod
    def decode(cls, token: str, secret: str) -> _TokenComponents:
        try:
            payload_b64, signature_b64 = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise SessionInvalidToken("malformed bearer token") from exc
        expected = hmac.new(
            secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        expected_b64 = cls._b64encode(expected)
        if not hmac.compare_digest(signature_b64, expected_b64):
            raise SessionInvalidToken("invalid bearer signature")
        try:
            payload_obj = json.loads(cls._b64decode(payload_b64).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise SessionInvalidToken("invalid bearer payload") from exc
        if not isinstance(payload_obj, dict):
            raise SessionInvalidToken("invalid bearer payload shape")
        return _TokenComponents(
            payload=payload_obj,
            payload_b64=payload_b64,
            signature_b64=signature_b64,
        )

    @classmethod
    def hash_signature(cls, signature_b64: str) -> str:
        return hashlib.sha256(signature_b64.encode("ascii")).hexdigest()

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


@dataclass(frozen=True)
class SessionPolicy:
    default_ttl_seconds: int = _DEFAULT_TTL_SECONDS
    max_ttl_seconds: int = _MAX_TTL_SECONDS
    retention_after_expiry_seconds: int = _DEFAULT_RETENTION_AFTER_EXPIRY_SECONDS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SessionPolicy":
        env = env if env is not None else dict(os.environ)
        return cls(
            default_ttl_seconds=cls._read_int(
                env, "SESSION_DEFAULT_TTL_SECONDS", _DEFAULT_TTL_SECONDS
            ),
            max_ttl_seconds=cls._read_int(
                env, "SESSION_MAX_TTL_SECONDS", _MAX_TTL_SECONDS
            ),
            retention_after_expiry_seconds=cls._read_int(
                env,
                "SESSION_RETENTION_AFTER_EXPIRY_SECONDS",
                _DEFAULT_RETENTION_AFTER_EXPIRY_SECONDS,
            ),
        )

    @staticmethod
    def _read_int(env: dict[str, str], key: str, default: int) -> int:
        raw = env.get(key, "").strip()
        if not raw:
            return default
        try:
            return max(1, int(raw))
        except ValueError:
            return default


class SessionService:
    """High-level operations on the sessions table.

    Caller supplies the auth secret via env (``ENTERPRISE_AUTH_SECRET``) so
    the same value used by the facade applies here. Tests can pass an
    explicit secret to ``__init__`` to avoid env coupling.
    """

    def __init__(
        self,
        store: SessionStore,
        *,
        auth_secret: str | None = None,
        policy: SessionPolicy | None = None,
        dev_mint_allowed: bool = False,
    ) -> None:
        self._store = store
        self._auth_secret = (
            auth_secret if auth_secret is not None else self._read_secret()
        )
        self._policy = policy or SessionPolicy.from_env()
        self._dev_mint_allowed = dev_mint_allowed

    @staticmethod
    def _read_secret() -> str:
        value = os.environ.get("ENTERPRISE_AUTH_SECRET", "").strip()
        if not value:
            raise SessionAuthSecretMissing(
                "ENTERPRISE_AUTH_SECRET must be set for the session service"
            )
        return value

    # Mint --------------------------------------------------------------
    def create(
        self,
        *,
        org_id: str,
        user_id: str,
        roles: tuple[str, ...] | list[str] = ("employee",),
        permission_scopes: tuple[str, ...] | list[str] = (),
        connector_scopes: dict[str, tuple[str, ...]] | None = None,
        ttl_seconds: int | None = None,
        auth_provider_id: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        device_label: str | None = None,
    ) -> SessionMintResult:
        ttl = self._resolve_ttl(ttl_seconds)
        expires_at = _now() + timedelta(seconds=ttl)
        record = SessionRecord(
            org_id=org_id,
            user_id=user_id,
            token_hash="",  # placeholder; replaced after we sign
            roles=tuple(roles),
            permission_scopes=tuple(permission_scopes),
            connector_scopes=connector_scopes or {},
            auth_provider_id=auth_provider_id,
            client_ip=client_ip,
            user_agent=user_agent,
            device_label=device_label,
            expires_at=expires_at,
        )
        bearer = self._sign_for(record)
        components = _BearerCodec.decode(bearer, self._auth_secret)
        record = record.model_copy(update={"token_hash": components.signature_hash})
        self._store.create_session(record)
        _LOGGER.info(
            "session_created session_id=%s org_id=%s user_id=%s ttl_s=%d",
            record.session_id,
            record.org_id,
            record.user_id,
            ttl,
        )
        return SessionMintResult(
            session_id=record.session_id,
            bearer_token=bearer,
            expires_at=record.expires_at,
        )

    def dev_mint(
        self,
        *,
        org_id: str,
        user_id: str,
        roles: tuple[str, ...] = ("employee",),
        permission_scopes: tuple[str, ...] = ("runtime:use",),
        connector_scopes: dict[str, tuple[str, ...]] | None = None,
        ttl_seconds: int = 24 * 60 * 60,
    ) -> SessionMintResult:
        if not self._dev_mint_allowed:
            raise DevMintNotAllowed(
                "dev_mint disabled under the active deployment profile"
            )
        return self.create(
            org_id=org_id,
            user_id=user_id,
            roles=roles,
            permission_scopes=permission_scopes,
            connector_scopes=connector_scopes,
            ttl_seconds=ttl_seconds,
            auth_provider_id=None,
            device_label="dev-mint",
        )

    # Touch -------------------------------------------------------------
    def touch_by_token(self, bearer: str) -> SessionTouchResult:
        components = _BearerCodec.decode(bearer, self._auth_secret)
        sid = components.payload.get(CLAIM_SID)
        if not isinstance(sid, str) or not sid:
            raise SessionNotActive("token has no sid claim")
        record = self._store.get_active_by_token_hash(
            session_id=sid, token_hash=components.signature_hash
        )
        if record is None:
            raise SessionNotActive("session not active")
        self._store.touch_session(session_id=sid, now=_now())
        return self._touch_result(record)

    def touch_by_components(
        self, *, session_id: str, token_hash: str
    ) -> SessionTouchResult:
        """Used by the facade after it has already verified HMAC locally."""

        record = self._store.get_active_by_token_hash(
            session_id=session_id, token_hash=token_hash
        )
        if record is None:
            raise SessionNotActive("session not active")
        self._store.touch_session(session_id=session_id, now=_now())
        return self._touch_result(record)

    # Revoke / list -----------------------------------------------------
    def revoke(
        self,
        *,
        org_id: str,
        session_id: str,
        reason: str | None = None,
    ) -> bool:
        revoked = self._store.revoke_session(
            org_id=org_id, session_id=session_id, reason=reason
        )
        if revoked:
            _LOGGER.info(
                "session_revoked session_id=%s org_id=%s reason=%s",
                session_id,
                org_id,
                reason or "",
            )
        return revoked

    def list_active(self, *, org_id: str, user_id: str) -> tuple[SessionRecord, ...]:
        return self._store.list_active_sessions(org_id=org_id, user_id=user_id)

    def mark_mfa_satisfied(
        self,
        *,
        session_id: str,
        promoted_scopes: tuple[str, ...] | None = None,
    ) -> bool:
        """Stamp ``mfa_satisfied_at`` (and optionally swap the
        ``mfa:pending`` placeholder for the real scopes). Returns ``True``
        when a row was updated.

        Called by ``MfaService`` after a successful TOTP / WebAuthn /
        recovery-code verify so the next ``touch`` returns the satisfied
        state and protected routes 200 instead of 401.
        """

        return self._store.mark_mfa_satisfied(
            session_id=session_id,
            when=_now(),
            promoted_scopes=promoted_scopes,
        )

    # Sweeper -----------------------------------------------------------
    def sweep_expired(self) -> int:
        cutoff = _now() - timedelta(seconds=self._policy.retention_after_expiry_seconds)
        count = self._store.sweep_expired(before=cutoff)
        if count:
            _LOGGER.info("session_sweeper_purged count=%d cutoff=%s", count, cutoff)
        return count

    # Internals ---------------------------------------------------------
    def _sign_for(self, record: SessionRecord) -> str:
        payload: dict[str, Any] = {
            CLAIM_SID: record.session_id,
            CLAIM_ORG_ID: record.org_id,
            CLAIM_USER_ID: record.user_id,
            CLAIM_ROLES: list(record.roles),
            CLAIM_PERMISSION_SCOPES: list(record.permission_scopes),
            CLAIM_CONNECTOR_SCOPES: {
                connector: list(scopes)
                for connector, scopes in record.connector_scopes.items()
            },
            CLAIM_EXPIRES_AT: int(record.expires_at.timestamp()),
        }
        return _BearerCodec.encode(payload, self._auth_secret)

    def _resolve_ttl(self, ttl_seconds: int | None) -> int:
        if ttl_seconds is None:
            return self._policy.default_ttl_seconds
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        return min(ttl_seconds, self._policy.max_ttl_seconds)

    def _touch_result(self, record: SessionRecord) -> SessionTouchResult:
        return SessionTouchResult(
            session_id=record.session_id,
            org_id=record.org_id,
            user_id=record.user_id,
            roles=record.roles,
            permission_scopes=record.permission_scopes,
            connector_scopes=record.connector_scopes,
            mfa_satisfied=record.mfa_satisfied_at is not None,
            mfa_satisfied_at=record.mfa_satisfied_at,
            expires_at=record.expires_at,
        )


def audit_session_event(
    *,
    org_id: str,
    actor_user_id: str | None,
    action: str,
    metadata: dict[str, Any] | None = None,
    request_ip: str | None = None,
    user_agent: str | None = None,
) -> IdentityAuditEventRecord:
    """Build an ``IdentityAuditEventRecord`` for a session-lifecycle event.

    Caller persists via ``IdentityStore.append_identity_audit``. Kept as a
    free factory so the SessionService doesn't need a write-coupling to the
    identity store; the routes layer composes the two inside one transaction.
    """

    return IdentityAuditEventRecord(
        org_id=org_id,
        actor_user_id=actor_user_id,
        subject_user_id=actor_user_id,
        action=action,
        metadata=metadata or {},
        request_ip=request_ip,
        user_agent=user_agent,
    )
