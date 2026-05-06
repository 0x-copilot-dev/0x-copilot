"""Login email-first services (PR 5.1).

Three services live here:

* :class:`DiscoveryService` — ``email`` → IdP routing decision. Public
  endpoint, anti-enumeration response shape.
* :class:`MagicLinkService` — ``request`` (issue + email) and ``consume``
  (mint a session, or issue a workspace-pick token if the email maps to
  multiple orgs). Always 202 on request, regardless of whether the
  email exists.
* :class:`SessionSelectService` — exchange a workspace-pick token + chosen
  ``org_id`` for a final session bearer.

Privileged code paths reused unchanged: :class:`SessionService` mints
the session, :class:`MfaService` evaluates whether MFA is required,
:class:`IdentityStore` records audit + login_attempts, :class:`TokenVault`
is unused (no secrets in flight beyond the magic-link plaintext, which
is hashed at rest).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend_app.contracts import (
    AuthDiscoverKind,
    AuthDiscoverRequest,
    AuthDiscoverResponse,
    AuthProviderKind,
    IdentityAuditEventRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    MagicLinkCallbackOutcome,
    MagicLinkCallbackRequest,
    MagicLinkCallbackResult,
    MagicLinkStartRequest,
    MagicLinkStartResponse,
    MagicLinkTokenRecord,
    SessionMintResult,
    SessionSelectRequest,
    SessionSelectResult,
    UserStatus,
    WorkspaceCandidate,
)
from backend_app.identity.email_dispatcher import (
    EmailDispatcherPort,
    LoggingEmailDispatcher,
)
from backend_app.identity.login_email_first_store import (
    AuthProviderDomainStore,
    MagicLinkTokenStore,
)
from backend_app.identity.sessions import SessionService
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)


# Defaults — honoured by ``from_env`` constructors.
_MAGIC_LINK_TTL_SECONDS = 15 * 60
_PICK_TOKEN_TTL_SECONDS = 5 * 60
_DISCOVER_RATE_PER_MIN = 30
_MAGIC_LINK_START_RATE_PER_MIN_IP = 5
_MAGIC_LINK_START_RATE_PER_HOUR_EMAIL = 3
_MAGIC_LINK_CALLBACK_RATE_PER_MIN_IP = 20
_PICK_RATE_PER_MIN = 10

# Well-known consumer-domain → personal-provider mapping. Sourced from the
# prototype (login-page.jsx); deploys can extend via ``personal_domains``
# constructor arg if needed. Discovery returns ``kind=personal`` for any
# match; the routing is the same as ``kind=magic_link`` (anti-enumeration).
_DEFAULT_PERSONAL_DOMAINS: dict[str, str] = {
    "gmail.com": "Google",
    "googlemail.com": "Google",
    "icloud.com": "Apple ID",
    "me.com": "Apple ID",
    "outlook.com": "Microsoft",
    "hotmail.com": "Microsoft",
    "yahoo.com": "Yahoo",
    "proton.me": "Proton",
    "protonmail.com": "Proton",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _domain_of(email: str) -> str:
    at = email.find("@")
    if at < 0:
        return ""
    return email[at + 1 :].strip().lower()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DiscoveryRateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int = 30) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


class MagicLinkRateLimited(DiscoveryRateLimited):
    pass


class MagicLinkInvalidToken(RuntimeError):
    def __init__(self, reason: str = "invalid_token") -> None:
        super().__init__(reason)
        self.reason = reason


class PickTokenInvalid(RuntimeError):
    def __init__(self, reason: str = "invalid_pick_token") -> None:
        super().__init__(reason)
        self.reason = reason


class WorkspaceMembershipDenied(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Rate limiter — small in-memory window, per-key
# ---------------------------------------------------------------------------


@dataclass
class _Window:
    """Per-key sliding window. Mutates in place under a single thread per
    process; FastAPI workers are sync per-request which is enough.

    For multi-worker deploys we'd swap for the existing
    ``LockoutStore`` row but the v1 design uses this in-process limiter so
    the discovery surface has zero new persistence dependency.
    """

    window_seconds: int
    limit: int
    timestamps: deque[float]


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Window] = {}

    def _bucket(
        self, *, scope: str, key: str, window_seconds: int, limit: int
    ) -> _Window:
        bucket = self._buckets.get((scope, key))
        if bucket is None:
            bucket = _Window(
                window_seconds=window_seconds, limit=limit, timestamps=deque()
            )
            self._buckets[(scope, key)] = bucket
        return bucket

    def hit(self, *, scope: str, key: str, window_seconds: int, limit: int) -> int:
        """Returns 0 on success or the number of seconds to wait on rate-limit."""

        now = time.monotonic()
        bucket = self._bucket(
            scope=scope, key=key, window_seconds=window_seconds, limit=limit
        )
        cutoff = now - window_seconds
        while bucket.timestamps and bucket.timestamps[0] < cutoff:
            bucket.timestamps.popleft()
        if len(bucket.timestamps) >= limit:
            oldest = bucket.timestamps[0]
            return max(1, int(window_seconds - (now - oldest)) + 1)
        bucket.timestamps.append(now)
        return 0


# ---------------------------------------------------------------------------
# Magic-link token codec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MagicLinkPlaintext:
    """The plaintext value the user receives in the email URL.

    Wire shape: ``base64url(secret_bytes)``. The server's lookup key is
    ``sha256(plaintext)`` — the same shape SCIM tokens (``0015_scim``) and
    the invitation token (``0019_invitations``) use.
    """

    plaintext: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.plaintext.encode("ascii")).hexdigest()

    @classmethod
    def generate(cls) -> "_MagicLinkPlaintext":
        # 32 bytes = 256 bits of entropy. Base64url encoding produces 43
        # printable characters with no padding.
        raw = secrets.token_urlsafe(32)
        return cls(plaintext=raw)


# ---------------------------------------------------------------------------
# Pick-token codec — HMAC over a small claim set
# ---------------------------------------------------------------------------


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


@dataclass(frozen=True)
class _PickTokenCodec:
    """HMAC-signed claim set for the workspace-pick step.

    The token carries everything needed to re-issue a session WITHOUT
    requiring server-side state: ``user_id``, the candidate ``org_ids``
    the user is a member of (snapshotted at issue time), and an ``exp``.
    The signature gates tampering; the claim set gates org probing.
    """

    secret: str

    def encode(self, *, user_id: str, candidate_orgs: tuple[str, ...]) -> str:
        exp = int(_now().timestamp()) + _PICK_TOKEN_TTL_SECONDS
        payload: dict[str, Any] = {
            "user_id": user_id,
            "orgs": list(candidate_orgs),
            "exp": exp,
            # token_id makes the row forensically distinct (audit logging
            # via login_attempts.user_id + this id).
            "tid": f"pick_{secrets.token_hex(8)}",
        }
        canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        payload_b64 = _b64url_encode(canonical.encode("utf-8"))
        signature = hmac.new(
            self.secret.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{payload_b64}.{_b64url_encode(signature)}"

    def decode(self, token: str) -> dict[str, Any]:
        try:
            payload_b64, signature_b64 = token.split(".", 1)
        except ValueError as exc:
            raise PickTokenInvalid("malformed pick_token") from exc
        expected = hmac.new(
            self.secret.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature_b64, _b64url_encode(expected)):
            raise PickTokenInvalid("invalid pick_token signature")
        try:
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise PickTokenInvalid("invalid pick_token payload") from exc
        if not isinstance(payload, dict):
            raise PickTokenInvalid("invalid pick_token payload shape")
        if int(payload.get("exp", 0)) < int(_now().timestamp()):
            raise PickTokenInvalid("expired_pick_token")
        if not isinstance(payload.get("user_id"), str):
            raise PickTokenInvalid("invalid pick_token payload shape")
        orgs = payload.get("orgs")
        if not isinstance(orgs, list) or not all(isinstance(o, str) for o in orgs):
            raise PickTokenInvalid("invalid pick_token payload shape")
        return payload


# ---------------------------------------------------------------------------
# DiscoveryService
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryService:
    """Resolve ``email → IdP routing`` for the login surface.

    Lookups: ``auth_provider_domains`` table (claimed) + the in-process
    ``personal_domains`` map (well-known consumer providers). Bank-deploy
    profile (``magic_link_globally_enabled=False``) returns
    ``kind='unknown'`` for personal / unmapped domains, blocking the
    magic-link path; the FE renders the ``message`` field as the reason.
    """

    domain_store: AuthProviderDomainStore
    identity_store: IdentityStore
    rate_limiter: InMemoryRateLimiter
    magic_link_globally_enabled: bool = True
    personal_domains: dict[str, str] | None = None

    def discover(
        self,
        request: AuthDiscoverRequest,
    ) -> AuthDiscoverResponse:
        retry = self.rate_limiter.hit(
            scope="auth.discover",
            key=request.ip or "anon",
            window_seconds=60,
            limit=_DISCOVER_RATE_PER_MIN,
        )
        if retry:
            raise DiscoveryRateLimited(retry_after_seconds=retry)

        domain = _domain_of(request.email)
        if not domain or "." not in domain:
            return AuthDiscoverResponse(
                kind=AuthDiscoverKind.UNKNOWN,
                domain=None,
                magic_link_supported=False,
                message="invalid_email",
            )

        # 1. Workspace claim — most specific, wins over personal-domain
        #    suffix matches (an admin can claim gmail.com for a workspace
        #    that requires SSO of personal accounts; SSO wins).
        active = self.domain_store.get_active_by_domain(domain=domain)
        if active:
            # If multiple orgs claim the same domain, pick the SSO-enforced
            # one first; otherwise the first row wins. Multi-claim is
            # uncommon — see PRD §3.8 edge case.
            chosen = sorted(active, key=lambda r: (not r.sso_enforced, r.created_at))[0]
            provider = self.identity_store.get_auth_provider_by_id(chosen.provider_id)
            org = self.identity_store.get_organization(org_id=chosen.org_id)
            if provider is None or org is None or not provider.enabled:
                # Provider was disabled or org soft-deleted — fall through
                # to the unknown / magic-link branch as if the claim wasn't
                # there. Re-claim is an admin task.
                return self._unknown_or_magic_link(domain=domain)
            members = self.identity_store.list_members(org_id=org.org_id)
            return AuthDiscoverResponse(
                kind=AuthDiscoverKind.SSO,
                domain=domain,
                org_id=org.org_id,
                org_display_name=org.display_name,
                org_logo_url=(org.metadata or {}).get("logo_url")
                if isinstance(org.metadata, dict)
                else None,
                member_count=len(members),
                provider_id=provider.provider_id,
                provider_kind=AuthProviderKind(provider.kind),
                provider_display_name=provider.display_name,
                sso_enforced=chosen.sso_enforced,
                magic_link_supported=not chosen.sso_enforced
                and self.magic_link_globally_enabled,
            )

        # 2. Well-known consumer domain → personal magic-link (unless the
        #    deploy disables magic-link globally).
        personal_map = self.personal_domains or _DEFAULT_PERSONAL_DOMAINS
        if domain in personal_map:
            if not self.magic_link_globally_enabled:
                return AuthDiscoverResponse(
                    kind=AuthDiscoverKind.UNKNOWN,
                    domain=domain,
                    magic_link_supported=False,
                    message=(
                        "Your workspace requires single sign-on. Contact your admin."
                    ),
                )
            return AuthDiscoverResponse(
                kind=AuthDiscoverKind.PERSONAL,
                domain=domain,
                provider_kind=None,  # the FE branches on kind, not provider_kind
                provider_display_name=personal_map[domain],
                magic_link_supported=True,
            )

        # 3. Unknown domain — magic-link fallback (or hard-no in bank deploy).
        return self._unknown_or_magic_link(domain=domain)

    def _unknown_or_magic_link(self, *, domain: str) -> AuthDiscoverResponse:
        if not self.magic_link_globally_enabled:
            return AuthDiscoverResponse(
                kind=AuthDiscoverKind.UNKNOWN,
                domain=domain,
                magic_link_supported=False,
                message="Your workspace requires single sign-on. Contact your admin.",
            )
        return AuthDiscoverResponse(
            kind=AuthDiscoverKind.MAGIC_LINK,
            domain=domain,
            magic_link_supported=True,
        )


# ---------------------------------------------------------------------------
# MagicLinkService
# ---------------------------------------------------------------------------


@dataclass
class MagicLinkService:
    """Issue and consume one-time email-link tokens.

    ``request`` is the public POST that **always** returns 202. A row is
    inserted only when the email maps to one or more existing active
    users; otherwise we skip both the row write and the dispatch — the
    response shape is identical, the absence is the only signal.

    ``consume`` is the GET on the token. Single-workspace users get a
    session bearer immediately; multi-workspace users get a HMAC-signed
    pick token + a frozen list of candidate workspaces.
    """

    token_store: MagicLinkTokenStore
    identity_store: IdentityStore
    sessions: SessionService
    pick_codec: _PickTokenCodec
    rate_limiter: InMemoryRateLimiter
    email_dispatcher: EmailDispatcherPort
    base_url: str  # e.g. "https://app.example.com"
    magic_link_globally_enabled: bool = True

    # request --------------------------------------------------------------
    def request(self, payload: MagicLinkStartRequest) -> MagicLinkStartResponse:
        if not self.magic_link_globally_enabled:
            # Bank-profile deploys: log an attempt for forensic value but
            # never queue. Same anti-enumeration shape on the wire.
            self._record_attempt(
                org_id=None,
                email=payload.email,
                user_id=None,
                outcome=LoginAttemptOutcome.RATE_LIMITED,
                ip=payload.ip,
                user_agent=payload.user_agent,
                failure_reason="magic_link_globally_disabled",
            )
            return MagicLinkStartResponse()

        retry = self.rate_limiter.hit(
            scope="auth.magic_link.start.ip",
            key=payload.ip or "anon",
            window_seconds=60,
            limit=_MAGIC_LINK_START_RATE_PER_MIN_IP,
        )
        if retry:
            raise MagicLinkRateLimited(retry_after_seconds=retry)
        retry_email = self.rate_limiter.hit(
            scope="auth.magic_link.start.email",
            key=payload.email,
            window_seconds=60 * 60,
            limit=_MAGIC_LINK_START_RATE_PER_HOUR_EMAIL,
        )
        if retry_email:
            raise MagicLinkRateLimited(retry_after_seconds=retry_email)

        users = tuple(
            u
            for u in self.identity_store.list_users_by_email(email=payload.email)
            if u.status == UserStatus.ACTIVE
        )
        if not users:
            # Anti-enumeration: no row, no email, identical 202.
            self._record_attempt(
                org_id=None,
                email=payload.email,
                user_id=None,
                outcome=LoginAttemptOutcome.UNKNOWN_USER,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            return MagicLinkStartResponse()

        # The email may map to multiple workspaces; we pin one row per
        # user but freeze the candidate-orgs list inside it so the
        # consumer doesn't have to re-query at click time. Pick the
        # earliest-created user as the "primary" key for the row; the
        # other memberships travel inside ``candidate_orgs``.
        primary = users[0]
        candidate_orgs = tuple(self._candidate_for(user) for user in users)
        token = _MagicLinkPlaintext.generate()
        record = MagicLinkTokenRecord(
            org_id=primary.org_id if len(users) == 1 else None,
            user_id=primary.user_id,
            email_lower=payload.email,
            token_hash=token.hash,
            candidate_orgs=[c.model_dump(mode="json") for c in candidate_orgs],
            return_to=payload.return_to,
            requested_ip=payload.ip,
            requested_ua=payload.user_agent,
            expires_at=_now() + timedelta(seconds=_MAGIC_LINK_TTL_SECONDS),
        )
        self.token_store.create(record)
        self._record_attempt(
            org_id=primary.org_id,
            email=payload.email,
            user_id=primary.user_id,
            outcome=LoginAttemptOutcome.MAGIC_LINK_REQUESTED,
            ip=payload.ip,
            user_agent=payload.user_agent,
        )
        self._audit(
            org_id=primary.org_id,
            actor_user_id=primary.user_id,
            action="auth.magic_link.requested",
            metadata={
                "email_hash": _email_hash(payload.email),
                "candidate_orgs": [c.org_id for c in candidate_orgs],
                "return_to_present": payload.return_to is not None,
            },
            ip=payload.ip,
            user_agent=payload.user_agent,
        )
        # Build the URL the user receives. The plaintext travels in the
        # query string because email clients can't post; the server
        # validates by hashing on the way back in.
        login_url = self._build_login_url(token=token.plaintext)
        org_for_dispatch = primary.org_id if len(users) == 1 else None
        org_display_name = None
        if org_for_dispatch is not None:
            org = self.identity_store.get_organization(org_id=org_for_dispatch)
            org_display_name = org.display_name if org is not None else None
        try:
            self.email_dispatcher.send_magic_link(
                to_email=payload.email,
                org_display_name=org_display_name,
                login_url=login_url,
                expires_minutes=_MAGIC_LINK_TTL_SECONDS // 60,
                request_ip=payload.ip,
                request_user_agent=payload.user_agent,
            )
        except Exception:  # pragma: no cover — port contract says no raise
            _LOGGER.exception("email dispatcher raised; ignoring (response is 202)")
        return MagicLinkStartResponse()

    # consume --------------------------------------------------------------
    def consume(self, payload: MagicLinkCallbackRequest) -> MagicLinkCallbackResult:
        retry = self.rate_limiter.hit(
            scope="auth.magic_link.callback",
            key=payload.ip or "anon",
            window_seconds=60,
            limit=_MAGIC_LINK_CALLBACK_RATE_PER_MIN_IP,
        )
        if retry:
            raise MagicLinkRateLimited(retry_after_seconds=retry)

        token_hash = hashlib.sha256(payload.token.encode("ascii")).hexdigest()
        record = self.token_store.get_by_hash(token_hash=token_hash)
        if record is None:
            self._record_attempt(
                org_id=None,
                email=None,
                user_id=None,
                outcome=LoginAttemptOutcome.INVALID_TOKEN,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            raise MagicLinkInvalidToken("invalid_token")
        if record.consumed_at is not None:
            self._record_attempt(
                org_id=record.org_id,
                email=record.email_lower,
                user_id=record.user_id,
                outcome=LoginAttemptOutcome.CONSUMED_TOKEN,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            raise MagicLinkInvalidToken("consumed_token")
        if record.expires_at < _now():
            self._record_attempt(
                org_id=record.org_id,
                email=record.email_lower,
                user_id=record.user_id,
                outcome=LoginAttemptOutcome.EXPIRED_TOKEN,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            raise MagicLinkInvalidToken("expired_token")

        # Re-check the user is still active. Cannot leak existence
        # because by definition the row exists ⇒ the user existed at
        # request time. The branch covers between-request deactivation.
        primary = self.identity_store.get_user(
            org_id=record.candidate_orgs[0]["org_id"], user_id=record.user_id
        )
        if primary is None or primary.status != UserStatus.ACTIVE:
            self.token_store.mark_consumed(
                token_id=record.token_id, consumed_session_id=None
            )
            self._record_attempt(
                org_id=record.org_id,
                email=record.email_lower,
                user_id=record.user_id,
                outcome=LoginAttemptOutcome.INVALID_TOKEN,
                ip=payload.ip,
                user_agent=payload.user_agent,
                failure_reason="user_disabled",
            )
            raise MagicLinkInvalidToken("invalid_token")

        # Single workspace → mint a session immediately.
        if len(record.candidate_orgs) == 1:
            org_id = record.candidate_orgs[0]["org_id"]
            user = self.identity_store.get_user(org_id=org_id, user_id=record.user_id)
            if user is None or user.status != UserStatus.ACTIVE:
                self.token_store.mark_consumed(
                    token_id=record.token_id, consumed_session_id=None
                )
                raise MagicLinkInvalidToken("invalid_token")
            mint = self._mint_session(
                org_id=org_id,
                user_id=user.user_id,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            self.token_store.mark_consumed(
                token_id=record.token_id, consumed_session_id=mint.session_id
            )
            self._record_attempt(
                org_id=org_id,
                email=record.email_lower,
                user_id=user.user_id,
                outcome=LoginAttemptOutcome.MAGIC_LINK_CONSUMED,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            self._audit(
                org_id=org_id,
                actor_user_id=user.user_id,
                action="auth.magic_link.consumed",
                metadata={
                    "token_id": record.token_id,
                    "outcome": "session_minted",
                    "candidate_orgs": [c["org_id"] for c in record.candidate_orgs],
                },
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
            return MagicLinkCallbackResult(
                outcome=MagicLinkCallbackOutcome.SESSION_MINTED,
                user_id=user.user_id,
                bearer_token=mint.bearer_token,
                session_id=mint.session_id,
                org_id=org_id,
                requires_mfa=False,  # MFA wired in PR 5.1.x; see notes
                return_to=record.return_to,
                expires_at=mint.expires_at,
            )

        # Multi-workspace → issue a pick token and surface the candidates.
        # The token is HMAC-signed so the server doesn't need to persist it
        # in the magic_link_tokens row (which is now consumed below).
        candidate_org_ids = tuple(c["org_id"] for c in record.candidate_orgs)
        pick_token = self.pick_codec.encode(
            user_id=record.user_id, candidate_orgs=candidate_org_ids
        )
        # Mark the magic-link row consumed; the pick_token is the next
        # ratchet. This prevents replay of the email-clicked URL after
        # the picker step starts.
        self.token_store.mark_consumed(
            token_id=record.token_id, consumed_session_id=None
        )
        self._record_attempt(
            org_id=None,
            email=record.email_lower,
            user_id=record.user_id,
            outcome=LoginAttemptOutcome.WORKSPACE_PICKER_ISSUED,
            ip=payload.ip,
            user_agent=payload.user_agent,
        )
        self._audit(
            org_id=primary.org_id,
            actor_user_id=record.user_id,
            action="auth.workspace_pick.issued",
            metadata={
                "token_id": record.token_id,
                "candidate_orgs": list(candidate_org_ids),
            },
            ip=payload.ip,
            user_agent=payload.user_agent,
        )
        return MagicLinkCallbackResult(
            outcome=MagicLinkCallbackOutcome.WORKSPACE_PICK_REQUIRED,
            user_id=record.user_id,
            pick_token=pick_token,
            expires_in_seconds=_PICK_TOKEN_TTL_SECONDS,
            workspaces=tuple(WorkspaceCandidate(**c) for c in record.candidate_orgs),
            return_to=record.return_to,
        )

    # helpers --------------------------------------------------------------
    def _build_login_url(self, *, token: str) -> str:
        sep = "&" if "?" in self.base_url else "?"
        # The frontend's magic-link callback page reads ``token`` and
        # POSTs it to /v1/auth/magic-link/callback.
        return f"{self.base_url}/auth/magic-link/callback{sep}token={token}"

    def _candidate_for(self, user: Any) -> WorkspaceCandidate:
        org = self.identity_store.get_organization(org_id=user.org_id)
        members = self.identity_store.list_members(org_id=user.org_id)
        # Best-effort role: the first non-revoked role assignment.
        role_records = self.identity_store.list_role_assignments(
            org_id=user.org_id, user_id=user.user_id
        )
        role = "Member"
        for assignment in role_records:
            if assignment.revoked_at is None:
                role_record = self.identity_store.get_role(role_id=assignment.role_id)
                if role_record is not None:
                    role = role_record.display_name or role_record.name or "Member"
                    break
        logo_url = None
        if org is not None and isinstance(org.metadata, dict):
            logo_url = org.metadata.get("logo_url")
        return WorkspaceCandidate(
            org_id=user.org_id,
            display_name=(org.display_name if org is not None else user.org_id),
            logo_url=logo_url,
            role=role,
            member_count=len(members),
            last_active_at=user.last_seen_at,
        )

    def _mint_session(
        self,
        *,
        org_id: str,
        user_id: str,
        ip: str | None,
        user_agent: str | None,
    ) -> SessionMintResult:
        return self.sessions.create(
            org_id=org_id,
            user_id=user_id,
            roles=("employee",),
            permission_scopes=("runtime:use",),
            connector_scopes=None,
            ttl_seconds=None,
            auth_provider_id=None,
            client_ip=ip,
            user_agent=user_agent,
            device_label="magic_link",
        )

    def _record_attempt(
        self,
        *,
        org_id: str | None,
        email: str | None,
        user_id: str | None,
        outcome: LoginAttemptOutcome,
        ip: str | None,
        user_agent: str | None,
        failure_reason: str | None = None,
    ) -> None:
        self.identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org_id,
                email_attempted=email,
                user_id=user_id,
                auth_kind=LoginAttemptKind.MAGIC_LINK,
                outcome=outcome,
                ip=ip,
                user_agent=user_agent,
                failure_reason=failure_reason,
            )
        )

    def _audit(
        self,
        *,
        org_id: str | None,
        actor_user_id: str | None,
        action: str,
        metadata: dict[str, Any],
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        # identity_audit_events.org_id is NOT NULL — for pre-pick events
        # (where we don't know the org yet) we skip the chain row and
        # rely on login_attempts as the operational record.
        if org_id is None:
            return
        self.identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org_id,
                actor_user_id=actor_user_id,
                subject_user_id=actor_user_id,
                action=action,
                metadata=metadata,
                request_ip=ip,
                user_agent=user_agent,
            )
        )


def _email_hash(email: str) -> str:
    """HMAC-SHA256 of the lower-cased email, hex-encoded.

    Audit chain stores the hash so forensic queries match without
    persisting plaintext. The key is the same one the rest of the
    service uses for HMAC; we don't introduce a separate hash key in v1.
    """

    import os

    secret = os.environ.get("ENTERPRISE_AUDIT_HASH_KEY") or os.environ.get(
        "ENTERPRISE_AUTH_SECRET", ""
    )
    if not secret:
        # Fall back to plain sha256 — not ideal but better than nothing
        # in dev mode when no secret is set.
        return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()
    return hmac.new(
        secret.encode("utf-8"),
        email.lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# SessionSelectService
# ---------------------------------------------------------------------------


@dataclass
class SessionSelectService:
    identity_store: IdentityStore
    sessions: SessionService
    pick_codec: _PickTokenCodec
    rate_limiter: InMemoryRateLimiter

    def select(self, payload: SessionSelectRequest) -> SessionSelectResult:
        retry = self.rate_limiter.hit(
            scope="auth.workspace_pick.select",
            key=payload.pick_token[:24],
            window_seconds=60,
            limit=_PICK_RATE_PER_MIN,
        )
        if retry:
            raise MagicLinkRateLimited(retry_after_seconds=retry)
        claims = self.pick_codec.decode(payload.pick_token)
        user_id = claims["user_id"]
        candidate_orgs = tuple(claims["orgs"])
        if payload.org_id not in candidate_orgs:
            self.identity_store.append_login_attempt(
                LoginAttemptRecord(
                    org_id=payload.org_id,
                    email_attempted=None,
                    user_id=user_id,
                    auth_kind=LoginAttemptKind.MAGIC_LINK,
                    outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                    ip=payload.ip,
                    user_agent=payload.user_agent,
                    failure_reason="not_a_member",
                )
            )
            raise WorkspaceMembershipDenied("not_a_member")
        user = self.identity_store.get_user(org_id=payload.org_id, user_id=user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise WorkspaceMembershipDenied("not_a_member")
        mint = self.sessions.create(
            org_id=payload.org_id,
            user_id=user_id,
            roles=("employee",),
            permission_scopes=("runtime:use",),
            connector_scopes=None,
            ttl_seconds=None,
            auth_provider_id=None,
            client_ip=payload.ip,
            user_agent=payload.user_agent,
            device_label="magic_link_workspace_select",
        )
        self.identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=payload.org_id,
                email_attempted=user.primary_email,
                user_id=user_id,
                auth_kind=LoginAttemptKind.MAGIC_LINK,
                outcome=LoginAttemptOutcome.WORKSPACE_SELECTED,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        )
        self.identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=payload.org_id,
                actor_user_id=user_id,
                subject_user_id=user_id,
                action="auth.workspace_pick.consumed",
                metadata={
                    "pick_token_id": claims.get("tid"),
                    "candidate_orgs": list(candidate_orgs),
                },
                request_ip=payload.ip,
                user_agent=payload.user_agent,
            )
        )
        return SessionSelectResult(
            bearer_token=mint.bearer_token,
            session_id=mint.session_id,
            user_id=user_id,
            org_id=payload.org_id,
            requires_mfa=False,
            expires_at=mint.expires_at,
        )


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------


def build_default_email_dispatcher() -> EmailDispatcherPort:
    return LoggingEmailDispatcher(logger=_LOGGER)


def build_pick_codec(*, secret: str) -> _PickTokenCodec:
    return _PickTokenCodec(secret=secret)


__all__ = [
    "DiscoveryService",
    "DiscoveryRateLimited",
    "InMemoryRateLimiter",
    "MagicLinkInvalidToken",
    "MagicLinkRateLimited",
    "MagicLinkService",
    "PickTokenInvalid",
    "SessionSelectService",
    "WorkspaceMembershipDenied",
    "build_default_email_dispatcher",
    "build_pick_codec",
    "_DEFAULT_PERSONAL_DOMAINS",
    "_MAGIC_LINK_TTL_SECONDS",
    "_PICK_TOKEN_TTL_SECONDS",
]
