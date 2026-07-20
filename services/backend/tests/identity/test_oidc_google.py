"""Tests for the global "Continue with Google" OIDC provider + self-signup.

Same in-process fake-IdP pattern as ``test_oidc.py``: a per-test RSA key
signs ID tokens, a stub token endpoint returns them, and a stub JWKS
fetcher serves the public key. No network. The provider under test is the
env-configured global ``google`` provider (no per-org auth_providers row
drives resolution).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt

from backend_app.app import create_app
from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleRecord,
)
from backend_app.identity import (
    GOOGLE_GLOBAL_ORG_ID,
    GOOGLE_PROVIDER_ID,
    GlobalProviderConflict,
    IdTokenVerificationError,
    InMemoryIdentityStore,
    InMemoryOidcStore,
    InMemorySessionStore,
    OidcService,
    OidcStateMismatch,
    OidcUserNotProvisioned,
    SessionService,
    build_google_provider,
    ensure_global_auth_provider,
)
from backend_app.identity.google import (
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_ISSUER,
    GOOGLE_JWKS_URL,
    GOOGLE_TOKEN_ENDPOINT,
)
from backend_app.identity.jwks import IdTokenVerifier, JwksProvider
from backend_app.token_vault import LocalTokenVault


_AUTH_SECRET = "test-auth-secret-google-oidc"
_TEST_SERVICE_TOKEN = "test-service-token"
# Obviously-fake OAuth client values (never real credentials).
_FAKE_CLIENT_ID = "test-google-client-id.apps.example"
_FAKE_CLIENT_SECRET = "test-google-client-secret-not-real"


# ---------------------------------------------------------------------------
# Fake IdP helpers (mirrors test_oidc.py)
# ---------------------------------------------------------------------------


def _generate_rsa_key() -> tuple[Any, dict[str, Any], str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private.public_key().public_numbers()

    def _b64(value: int) -> str:
        size = (value.bit_length() + 7) // 8
        return (
            base64.urlsafe_b64encode(value.to_bytes(size, "big"))
            .decode("ascii")
            .rstrip("=")
        )

    kid = "test-google-key-1"
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64(public_numbers.n),
                "e": _b64(public_numbers.e),
            }
        ]
    }
    return private, jwks, kid


def _sign_id_token(
    private_key: Any,
    *,
    kid: str,
    subject: str,
    nonce: str,
    email: str | None = None,
    email_verified: bool | None = None,
    name: str | None = None,
    expires_in_seconds: int = 3600,
) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload: dict[str, Any] = {
        "iss": GOOGLE_ISSUER,
        "aud": _FAKE_CLIENT_ID,
        "sub": subject,
        "iat": now,
        "exp": now + expires_in_seconds,
        "nonce": nonce,
    }
    if email is not None:
        payload["email"] = email
    if email_verified is not None:
        payload["email_verified"] = email_verified
    if name is not None:
        payload["name"] = name
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


class _StubJwksFetcher:
    def __init__(self, jwks: dict[str, Any]) -> None:
        self.jwks = jwks

    def fetch(self, jwks_url: str) -> dict[str, Any]:
        del jwks_url
        return self.jwks


class _StubTokenEndpoint:
    def __init__(self) -> None:
        self.response: dict[str, Any] = {}
        self.calls: list[dict[str, str]] = []

    def exchange(self, *, token_endpoint: str, body: dict[str, str]) -> dict[str, Any]:
        del token_endpoint
        self.calls.append(dict(body))
        return self.response


# ---------------------------------------------------------------------------
# Provider builder + anchor row
# ---------------------------------------------------------------------------


class TestBuildGoogleProvider:
    def test_returns_none_without_client_id(self) -> None:
        vault = LocalTokenVault(secret="test-vault-secret-32characterslong-1234")
        assert build_google_provider(environ={}, token_vault=vault) is None
        assert (
            build_google_provider(
                environ={"GOOGLE_OAUTH_CLIENT_ID": "   "}, token_vault=vault
            )
            is None
        )

    def test_builds_record_with_documented_google_endpoints(self) -> None:
        vault = LocalTokenVault(secret="test-vault-secret-32characterslong-1234")
        record = build_google_provider(
            environ={"GOOGLE_OAUTH_CLIENT_ID": _FAKE_CLIENT_ID},
            token_vault=vault,
        )
        assert record is not None
        assert record.provider_id == GOOGLE_PROVIDER_ID
        assert record.org_id == GOOGLE_GLOBAL_ORG_ID
        assert record.kind == AuthProviderKind.OIDC
        assert record.enabled is True
        assert record.config["issuer"] == GOOGLE_ISSUER
        assert record.config["authorization_endpoint"] == GOOGLE_AUTHORIZATION_ENDPOINT
        assert record.config["token_endpoint"] == GOOGLE_TOKEN_ENDPOINT
        assert record.config["jwks_url"] == GOOGLE_JWKS_URL
        assert record.config["scopes"] == ["openid", "email", "profile"]
        # No secret → PKCE-only token exchange.
        assert record.config["token_endpoint_auth_method"] == "none"
        assert record.encrypted_client_secret is None

    def test_secret_is_vault_encrypted_never_plaintext(self) -> None:
        vault = LocalTokenVault(secret="test-vault-secret-32characterslong-1234")
        record = build_google_provider(
            environ={
                "GOOGLE_OAUTH_CLIENT_ID": _FAKE_CLIENT_ID,
                "GOOGLE_OAUTH_CLIENT_SECRET": _FAKE_CLIENT_SECRET,
            },
            token_vault=vault,
        )
        assert record is not None
        assert record.config["token_endpoint_auth_method"] == "client_secret_post"
        assert record.encrypted_client_secret is not None
        assert _FAKE_CLIENT_SECRET not in record.encrypted_client_secret
        assert vault.decrypt(record.encrypted_client_secret) == _FAKE_CLIENT_SECRET


class TestEnsureGlobalAuthProvider:
    def _record(self) -> AuthProviderRecord:
        vault = LocalTokenVault(secret="test-vault-secret-32characterslong-1234")
        record = build_google_provider(
            environ={"GOOGLE_OAUTH_CLIENT_ID": _FAKE_CLIENT_ID},
            token_vault=vault,
        )
        assert record is not None
        return record

    def test_creates_anchor_row_then_updates_idempotently(self) -> None:
        store = InMemoryIdentityStore()
        record = self._record()
        ensure_global_auth_provider(identity_store=store, record=record)
        row = store.get_auth_provider_by_id(GOOGLE_PROVIDER_ID)
        assert row is not None
        assert row.org_id == GOOGLE_GLOBAL_ORG_ID

        # Second boot: same id → update path, still one row.
        rotated = record.model_copy(
            update={"config": {**record.config, "client_id": "rotated.example"}}
        )
        ensure_global_auth_provider(identity_store=store, record=rotated)
        row = store.get_auth_provider_by_id(GOOGLE_PROVIDER_ID)
        assert row is not None
        assert row.config["client_id"] == "rotated.example"

    def test_conflicting_org_row_is_refused(self) -> None:
        store = InMemoryIdentityStore()
        store.create_auth_provider(
            AuthProviderRecord(
                provider_id=GOOGLE_PROVIDER_ID,
                org_id="org_someone_else",
                kind=AuthProviderKind.OIDC,
                display_name="Google",
            )
        )
        with pytest.raises(GlobalProviderConflict):
            ensure_global_auth_provider(identity_store=store, record=self._record())


# ---------------------------------------------------------------------------
# Service-level flow fixture
# ---------------------------------------------------------------------------


class GoogleFlowFixtureMixin:
    def build(
        self,
        *,
        allow_self_signup: bool = True,
        with_secret: bool = True,
        seed_admin_role: bool = True,
    ) -> tuple[OidcService, dict[str, Any]]:
        identity_store = InMemoryIdentityStore()
        oidc_store = InMemoryOidcStore()
        sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        # System roles as seeded by 0004b_seed_system_roles.sql.
        if seed_admin_role:
            identity_store.create_role(
                RoleRecord(
                    name="admin",
                    display_name="Administrator",
                    is_system=True,
                    permission_scopes=(
                        "admin:users",
                        "admin:idp",
                        "runtime:use",
                    ),
                )
            )
        identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="Employee",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )

        token_vault = LocalTokenVault(secret="test-vault-secret-32characterslong-1234")
        environ = {"GOOGLE_OAUTH_CLIENT_ID": _FAKE_CLIENT_ID}
        if with_secret:
            environ["GOOGLE_OAUTH_CLIENT_SECRET"] = _FAKE_CLIENT_SECRET
        google = build_google_provider(environ=environ, token_vault=token_vault)
        assert google is not None
        ensure_global_auth_provider(identity_store=identity_store, record=google)

        private, jwks, kid = _generate_rsa_key()
        jwks_provider = JwksProvider(store=oidc_store, fetcher=_StubJwksFetcher(jwks))
        token_endpoint = _StubTokenEndpoint()
        service = OidcService(
            identity_store=identity_store,
            oidc_store=oidc_store,
            sessions=sessions,
            token_vault=token_vault,
            token_endpoint_client=token_endpoint,
            id_token_verifier=IdTokenVerifier(jwks_provider=jwks_provider),
            global_providers={google.provider_id: google},
            allow_self_signup=allow_self_signup,
        )
        return service, {
            "identity_store": identity_store,
            "oidc_store": oidc_store,
            "sessions": sessions,
            "token_endpoint": token_endpoint,
            "token_vault": token_vault,
            "private": private,
            "kid": kid,
            "google": google,
        }

    def round_trip(
        self,
        service: OidcService,
        ctx: dict[str, Any],
        *,
        subject: str = "gsub-1234567890",
        email: str | None = "alice.doe@gmail.example",
        email_verified: bool | None = True,
        name: str | None = "Alice Doe",
    ) -> Any:
        authorize = service.authorize(
            org_id="-",  # facade placeholder: no workspace known pre-login
            provider_id=GOOGLE_PROVIDER_ID,
            redirect_uri="https://app.example/v1/auth/oidc/callback",
        )
        auth_record = next(
            row
            for row in ctx["oidc_store"].authentications.values()
            if row.state == authorize.state
        )
        id_token = _sign_id_token(
            ctx["private"],
            kid=ctx["kid"],
            subject=subject,
            nonce=auth_record.nonce,
            email=email,
            email_verified=email_verified,
            name=name,
        )
        ctx["token_endpoint"].response = {
            "id_token": id_token,
            "access_token": "test-access-token",
            "expires_in": 3600,
        }
        return service.callback(state=authorize.state, code="auth-code-from-google")


class TestGoogleAuthorize(GoogleFlowFixtureMixin):
    def test_authorize_without_org_targets_google_and_pins_sentinel_org(self) -> None:
        service, ctx = self.build()
        result = service.authorize(
            org_id="-",
            provider_id=GOOGLE_PROVIDER_ID,
            redirect_uri="https://app.example/cb",
        )
        assert result.auth_url.startswith(GOOGLE_AUTHORIZATION_ENDPOINT + "?")
        assert "code_challenge_method=S256" in result.auth_url
        assert "scope=openid+email+profile" in result.auth_url
        auth_record = next(
            row
            for row in ctx["oidc_store"].authentications.values()
            if row.state == result.state
        )
        assert auth_record.org_id == GOOGLE_GLOBAL_ORG_ID
        assert auth_record.provider_id == GOOGLE_PROVIDER_ID


class TestGoogleSelfSignup(GoogleFlowFixtureMixin):
    def test_first_login_provisions_personal_org_user_link_session(self) -> None:
        service, ctx = self.build()
        result = self.round_trip(service, ctx)

        store = ctx["identity_store"]
        # Personal org: slug derived from the email local part.
        org = store.get_organization_by_slug(slug="alice-doe")
        assert org is not None
        assert org.display_name == "alice.doe"

        user = store.get_user(org_id=org.org_id, user_id=result.user_id)
        assert user is not None
        assert user.primary_email == "alice.doe@gmail.example"
        assert user.email_verified_at is not None  # email_verified=True respected
        assert user.display_name == "Alice Doe"

        members = store.list_members(org_id=org.org_id)
        assert [m.user_id for m in members] == [user.user_id]
        assert members[0].source.value == "oidc"

        # Sole member of a personal org is its admin.
        assignments = store.list_role_assignments(
            org_id=org.org_id, user_id=user.user_id
        )
        role_names = {store.get_role(role_id=a.role_id).name for a in assignments}
        assert role_names == {"admin"}

        # Identity link exists for the google subject.
        link = ctx["oidc_store"].get_identity_by_subject(
            provider_id=GOOGLE_PROVIDER_ID, subject="gsub-1234567890"
        )
        assert link is not None
        assert link.org_id == org.org_id
        assert link.user_id == user.user_id

        # Session minted exactly like existing flows: signed bearer, admin role.
        assert result.session_id.startswith("sid_")
        assert "." in result.bearer_token

        # Audit trail lands in the NEW personal org.
        audit_actions = [e.action for e in store.list_identity_audit(org_id=org.org_id)]
        assert "oidc.self_signup_org_created" in audit_actions
        assert "oidc.user_provisioned" in audit_actions

    def test_second_login_links_to_existing_user_and_org(self) -> None:
        service, ctx = self.build()
        first = self.round_trip(service, ctx)
        second = self.round_trip(service, ctx)
        assert first.user_id == second.user_id
        assert first.session_id != second.session_id
        # No second org was created.
        assert len(ctx["identity_store"].organizations) == 1

    def test_two_subjects_get_two_personal_orgs(self) -> None:
        service, ctx = self.build()
        first = self.round_trip(
            service, ctx, subject="gsub-a", email="alice.doe@gmail.example"
        )
        second = self.round_trip(
            service, ctx, subject="gsub-b", email="bob@corp.example"
        )
        assert first.user_id != second.user_id
        assert len(ctx["identity_store"].organizations) == 2

    def test_slug_collision_gets_random_suffix(self) -> None:
        service, ctx = self.build()
        ctx["identity_store"].create_organization(
            OrganizationRecord(display_name="Taken", slug="alice-doe")
        )
        result = self.round_trip(service, ctx)
        user = None
        for org in ctx["identity_store"].organizations.values():
            candidate = ctx["identity_store"].get_user(
                org_id=org.org_id, user_id=result.user_id
            )
            if candidate is not None:
                user = candidate
                new_org = org
        assert user is not None
        assert new_org.slug != "alice-doe"
        assert new_org.slug.startswith("alice-doe-")

    def test_signup_refused_when_flag_off(self) -> None:
        service, ctx = self.build(allow_self_signup=False)
        with pytest.raises(OidcUserNotProvisioned):
            self.round_trip(service, ctx)
        # Nothing provisioned.
        assert len(ctx["identity_store"].organizations) == 0
        attempts = ctx["identity_store"].list_login_attempts(
            org_id=GOOGLE_GLOBAL_ORG_ID
        )
        assert any(a.outcome.value == "unknown_user" for a in attempts)

    def test_flag_off_still_allows_already_linked_user(self) -> None:
        service, ctx = self.build(allow_self_signup=True)
        first = self.round_trip(service, ctx)
        # Same store, signup now off — the linked identity still logs in.
        strict = OidcService(
            identity_store=ctx["identity_store"],
            oidc_store=ctx["oidc_store"],
            sessions=ctx["sessions"],
            token_vault=ctx["token_vault"],
            token_endpoint_client=ctx["token_endpoint"],
            id_token_verifier=IdTokenVerifier(
                jwks_provider=JwksProvider(
                    store=ctx["oidc_store"],
                    fetcher=_StubJwksFetcher({"keys": []}),
                )
            ),
            global_providers={GOOGLE_PROVIDER_ID: ctx["google"]},
            allow_self_signup=False,
        )
        second = self.round_trip(strict, ctx)
        assert second.user_id == first.user_id

    def test_signup_refused_when_email_unverified(self) -> None:
        service, ctx = self.build()
        with pytest.raises(OidcUserNotProvisioned):
            self.round_trip(service, ctx, email_verified=False)
        assert len(ctx["identity_store"].organizations) == 0

    def test_signup_refused_without_email_claim(self) -> None:
        service, ctx = self.build()
        with pytest.raises(OidcUserNotProvisioned):
            self.round_trip(service, ctx, email=None, email_verified=None)

    def test_unverified_flag_absent_creates_user_without_verified_at(self) -> None:
        # Some IdPs omit email_verified entirely; we provision but do not
        # stamp email_verified_at.
        service, ctx = self.build()
        result = self.round_trip(service, ctx, email_verified=None)
        store = ctx["identity_store"]
        org = store.get_organization_by_slug(slug="alice-doe")
        user = store.get_user(org_id=org.org_id, user_id=result.user_id)
        assert user.email_verified_at is None

    def test_state_replay_and_nonce_validation_intact(self) -> None:
        service, ctx = self.build()
        result = self.round_trip(service, ctx)
        assert result.user_id.startswith("usr_")
        with pytest.raises(OidcStateMismatch):
            service.callback(state="bogus-state", code="x")

        authorize = service.authorize(
            org_id="-",
            provider_id=GOOGLE_PROVIDER_ID,
            redirect_uri="https://app.example/cb",
        )
        bad_nonce = _sign_id_token(
            ctx["private"],
            kid=ctx["kid"],
            subject="gsub-1234567890",
            nonce="not-the-real-nonce",
            email="alice.doe@gmail.example",
            email_verified=True,
        )
        ctx["token_endpoint"].response = {"id_token": bad_nonce}
        with pytest.raises(IdTokenVerificationError):
            service.callback(state=authorize.state, code="x")

    def test_token_exchange_sends_decrypted_secret(self) -> None:
        service, ctx = self.build(with_secret=True)
        self.round_trip(service, ctx)
        last_call = ctx["token_endpoint"].calls[-1]
        assert last_call["client_id"] == _FAKE_CLIENT_ID
        assert last_call["client_secret"] == _FAKE_CLIENT_SECRET
        assert last_call["grant_type"] == "authorization_code"


# ---------------------------------------------------------------------------
# Route-level: providers list advertises google only when env is set
# ---------------------------------------------------------------------------


class TestProvidersRoute:
    def _client(self, monkeypatch, *, with_google: bool) -> TestClient:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _AUTH_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)
        monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        if with_google:
            monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", _FAKE_CLIENT_ID)
            monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", _FAKE_CLIENT_SECRET)
        else:
            monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
            monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        return TestClient(create_app())

    def _headers(self, org_id: str) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _TEST_SERVICE_TOKEN,
            "x-enterprise-org-id": org_id,
            "x-enterprise-user-id": "anonymous",
        }

    def test_google_absent_without_env(self, monkeypatch) -> None:
        client = self._client(monkeypatch, with_google=False)
        response = client.get(
            "/internal/v1/auth/oidc/providers",
            params={"org_id": "org_any"},
            headers=self._headers("org_any"),
        )
        assert response.status_code == 200
        ids = [p["provider_id"] for p in response.json()["providers"]]
        assert GOOGLE_PROVIDER_ID not in ids

    def test_google_advertised_when_env_set(self, monkeypatch) -> None:
        client = self._client(monkeypatch, with_google=True)
        response = client.get(
            "/internal/v1/auth/oidc/providers",
            params={"org_id": "org_any"},
            headers=self._headers("org_any"),
        )
        assert response.status_code == 200
        providers = response.json()["providers"]
        google = [p for p in providers if p["provider_id"] == GOOGLE_PROVIDER_ID]
        assert len(google) == 1
        assert google[0]["kind"] == "oidc"
        assert google[0]["display_name"] == "Google"
        assert google[0]["enabled"] is True

    def test_google_advertised_for_orgless_placeholder(self, monkeypatch) -> None:
        # The pre-workspace login screen lists providers with org_id="-".
        client = self._client(monkeypatch, with_google=True)
        response = client.get(
            "/internal/v1/auth/oidc/providers",
            params={"org_id": "-"},
            headers=self._headers("-"),
        )
        assert response.status_code == 200
        ids = [p["provider_id"] for p in response.json()["providers"]]
        assert ids == [GOOGLE_PROVIDER_ID]

    def test_authorize_route_accepts_orgless_google_start(self, monkeypatch) -> None:
        client = self._client(monkeypatch, with_google=True)
        response = client.post(
            f"/internal/v1/auth/oidc/{GOOGLE_PROVIDER_ID}/authorize",
            headers=self._headers("-"),
            json={
                "org_id": "-",
                "provider_id": GOOGLE_PROVIDER_ID,
                "redirect_uri": "https://app.example/v1/auth/oidc/callback",
                "return_to": "/chat",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["auth_url"].startswith(GOOGLE_AUTHORIZATION_ENDPOINT + "?")
        assert body["state"]


# ---------------------------------------------------------------------------
# Authenticated Google link (account-linking PRD FR-L2/L3/L6/M1)
# ---------------------------------------------------------------------------


class TestGoogleLink(GoogleFlowFixtureMixin):
    """The link fork: attach-to-caller, email upgrade, no session, conflicts."""

    _WALLET_EMAIL = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed@wallet.invalid"

    def _seed_caller(self, ctx: dict[str, Any], *, email: str = _WALLET_EMAIL) -> None:
        from backend_app.contracts import UserRecord

        ctx["identity_store"].create_organization(
            OrganizationRecord(
                org_id="org_caller", display_name="Caller", slug="caller"
            )
        )
        ctx["identity_store"].create_user(
            UserRecord(
                user_id="usr_caller",
                org_id="org_caller",
                primary_email=email,
                display_name="0x5aAe…eAed",
                email_verified_at=None,
            )
        )

    def link_round_trip(
        self,
        service: OidcService,
        ctx: dict[str, Any],
        *,
        subject: str = "gsub-link-123",
        email: str | None = "alice.doe@gmail.example",
        email_verified: bool | None = True,
    ) -> Any:
        authorize = service.authorize(
            org_id="org_caller",
            provider_id=GOOGLE_PROVIDER_ID,
            redirect_uri="https://app.example/v1/auth/oidc/callback",
            link_org_id="org_caller",
            link_user_id="usr_caller",
        )
        auth_record = next(
            row
            for row in ctx["oidc_store"].authentications.values()
            if row.state == authorize.state
        )
        # The link binding is persisted server-side on the state row.
        assert auth_record.link_org_id == "org_caller"
        assert auth_record.link_user_id == "usr_caller"
        id_token = _sign_id_token(
            ctx["private"],
            kid=ctx["kid"],
            subject=subject,
            nonce=auth_record.nonce,
            email=email,
            email_verified=email_verified,
            name="Alice Doe",
        )
        ctx["token_endpoint"].response = {
            "id_token": id_token,
            "access_token": "test-access-token",
            "expires_in": 3600,
        }
        return service.callback(state=authorize.state, code="auth-code-from-google")

    def test_link_attaches_identity_and_upgrades_placeholder_email(self) -> None:
        service, ctx = self.build()
        self._seed_caller(ctx)
        result = self.link_round_trip(service, ctx)
        assert result.linked is True
        assert result.status == "linked"
        assert result.user_id == "usr_caller"
        assert result.email_upgraded is True
        # The identity row binds to the CALLER — no new org/user provisioned.
        ident = ctx["oidc_store"].get_identity_by_subject(
            provider_id=GOOGLE_PROVIDER_ID, subject="gsub-link-123"
        )
        assert ident is not None
        assert (ident.org_id, ident.user_id) == ("org_caller", "usr_caller")
        # The wallet placeholder was upgraded to the VERIFIED Google email.
        user = ctx["identity_store"].get_user(org_id="org_caller", user_id="usr_caller")
        assert user is not None
        assert user.primary_email == "alice.doe@gmail.example"
        assert user.email_verified_at is not None
        # Only the caller's user exists.
        assert len(ctx["identity_store"].users) == 1

    def test_link_mints_no_session(self) -> None:
        service, ctx = self.build()
        self._seed_caller(ctx)
        self.link_round_trip(service, ctx)
        # No session row was minted — the caller keeps their existing bearer.
        assert ctx["sessions"]._store.sessions == {}  # noqa: SLF001

    def test_link_requires_email_verified(self) -> None:
        from backend_app.identity import OidcEmailNotVerified

        service, ctx = self.build()
        self._seed_caller(ctx)
        with pytest.raises(OidcEmailNotVerified):
            self.link_round_trip(service, ctx, email_verified=False)
        # Nothing linked, email untouched.
        user = ctx["identity_store"].get_user(org_id="org_caller", user_id="usr_caller")
        assert user is not None and user.primary_email == self._WALLET_EMAIL

    def test_link_keeps_real_email_untouched(self) -> None:
        service, ctx = self.build()
        self._seed_caller(ctx, email="caller@acme.com")
        result = self.link_round_trip(service, ctx)
        assert result.email_upgraded is False
        user = ctx["identity_store"].get_user(org_id="org_caller", user_id="usr_caller")
        assert user is not None and user.primary_email == "caller@acme.com"

    def test_link_idempotent_when_already_mine(self) -> None:
        service, ctx = self.build()
        self._seed_caller(ctx)
        first = self.link_round_trip(service, ctx)
        assert first.status == "linked"
        second = self.link_round_trip(service, ctx)
        assert second.status == "already_linked"
        # Still exactly one identity row for the subject.
        rows = [
            row
            for row in ctx["oidc_store"].identities.values()
            if row.subject == "gsub-link-123" and row.unlinked_at is None
        ]
        assert len(rows) == 1

    def test_link_conflict_when_subject_owned_by_another_account(self) -> None:
        from backend_app.contracts import OidcIdentityRecord
        from backend_app.identity import OidcIdentityAlreadyLinked

        service, ctx = self.build()
        self._seed_caller(ctx)
        # The Google subject already belongs to a DIFFERENT account.
        ctx["oidc_store"].create_identity(
            OidcIdentityRecord(
                org_id="org_other",
                user_id="usr_other",
                provider_id=GOOGLE_PROVIDER_ID,
                subject="gsub-link-123",
            )
        )
        with pytest.raises(OidcIdentityAlreadyLinked) as exc_info:
            self.link_round_trip(service, ctx)
        assert exc_info.value.org_id == "org_other"
        assert exc_info.value.user_id == "usr_other"
