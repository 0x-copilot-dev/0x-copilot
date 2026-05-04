"""Authentication helpers for the product-facing facade."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import os
import time
from threading import Lock
from typing import Any

import httpx
from enterprise_service_contracts.auth_claims import (
    CLAIM_SID,
    ENV_REQUIRE_SESSION_BINDING,
)
from enterprise_service_contracts.headers import (
    AUTH_HEADER,
    CONNECTOR_SCOPES_HEADER,
    ORG_HEADER,
    PERMISSION_SCOPES_HEADER,
    ROLES_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import HTTPException, Request, status


# Per-process cache of canonical session identities returned by the backend
# touch endpoint. Spec A2 §2.4: "Per-request cache: a small LRU(maxsize=128)
# keyed on (token_hash, current_minute_bucket) — capped TTL 30s. Strictly
# per-process; no shared cache." The bucket trick keeps cache invalidation
# implicit — as the wall clock crosses the next 30s boundary the old key is
# never read again and falls out of the LRU naturally.
_TOUCH_CACHE_TTL_SECONDS = 30
_TOUCH_CACHE_MAX_SIZE = 128


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Request identity derived from a verified enterprise auth token."""

    org_id: str
    user_id: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ()
    connector_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Populated by ``verify_with_touch`` when the backend touch returns a
    # ``mfa_satisfied_at`` timestamp. The HMAC-only path leaves this None
    # since back-compat tokens carry no session row.
    mfa_satisfied_at: datetime | None = None
    # ``sid`` claim from the bearer (None for back-compat / dev-bypass
    # tokens). ``requires_recent_mfa`` uses this to decide whether the
    # caller is session-bound — step-up gates only fire on real
    # sessions, not on the dev bypass path.
    session_id: str | None = None

    def scoped_params(
        self, extra: dict[str, object] | None = None
    ) -> dict[str, object]:
        params: dict[str, object] = {"org_id": self.org_id, "user_id": self.user_id}
        if extra:
            params.update(extra)
        return params

    def scoped_payload(
        self,
        payload: dict[str, object] | None = None,
        *,
        include_request_context: bool = False,
    ) -> dict[str, object]:
        scoped = dict(payload or {})
        scoped["org_id"] = self.org_id
        scoped["user_id"] = self.user_id
        scoped.pop("runtime_context", None)
        if include_request_context:
            scoped["request_context"] = {
                **dict(
                    scoped.get("request_context")
                    if isinstance(scoped.get("request_context"), dict)
                    else {}
                ),
                "roles": self.roles,
                "permission_scopes": self.permission_scopes,
                "connector_scopes": self.connector_scopes,
            }
        return scoped


class _TouchCache:
    """TTL-bucketed LRU for the canonical identity returned by the backend
    session touch endpoint.

    Key = ``(token_hash, time_bucket)`` where ``time_bucket = floor(now /
    TTL_SECONDS)``. Crossing a bucket boundary forces a fresh touch even if
    the LRU still has the prior bucket; in steady state each session pays
    one touch per ``TTL_SECONDS`` window. Size-bounded so a flood of
    distinct tokens cannot push working sessions out — oldest evict first.

    Reusable: sensitive routes that need DB-backed instant revocation pass
    ``cache_bypass=True``; read-mostly routes amortise touch cost.
    """

    def __init__(
        self,
        *,
        max_size: int = _TOUCH_CACHE_MAX_SIZE,
        ttl_seconds: int = _TOUCH_CACHE_TTL_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._ttl_seconds = max(1, ttl_seconds)
        self._entries: OrderedDict[tuple[str, int], AuthenticatedIdentity] = (
            OrderedDict()
        )
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def _bucket(self, *, now: float | None = None) -> int:
        return int((now if now is not None else time.time()) // self._ttl_seconds)

    def get(
        self, *, token_hash: str, now: float | None = None
    ) -> AuthenticatedIdentity | None:
        key = (token_hash, self._bucket(now=now))
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    def put(
        self,
        *,
        token_hash: str,
        identity: AuthenticatedIdentity,
        now: float | None = None,
    ) -> None:
        key = (token_hash, self._bucket(now=now))
        with self._lock:
            self._entries[key] = identity
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)

    def invalidate(self, *, token_hash: str) -> None:
        """Drop every bucketed entry for this token. Called on logout/revoke
        so the immediately-following request never sees a stale identity."""

        with self._lock:
            doomed = [key for key in self._entries if key[0] == token_hash]
            for key in doomed:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0


# Module-level singleton; bound per worker process. Tests can clear via
# ``FacadeAuthenticator.touch_cache().clear()`` between cases.
_TOUCH_CACHE = _TouchCache()


class SessionRevoked(HTTPException):
    """Raised when the backend touch endpoint reports the session is no longer
    active (revoked, expired, or replaced). Mapped to 401 by FastAPI."""

    def __init__(self, detail: str = "Session no longer active") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class FacadeAuthenticator:
    """Class-scoped auth behavior for the product-facing facade."""

    @classmethod
    def authenticate_request(cls, request: Request) -> AuthenticatedIdentity:
        """Validate the client bearer token and return trusted identity claims."""

        header = request.headers.get(AUTH_HEADER, "")
        if not header.lower().startswith("bearer "):
            if cls._is_dev_auth_bypass_enabled():
                return cls._development_identity()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
        token = header.split(" ", maxsplit=1)[1].strip()
        return cls.verify_identity_token(token, cls._auth_secret())

    @classmethod
    async def verify_with_touch(
        cls,
        request: Request,
        *,
        backend_url: str,
        http_client: httpx.AsyncClient,
        cache_bypass: bool = False,
    ) -> AuthenticatedIdentity:
        """HMAC-verify locally → call backend touch (cached) → canonical identity.

        The backend's ``sessions`` row is the source of truth; roles, scopes,
        and revocation state come from there, not from the bearer payload.
        Pass ``cache_bypass=True`` from sensitive routes (admin, revoke,
        logout) so the touch happens even within the cache window.

        Falls back to the sync ``authenticate_request`` path for back-compat
        bearers that lack a ``sid`` claim — those carry no server-side
        session to touch and behave exactly as before A2.
        """

        identity = cls.authenticate_request(request)
        bearer = _bearer_from_authorization_header(request)
        if bearer is None:
            # Dev-bypass path. Nothing to touch.
            return identity
        sid = cls.session_id_from_token(bearer)
        if sid is None:
            # Back-compat token without a `sid` claim. The session-binding
            # gate in ``verify_identity_token`` already enforced the policy.
            return identity
        token_hash = cls.token_hash_from_signature(bearer)
        if not cache_bypass:
            cached = _TOUCH_CACHE.get(token_hash=token_hash)
            if cached is not None:
                return cached

        response = await http_client.post(
            f"{backend_url}/internal/v1/auth/sessions/touch",
            json={"session_id": sid, "token_hash": token_hash},
            headers={
                SERVICE_TOKEN_HEADER: cls._service_token(),
                ORG_HEADER: identity.org_id,
                USER_HEADER: identity.user_id,
            },
            timeout=5.0,
        )
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            _TOUCH_CACHE.invalidate(token_hash=token_hash)
            raise SessionRevoked()
        if response.status_code >= 400:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Backend session-touch upstream returned an error",
            )
        body = response.json() if response.content else {}
        canonical = AuthenticatedIdentity(
            org_id=str(body.get("org_id") or identity.org_id),
            user_id=str(body.get("user_id") or identity.user_id),
            roles=tuple(body.get("roles") or identity.roles),
            permission_scopes=tuple(
                body.get("permission_scopes") or identity.permission_scopes
            ),
            connector_scopes={
                str(connector): tuple(scopes or ())
                for connector, scopes in (body.get("connector_scopes") or {}).items()
            },
            mfa_satisfied_at=_parse_iso_timestamp(body.get("mfa_satisfied_at")),
            session_id=sid,
        )
        _TOUCH_CACHE.put(token_hash=token_hash, identity=canonical)
        return canonical

    @staticmethod
    def touch_cache() -> _TouchCache:
        """Expose the per-process touch cache (mainly for tests / metrics)."""

        return _TOUCH_CACHE

    @staticmethod
    def invalidate_touch_cache(token_hash: str) -> None:
        """Drop a token's cached identity — used by logout/revoke routes."""

        _TOUCH_CACHE.invalidate(token_hash=token_hash)

    @classmethod
    def service_headers(cls, identity: AuthenticatedIdentity) -> dict[str, str]:
        """Return service-to-service headers for upstream requests."""

        return {
            SERVICE_TOKEN_HEADER: cls._service_token(),
            ORG_HEADER: identity.org_id,
            USER_HEADER: identity.user_id,
            ROLES_HEADER: ",".join(identity.roles),
            PERMISSION_SCOPES_HEADER: ",".join(identity.permission_scopes),
            CONNECTOR_SCOPES_HEADER: json.dumps(
                identity.connector_scopes, separators=(",", ":")
            ),
        }

    @classmethod
    def verify_identity_token(cls, token: str, secret: str) -> AuthenticatedIdentity:
        """Verify a compact HMAC-signed JSON identity token."""

        try:
            payload_part, signature_part = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Malformed bearer token"
            ) from exc
        expected = cls._sign(payload_part.encode("ascii"), secret)
        if not hmac.compare_digest(signature_part, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
        try:
            payload = json.loads(cls._b64decode(payload_part).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid bearer token payload"
            ) from exc
        identity = cls._identity_from_payload(payload)
        # When REQUIRE_SESSION_BINDING is on, a bearer issued by an A2-aware
        # path (dev-mint or future A3..A5 logins) carries a `sid` claim. We
        # reject any bearer that lacks one so the externally-minted-token
        # back-door is closed. The full per-request DB touch lives in
        # ``verify_with_touch``; this static check is the cheap reject before
        # any backend round-trip.
        if cls._require_session_binding() and not _has_sid_claim(payload):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Bearer token must carry a session id (sid) claim",
            )
        return identity

    @classmethod
    def session_id_from_token(cls, token: str) -> str | None:
        """Return the `sid` claim from an HMAC-verified token, or None.

        Caller must already have verified the HMAC (e.g. via
        ``verify_identity_token``) so this routine does not re-validate the
        signature. Returns ``None`` when the claim is absent or malformed.
        """

        try:
            payload_part, _ = token.split(".", maxsplit=1)
            payload_obj = json.loads(cls._b64decode(payload_part).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload_obj, dict):
            return None
        sid = payload_obj.get(CLAIM_SID)
        return sid if isinstance(sid, str) and sid else None

    @classmethod
    def token_hash_from_signature(cls, token: str) -> str:
        """sha256 of the token's signature half.

        Used by ``/v1/auth/logout`` and the per-request touch path to
        identify the session row without exposing the bearer payload.
        """

        try:
            _, signature_part = token.split(".", maxsplit=1)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Malformed bearer token"
            ) from exc
        return hashlib.sha256(signature_part.encode("ascii")).hexdigest()

    @classmethod
    def _identity_from_payload(cls, payload: object) -> AuthenticatedIdentity:
        if not isinstance(payload, dict):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid bearer token payload"
            )
        org_id = cls._nonempty_str(payload.get("org_id"), "org_id")
        user_id = cls._nonempty_str(payload.get("user_id"), "user_id")
        roles = cls._string_tuple(payload.get("roles") or ("employee",))
        permission_scopes = cls._string_tuple(payload.get("permission_scopes") or ())
        connector_scopes = cls._connector_scopes(payload.get("connector_scopes") or {})
        return AuthenticatedIdentity(
            org_id=org_id,
            user_id=user_id,
            roles=roles,
            permission_scopes=permission_scopes,
            connector_scopes=connector_scopes,
        )

    @classmethod
    def _auth_secret(cls) -> str:
        return cls._required_secret("ENTERPRISE_AUTH_SECRET")

    @classmethod
    def _service_token(cls) -> str:
        value = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if value:
            return value
        if cls._environment() != "production":
            return ""
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ENTERPRISE_SERVICE_TOKEN is not configured",
        )

    @classmethod
    def _development_identity(cls) -> AuthenticatedIdentity:
        return AuthenticatedIdentity(
            org_id=os.environ.get("FACADE_DEV_ORG_ID", "org_123").strip() or "org_123",
            user_id=os.environ.get("FACADE_DEV_USER_ID", "user_123").strip()
            or "user_123",
            roles=("employee",),
            permission_scopes=("runtime:use",),
            connector_scopes={},
        )

    @staticmethod
    def _environment() -> str:
        return os.environ.get("FACADE_ENVIRONMENT", "development").strip().lower()

    @classmethod
    def _is_dev_auth_bypass_enabled(cls) -> bool:
        # Two gates:
        #  1. ``FACADE_ENVIRONMENT=development`` — the legacy gate.
        #  2. The deployment profile must allow it. Production profiles
        #     (``single_tenant_managed`` / ``single_tenant_self_hosted``) keep
        #     ``dev_auth_bypass_allowed=False`` even when the env claims to be
        #     development, so a leaked dev env var cannot accidentally relax
        #     auth in a regulated deploy.
        if cls._environment() != "development":
            return False
        if os.environ.get("DEV_AUTH_BYPASS", "").strip().lower() != "true":
            return False
        return cls._deployment_allows_dev_bypass()

    @staticmethod
    def _deployment_allows_dev_bypass() -> bool:
        # Imported lazily so the auth module stays free of import-time side
        # effects; profile loading touches env vars and is exercised via the
        # ``app.state.deployment`` cache in normal request flow.
        from backend_facade.deployment_profile import (
            DeploymentProfileError,
            DeploymentProfileLoader,
        )

        try:
            return DeploymentProfileLoader.load().toggles.dev_auth_bypass_allowed
        except DeploymentProfileError:
            # If the profile itself is misconfigured, fail closed: refuse
            # bypass. The profile loader at app startup is the canonical place
            # to surface the configuration error to the operator.
            return False

    @classmethod
    def _require_session_binding(cls) -> bool:
        return os.environ.get(ENV_REQUIRE_SESSION_BINDING, "").strip().lower() == "true"

    @classmethod
    def _required_secret(cls, name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} is not configured"
            )
        return value

    @classmethod
    def _sign(cls, payload: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
        return cls._b64encode(digest)

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))

    @staticmethod
    def _nonempty_str(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, f"Missing {field_name} claim"
            )
        return value.strip()

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list | tuple | set):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Identity claim must be a list"
            )
        normalized = tuple(str(item).strip() for item in value if str(item).strip())
        return normalized

    @classmethod
    def _connector_scopes(cls, value: object) -> dict[str, tuple[str, ...]]:
        if not isinstance(value, dict):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "connector_scopes must be an object"
            )
        return {
            str(connector): cls._string_tuple(scopes)
            for connector, scopes in value.items()
        }


def _has_sid_claim(payload: dict[str, Any]) -> bool:
    sid = payload.get(CLAIM_SID)
    return isinstance(sid, str) and bool(sid)


def _bearer_from_authorization_header(request: Request) -> str | None:
    header = request.headers.get(AUTH_HEADER, "")
    if not header.lower().startswith("bearer "):
        return None
    return header.split(" ", maxsplit=1)[1].strip() or None


def _parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # ``datetime.fromisoformat`` accepts ``+00:00`` and ``Z``; backend
        # currently emits the former via Pydantic, but we tolerate both.
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Step-up MFA gate (A6 §2.4)
# ---------------------------------------------------------------------------


class StepUpRequired(HTTPException):
    """Raised when a route demands a recent MFA verify and the session
    either never satisfied MFA or did so outside the allowed window. The
    ``WWW-Authenticate: x-step-up`` header tells the frontend to prompt
    for a fresh second factor without invalidating the session itself."""

    def __init__(
        self,
        *,
        max_age_seconds: int,
        elapsed_seconds: int | None,
    ) -> None:
        detail = (
            "step-up MFA required: re-verify your second factor "
            f"(window={max_age_seconds}s, "
            f"elapsed={'never' if elapsed_seconds is None else f'{elapsed_seconds}s'})"
        )
        super().__init__(
            status_code=403,
            detail=detail,
            headers={
                "WWW-Authenticate": (
                    f'x-step-up max_age="{max_age_seconds}", realm="enterprise-search"'
                ),
            },
        )


def requires_recent_mfa(
    identity: AuthenticatedIdentity,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> None:
    """Raise ``StepUpRequired`` if the caller's session hasn't satisfied
    MFA within ``max_age_seconds``. Routes that need step-up wrap their
    ``verify_with_touch`` call and immediately call this helper.

    Why a free function instead of a FastAPI ``Depends``: the route
    already drives ``verify_with_touch`` with its own ``http_client`` +
    ``cache_bypass`` knobs; a Depends would have to duplicate that
    machinery. Easier to compose explicitly.
    """

    if max_age_seconds <= 0:
        return
    if identity.session_id is None:
        # Back-compat / dev-bypass tokens have no server-side session row
        # to consult. Step-up is meaningless until ``REQUIRE_SESSION_BINDING``
        # is on, at which point ``verify_identity_token`` will reject these
        # tokens before they reach this guard.
        return
    satisfied = identity.mfa_satisfied_at
    current = now if now is not None else datetime.now(timezone.utc)
    if satisfied is None:
        raise StepUpRequired(max_age_seconds=max_age_seconds, elapsed_seconds=None)
    elapsed = int((current - satisfied).total_seconds())
    if elapsed > max_age_seconds:
        raise StepUpRequired(
            max_age_seconds=max_age_seconds,
            elapsed_seconds=elapsed,
        )
