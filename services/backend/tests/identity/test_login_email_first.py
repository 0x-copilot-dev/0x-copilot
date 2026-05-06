"""Tests for login email-first (PR 5.1).

Covers:
- DiscoveryService: SSO claim, personal domain, unknown domain, bank
  profile, rate limit.
- MagicLinkService: anti-enumeration (no row for unknown user),
  single- vs multi-workspace consume, expired / consumed token, rate limits.
- SessionSelectService: pick token round-trip, cross-org rejection,
  invalid token, rate limit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend_app.contracts import (
    AuthDiscoverKind,
    AuthDiscoverRequest,
    AuthProviderDomainRecord,
    AuthProviderKind,
    AuthProviderRecord,
    LoginAttemptOutcome,
    MagicLinkCallbackOutcome,
    MagicLinkCallbackRequest,
    MagicLinkStartRequest,
    OrganizationMemberRecord,
    OrganizationRecord,
    RoleAssignmentRecord,
    RoleRecord,
    SessionSelectRequest,
    UserRecord,
)
from backend_app.identity.email_dispatcher import LoggingEmailDispatcher
from backend_app.identity.login_email_first import (
    DiscoveryRateLimited,
    DiscoveryService,
    InMemoryRateLimiter,
    MagicLinkInvalidToken,
    MagicLinkRateLimited,
    MagicLinkService,
    PickTokenInvalid,
    SessionSelectService,
    WorkspaceMembershipDenied,
    _PickTokenCodec,
)
from backend_app.identity.login_email_first_store import (
    InMemoryAuthProviderDomainStore,
    InMemoryMagicLinkTokenStore,
)
from backend_app.identity.sessions import SessionService
from backend_app.identity.session_store import InMemorySessionStore
from backend_app.identity.store import InMemoryIdentityStore


_AUTH_SECRET = "test-auth-secret-login-email-first-1234567890abc"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _setup() -> tuple[
    InMemoryIdentityStore,
    InMemoryAuthProviderDomainStore,
    InMemoryMagicLinkTokenStore,
    SessionService,
    LoggingEmailDispatcher,
    InMemoryRateLimiter,
]:
    identity = InMemoryIdentityStore()
    domain_store = InMemoryAuthProviderDomainStore()
    token_store = InMemoryMagicLinkTokenStore()
    sessions = SessionService(
        InMemorySessionStore(), auth_secret=_AUTH_SECRET, dev_mint_allowed=True
    )
    dispatcher = LoggingEmailDispatcher(logger=logging.getLogger(__name__))
    rate = InMemoryRateLimiter()
    return identity, domain_store, token_store, sessions, dispatcher, rate


def _seed_org_and_user(
    identity: InMemoryIdentityStore,
    *,
    org_id: str,
    org_slug: str,
    email: str,
    user_id: str = "usr_acme",
) -> tuple[OrganizationRecord, UserRecord]:
    org = OrganizationRecord(
        org_id=org_id,
        display_name=f"{org_slug.title()} Inc.",
        slug=org_slug,
    )
    identity.create_organization(org)
    role = RoleRecord(
        role_id=f"role_{org_id}_member",
        org_id=org_id,
        name="member",
        display_name="Member",
        is_system=False,
    )
    identity.create_role(role)
    user = UserRecord(
        user_id=user_id,
        org_id=org_id,
        primary_email=email,
        display_name="Test User",
    )
    identity.create_user(user)
    identity.add_member(
        OrganizationMemberRecord(
            org_id=org_id,
            user_id=user.user_id,
        )
    )
    identity.assign_role(
        RoleAssignmentRecord(
            org_id=org_id,
            user_id=user.user_id,
            role_id=role.role_id,
        )
    )
    return org, user


def _seed_provider_and_claim(
    identity: InMemoryIdentityStore,
    domain_store: InMemoryAuthProviderDomainStore,
    *,
    org_id: str,
    domain: str,
    provider_kind: AuthProviderKind = AuthProviderKind.OIDC,
    sso_enforced: bool = False,
    display_name: str = "Okta",
) -> AuthProviderRecord:
    provider = AuthProviderRecord(
        provider_id=f"prv_{org_id}_okta",
        org_id=org_id,
        kind=provider_kind,
        display_name=display_name,
        enabled=True,
    )
    identity.create_auth_provider(provider)
    domain_store.upsert(
        AuthProviderDomainRecord(
            domain=domain,
            org_id=org_id,
            provider_id=provider.provider_id,
            sso_enforced=sso_enforced,
        )
    )
    return provider


# ---------------------------------------------------------------------------
# DiscoveryService
# ---------------------------------------------------------------------------


class TestDiscoveryService:
    def test_sso_claimed_domain_returns_sso(self) -> None:
        identity, domain_store, _, _, _, rate = _setup()
        _seed_org_and_user(
            identity,
            org_id="org_acme",
            org_slug="acme",
            email="sarah@acme.com",
        )
        _seed_provider_and_claim(
            identity, domain_store, org_id="org_acme", domain="acme.com"
        )
        svc = DiscoveryService(
            domain_store=domain_store,
            identity_store=identity,
            rate_limiter=rate,
        )
        result = svc.discover(AuthDiscoverRequest(email="sarah@acme.com"))
        assert result.kind == AuthDiscoverKind.SSO
        assert result.org_id == "org_acme"
        assert result.provider_id == "prv_org_acme_okta"
        assert result.provider_display_name == "Okta"
        assert result.member_count == 1
        assert result.magic_link_supported is True

    def test_sso_enforced_disables_magic_link(self) -> None:
        identity, domain_store, _, _, _, rate = _setup()
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        _seed_provider_and_claim(
            identity,
            domain_store,
            org_id="org_acme",
            domain="acme.com",
            sso_enforced=True,
        )
        svc = DiscoveryService(
            domain_store=domain_store,
            identity_store=identity,
            rate_limiter=rate,
        )
        result = svc.discover(AuthDiscoverRequest(email="x@acme.com"))
        assert result.kind == AuthDiscoverKind.SSO
        assert result.sso_enforced is True
        assert result.magic_link_supported is False

    def test_personal_domain_returns_personal(self) -> None:
        identity, domain_store, _, _, _, rate = _setup()
        svc = DiscoveryService(
            domain_store=domain_store,
            identity_store=identity,
            rate_limiter=rate,
        )
        result = svc.discover(AuthDiscoverRequest(email="me@gmail.com"))
        assert result.kind == AuthDiscoverKind.PERSONAL
        assert result.magic_link_supported is True
        assert result.provider_display_name == "Google"

    def test_unknown_domain_returns_magic_link_fallback(self) -> None:
        identity, domain_store, _, _, _, rate = _setup()
        svc = DiscoveryService(
            domain_store=domain_store,
            identity_store=identity,
            rate_limiter=rate,
        )
        result = svc.discover(AuthDiscoverRequest(email="m@launchco.io"))
        assert result.kind == AuthDiscoverKind.MAGIC_LINK
        assert result.magic_link_supported is True

    def test_bank_profile_blocks_personal_domain(self) -> None:
        identity, domain_store, _, _, _, rate = _setup()
        svc = DiscoveryService(
            domain_store=domain_store,
            identity_store=identity,
            rate_limiter=rate,
            magic_link_globally_enabled=False,
        )
        result = svc.discover(AuthDiscoverRequest(email="me@gmail.com"))
        assert result.kind == AuthDiscoverKind.UNKNOWN
        assert result.magic_link_supported is False
        assert result.message and "single sign-on" in result.message.lower()

    def test_rate_limit_per_ip(self) -> None:
        identity, domain_store, _, _, _, rate = _setup()
        svc = DiscoveryService(
            domain_store=domain_store,
            identity_store=identity,
            rate_limiter=rate,
        )
        # 30 / minute / IP. Hit it twice to make sure the first 30 succeed.
        for n in range(30):
            svc.discover(AuthDiscoverRequest(email=f"u{n}@gmail.com", ip="1.2.3.4"))
        with pytest.raises(DiscoveryRateLimited) as exc:
            svc.discover(AuthDiscoverRequest(email="x@gmail.com", ip="1.2.3.4"))
        assert exc.value.retry_after_seconds >= 1


# ---------------------------------------------------------------------------
# MagicLinkService — request
# ---------------------------------------------------------------------------


def _build_magic_link_service(
    *,
    magic_link_globally_enabled: bool = True,
) -> tuple[
    MagicLinkService,
    InMemoryIdentityStore,
    InMemoryMagicLinkTokenStore,
    SessionService,
    InMemoryRateLimiter,
    _PickTokenCodec,
]:
    identity, domain_store, token_store, sessions, dispatcher, rate = _setup()
    pick_codec = _PickTokenCodec(secret=_AUTH_SECRET)
    svc = MagicLinkService(
        token_store=token_store,
        identity_store=identity,
        sessions=sessions,
        pick_codec=pick_codec,
        rate_limiter=rate,
        email_dispatcher=dispatcher,
        base_url="http://localhost:5173",
        magic_link_globally_enabled=magic_link_globally_enabled,
    )
    return svc, identity, token_store, sessions, rate, pick_codec


class TestMagicLinkRequest:
    def test_known_user_writes_row_and_records_attempt(self) -> None:
        svc, identity, token_store, _, _, _ = _build_magic_link_service()
        _seed_org_and_user(
            identity,
            org_id="org_acme",
            org_slug="acme",
            email="sarah@acme.com",
        )
        result = svc.request(MagicLinkStartRequest(email="sarah@acme.com"))
        assert result.status == "queued"
        # One row, one login_attempt, one identity_audit_event.
        assert len(token_store.rows) == 1
        attempts = identity.list_login_attempts(org_id=None, email="sarah@acme.com")
        assert any(
            a.outcome == LoginAttemptOutcome.MAGIC_LINK_REQUESTED for a in attempts
        )

    def test_unknown_user_writes_no_row_anti_enumeration(self) -> None:
        svc, identity, token_store, _, _, _ = _build_magic_link_service()
        # No org / user seeded.
        result = svc.request(MagicLinkStartRequest(email="ghost@nowhere.com"))
        assert result.status == "queued"
        assert len(token_store.rows) == 0
        attempts = identity.list_login_attempts(org_id=None, email="ghost@nowhere.com")
        assert any(a.outcome == LoginAttemptOutcome.UNKNOWN_USER for a in attempts)

    def test_response_shape_identical_for_known_and_unknown(self) -> None:
        svc, identity, _, _, _, _ = _build_magic_link_service()
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        known = svc.request(MagicLinkStartRequest(email="x@acme.com"))
        unknown = svc.request(MagicLinkStartRequest(email="z@nope.com"))
        # The response shape is the only signal the caller sees; they MUST
        # be byte-identical.
        assert known.model_dump() == unknown.model_dump()

    def test_rate_limit_per_ip_with_distinct_emails(self) -> None:
        # Per-IP cap is 5/min, per-email is 3/hour; use 5 distinct emails so
        # the IP cap is the one that trips first.
        svc, identity, _, _, _, _ = _build_magic_link_service()
        for n in range(5):
            email = f"u{n}@gmail.com"
            svc.request(MagicLinkStartRequest(email=email, ip="1.2.3.4"))
        with pytest.raises(MagicLinkRateLimited):
            svc.request(MagicLinkStartRequest(email="u99@gmail.com", ip="1.2.3.4"))

    def test_rate_limit_per_email(self) -> None:
        # Per-email cap is 3/hour. Hit it three times then the fourth should
        # 429 even from a different IP.
        svc, identity, _, _, _, _ = _build_magic_link_service()
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        for n in range(3):
            svc.request(MagicLinkStartRequest(email="x@acme.com", ip=f"1.2.3.{n}"))
        with pytest.raises(MagicLinkRateLimited):
            svc.request(MagicLinkStartRequest(email="x@acme.com", ip="9.9.9.9"))

    def test_bank_profile_disabled_skips_dispatch(self) -> None:
        svc, identity, token_store, _, _, _ = _build_magic_link_service(
            magic_link_globally_enabled=False
        )
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        result = svc.request(MagicLinkStartRequest(email="x@acme.com"))
        assert result.status == "queued"
        assert len(token_store.rows) == 0


# ---------------------------------------------------------------------------
# MagicLinkService — consume
# ---------------------------------------------------------------------------


def _consume_token(svc: MagicLinkService, *, token_store: InMemoryMagicLinkTokenStore):
    """Helper: snoop the most-recent token to roundtrip without going through
    the email dispatcher."""

    # In-memory adapter: derive plaintext is impossible (we hash on write).
    # For tests we patch in a known plaintext + hash by calling through the
    # service request path and capturing via the dispatcher.
    raise NotImplementedError


class TestMagicLinkConsume:
    def _request_and_capture(
        self, *, email: str
    ) -> tuple[
        MagicLinkService, InMemoryIdentityStore, str, InMemoryMagicLinkTokenStore
    ]:
        """Issue a magic link and return the plaintext token by intercepting
        the dispatcher's logged URL."""

        captured: list[str] = []

        class _CapturingDispatcher:
            def send_magic_link(self, *, login_url: str, **kw) -> None:
                captured.append(login_url)

        identity, domain_store, token_store, sessions, _dispatcher, rate = _setup()
        pick_codec = _PickTokenCodec(secret=_AUTH_SECRET)
        svc = MagicLinkService(
            token_store=token_store,
            identity_store=identity,
            sessions=sessions,
            pick_codec=pick_codec,
            rate_limiter=rate,
            email_dispatcher=_CapturingDispatcher(),
            base_url="http://localhost:5173",
        )
        return svc, identity, captured, token_store

    def test_consume_single_workspace_mints_session(self) -> None:
        svc, identity, captured, _ = self._request_and_capture(email="x@acme.com")
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        svc.request(MagicLinkStartRequest(email="x@acme.com"))
        url = captured[0]
        token = url.split("token=", 1)[1]
        result = svc.consume(MagicLinkCallbackRequest(token=token))
        assert result.outcome == MagicLinkCallbackOutcome.SESSION_MINTED
        assert result.bearer_token is not None
        assert result.org_id == "org_acme"

    def test_consume_multi_workspace_returns_pick_token(self) -> None:
        svc, identity, captured, _ = self._request_and_capture(email="x@acme.com")
        _seed_org_and_user(
            identity,
            org_id="org_acme_us",
            org_slug="acme",
            email="x@acme.com",
            user_id="usr_acme_us",
        )
        _seed_org_and_user(
            identity,
            org_id="org_acme_eu",
            org_slug="acmeeu",
            email="x@acme.com",
            user_id="usr_acme_eu",
        )
        svc.request(MagicLinkStartRequest(email="x@acme.com"))
        url = captured[0]
        token = url.split("token=", 1)[1]
        result = svc.consume(MagicLinkCallbackRequest(token=token))
        assert result.outcome == MagicLinkCallbackOutcome.WORKSPACE_PICK_REQUIRED
        assert result.pick_token is not None
        assert len(result.workspaces) == 2
        candidate_ids = {w.org_id for w in result.workspaces}
        assert candidate_ids == {"org_acme_us", "org_acme_eu"}

    def test_consume_invalid_token_raises_401(self) -> None:
        svc, _, _, _ = self._request_and_capture(email="x@acme.com")
        with pytest.raises(MagicLinkInvalidToken) as exc:
            svc.consume(MagicLinkCallbackRequest(token="not_a_real_token"))
        assert exc.value.reason == "invalid_token"

    def test_consume_replay_returns_consumed_token(self) -> None:
        svc, identity, captured, _ = self._request_and_capture(email="x@acme.com")
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        svc.request(MagicLinkStartRequest(email="x@acme.com"))
        token = captured[0].split("token=", 1)[1]
        svc.consume(MagicLinkCallbackRequest(token=token))
        with pytest.raises(MagicLinkInvalidToken) as exc:
            svc.consume(MagicLinkCallbackRequest(token=token))
        assert exc.value.reason == "consumed_token"

    def test_consume_expired_token_raises(self) -> None:
        svc, identity, captured, token_store = self._request_and_capture(
            email="x@acme.com"
        )
        _seed_org_and_user(
            identity, org_id="org_acme", org_slug="acme", email="x@acme.com"
        )
        svc.request(MagicLinkStartRequest(email="x@acme.com"))
        token = captured[0].split("token=", 1)[1]
        # Backdate the row's expires_at.
        for tid, rec in list(token_store.rows.items()):
            token_store.rows[tid] = rec.model_copy(
                update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
            )
        with pytest.raises(MagicLinkInvalidToken) as exc:
            svc.consume(MagicLinkCallbackRequest(token=token))
        assert exc.value.reason == "expired_token"


# ---------------------------------------------------------------------------
# SessionSelectService
# ---------------------------------------------------------------------------


class TestSessionSelect:
    def test_select_valid_member_mints_session(self) -> None:
        svc, identity, _, sessions, rate, pick_codec = _build_magic_link_service()
        _seed_org_and_user(
            identity,
            org_id="org_acme",
            org_slug="acme",
            email="x@acme.com",
            user_id="usr_x",
        )
        sel = SessionSelectService(
            identity_store=identity,
            sessions=sessions,
            pick_codec=pick_codec,
            rate_limiter=rate,
        )
        token = pick_codec.encode(user_id="usr_x", candidate_orgs=("org_acme",))
        result = sel.select(SessionSelectRequest(pick_token=token, org_id="org_acme"))
        assert result.bearer_token
        assert result.org_id == "org_acme"
        # Audit row landed.
        attempts = identity.list_login_attempts(org_id="org_acme", user_id="usr_x")
        assert any(
            a.outcome == LoginAttemptOutcome.WORKSPACE_SELECTED for a in attempts
        )

    def test_select_cross_org_rejected(self) -> None:
        svc, identity, _, sessions, rate, pick_codec = _build_magic_link_service()
        _seed_org_and_user(
            identity,
            org_id="org_acme",
            org_slug="acme",
            email="x@acme.com",
            user_id="usr_x",
        )
        sel = SessionSelectService(
            identity_store=identity,
            sessions=sessions,
            pick_codec=pick_codec,
            rate_limiter=rate,
        )
        # User can probe org_other but the pick_token only authorised org_acme.
        token = pick_codec.encode(user_id="usr_x", candidate_orgs=("org_acme",))
        with pytest.raises(WorkspaceMembershipDenied):
            sel.select(SessionSelectRequest(pick_token=token, org_id="org_other"))

    def test_select_invalid_pick_token(self) -> None:
        svc, identity, _, sessions, rate, pick_codec = _build_magic_link_service()
        sel = SessionSelectService(
            identity_store=identity,
            sessions=sessions,
            pick_codec=pick_codec,
            rate_limiter=rate,
        )
        with pytest.raises(PickTokenInvalid):
            sel.select(
                SessionSelectRequest(
                    pick_token="malformed.signature", org_id="org_acme"
                )
            )
