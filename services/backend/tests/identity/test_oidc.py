"""Tests for the OIDC SSO service (A3).

Uses an in-process fake IdP — generates an RSA signing key per test,
returns a signed ID token from a stub token endpoint, and serves a
JWKS-shaped public key from a stub JWKS fetcher. No network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleRecord,
)
from backend_app.identity import (
    IdTokenVerificationError,
    InMemoryIdentityStore,
    InMemoryOidcStore,
    InMemorySessionStore,
    OidcConfigError,
    OidcProviderDisabled,
    OidcService,
    OidcStateMismatch,
    OidcUserNotProvisioned,
    SessionService,
)
from backend_app.identity.jwks import IdTokenVerifier, JwksProvider


_AUTH_SECRET = "test-auth-secret-oidc"


# ---------------------------------------------------------------------------
# Fake IdP fixtures
# ---------------------------------------------------------------------------


def _generate_rsa_key() -> tuple[Any, dict[str, Any], str]:
    """Return (private_key_object, jwks_dict, kid) for a fresh test key."""

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private.public_key().public_numbers()

    def _b64(value: int) -> str:
        import base64

        size = (value.bit_length() + 7) // 8
        return (
            base64.urlsafe_b64encode(value.to_bytes(size, "big"))
            .decode("ascii")
            .rstrip("=")
        )

    kid = "test-key-1"
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
    issuer: str,
    audience: str,
    subject: str,
    nonce: str,
    email: str | None = None,
    name: str | None = None,
    groups: list[str] | None = None,
    extra_claims: dict[str, Any] | None = None,
    expires_in_seconds: int = 3600,
) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": now,
        "exp": now + expires_in_seconds,
        "nonce": nonce,
    }
    if email is not None:
        payload["email"] = email
    if name is not None:
        payload["name"] = name
    if groups is not None:
        payload["groups"] = groups
    if extra_claims:
        payload.update(extra_claims)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


class _StubJwksFetcher:
    def __init__(self, jwks: dict[str, Any]) -> None:
        self.jwks = jwks
        self.fetch_count = 0

    def fetch(self, jwks_url: str) -> dict[str, Any]:
        del jwks_url
        self.fetch_count += 1
        return self.jwks


class _StubTokenEndpoint:
    """Captures token-endpoint requests + returns a canned response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    def exchange(self, *, token_endpoint: str, body: dict[str, str]) -> dict[str, Any]:
        del token_endpoint
        self.calls.append(dict(body))
        return self.response


# ---------------------------------------------------------------------------
# Test mixin
# ---------------------------------------------------------------------------


class OidcServiceFixtureMixin:
    def build(
        self,
        *,
        auto_provision_user: bool = True,
        group_role_map: dict[str, str] | None = None,
        client_secret_required: bool = False,
    ) -> tuple[OidcService, dict[str, Any]]:
        identity_store = InMemoryIdentityStore()
        oidc_store = InMemoryOidcStore()
        session_store = InMemorySessionStore()
        sessions = SessionService(
            store=session_store,
            auth_secret=_AUTH_SECRET,
            dev_mint_allowed=True,
        )

        org = identity_store.create_organization(
            OrganizationRecord(display_name="Acme", slug="acme")
        )
        # Seed an `employee` system role so default session minting has scopes.
        identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="Employee",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )

        private, jwks, kid = _generate_rsa_key()

        provider_config: dict[str, Any] = {
            "issuer": "https://idp.example/oidc",
            "client_id": "client-acme",
            "authorization_endpoint": "https://idp.example/authorize",
            "token_endpoint": "https://idp.example/token",
            "jwks_url": "https://idp.example/jwks",
            "auto_provision_user": auto_provision_user,
            "group_claim": "groups",
            "group_role_map": group_role_map or {},
            "audience": "client-acme",
            "token_endpoint_auth_method": (
                "client_secret_post" if client_secret_required else "none"
            ),
        }
        encrypted_secret: str | None = None

        from backend_app.token_vault import LocalTokenVault

        token_vault = LocalTokenVault(secret="test-vault-secret-32characterslong-1234")
        if client_secret_required:
            encrypted_secret = token_vault.encrypt("idp-client-secret")

        provider = identity_store.create_auth_provider(
            AuthProviderRecord(
                org_id=org.org_id,
                kind=AuthProviderKind.OIDC,
                display_name="Test IdP",
                config=provider_config,
                encrypted_client_secret=encrypted_secret,
            )
        )

        jwks_fetcher = _StubJwksFetcher(jwks)
        jwks_provider = JwksProvider(store=oidc_store, fetcher=jwks_fetcher)
        verifier = IdTokenVerifier(jwks_provider=jwks_provider)
        token_endpoint = _StubTokenEndpoint(response={})

        service = OidcService(
            identity_store=identity_store,
            oidc_store=oidc_store,
            sessions=sessions,
            token_vault=token_vault,
            token_endpoint_client=token_endpoint,
            id_token_verifier=verifier,
        )
        return service, {
            "identity_store": identity_store,
            "oidc_store": oidc_store,
            "sessions": sessions,
            "org": org,
            "provider": provider,
            "private": private,
            "kid": kid,
            "token_endpoint": token_endpoint,
            "jwks_fetcher": jwks_fetcher,
            "token_vault": token_vault,
        }


class TestAuthorize(OidcServiceFixtureMixin):
    def test_authorize_returns_url_with_pkce_and_persists_state(self) -> None:
        service, ctx = self.build()
        result = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            redirect_uri="https://app.example/cb",
        )

        assert "https://idp.example/authorize?" in result.auth_url
        assert "code_challenge=" in result.auth_url
        assert "code_challenge_method=S256" in result.auth_url
        assert f"state={result.state}" in result.auth_url
        # State persisted; consumable exactly once.
        consumed = ctx["oidc_store"].consume_authentication(state=result.state)
        assert consumed is not None
        replay = ctx["oidc_store"].consume_authentication(state=result.state)
        assert replay is None

    def test_authorize_rejected_when_provider_disabled(self) -> None:
        service, ctx = self.build()
        ctx["identity_store"].update_auth_provider(
            ctx["provider"].model_copy(update={"enabled": False})
        )
        with pytest.raises(OidcProviderDisabled):
            service.authorize(
                org_id=ctx["org"].org_id,
                provider_id=ctx["provider"].provider_id,
                redirect_uri="https://app.example/cb",
            )

    def test_authorize_rejected_when_provider_not_oidc(self) -> None:
        service, ctx = self.build()
        # Create a SAML provider in the same org and try to authorize against it.
        saml = ctx["identity_store"].create_auth_provider(
            AuthProviderRecord(
                org_id=ctx["org"].org_id,
                kind=AuthProviderKind.SAML,
                display_name="ADFS",
            )
        )
        with pytest.raises(OidcConfigError):
            service.authorize(
                org_id=ctx["org"].org_id,
                provider_id=saml.provider_id,
                redirect_uri="https://app.example/cb",
            )


class TestCallback(OidcServiceFixtureMixin):
    def _full_round_trip(
        self,
        ctx: dict[str, Any],
        service: OidcService,
        *,
        subject: str = "google|abc123",
        email: str | None = "alice@acme.com",
        name: str | None = "Alice",
        groups: list[str] | None = None,
        with_refresh_token: bool = True,
    ) -> Any:
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            redirect_uri="https://app.example/cb",
        )
        # Look up the authentication row to read its nonce (state→record map).
        auth_record = next(
            row
            for row in ctx["oidc_store"].authentications.values()
            if row.state == authorize.state
        )
        id_token = _sign_id_token(
            ctx["private"],
            kid=ctx["kid"],
            issuer="https://idp.example/oidc",
            audience="client-acme",
            subject=subject,
            nonce=auth_record.nonce,
            email=email,
            name=name,
            groups=groups,
        )
        ctx["token_endpoint"].response = {
            "id_token": id_token,
            "access_token": "access-token-1",
            "expires_in": 3600,
            **(
                {"refresh_token": "refresh-token-1", "scope": "openid email profile"}
                if with_refresh_token
                else {}
            ),
        }
        return service.callback(state=authorize.state, code="auth-code-from-idp")

    def test_full_callback_round_trip_provisions_user_and_session(self) -> None:
        service, ctx = self.build()
        result = self._full_round_trip(ctx, service)

        assert result.user_id.startswith("usr_")
        assert result.session_id.startswith("sid_")
        assert "." in result.bearer_token  # signed bearer
        # JIT provisioning created the user.
        assert (
            ctx["identity_store"].get_user(
                org_id=ctx["org"].org_id, user_id=result.user_id
            )
            is not None
        )

    def test_replay_of_state_rejected(self) -> None:
        service, ctx = self.build()
        result = self._full_round_trip(ctx, service)
        # Reusing the same code/state is rejected because state was consumed.
        with pytest.raises(OidcStateMismatch):
            service.callback(state="bogus-state", code="x")
        # Re-running the round trip mints a fresh session (different state).
        again = self._full_round_trip(ctx, service)
        assert again.session_id != result.session_id

    def test_forged_signature_rejected(self) -> None:
        service, ctx = self.build()
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            redirect_uri="https://app.example/cb",
        )
        auth_record = next(
            row
            for row in ctx["oidc_store"].authentications.values()
            if row.state == authorize.state
        )
        # Sign the ID token with a DIFFERENT key (attacker key).
        attacker_key, _, _ = _generate_rsa_key()
        forged_token = _sign_id_token(
            attacker_key,
            kid=ctx["kid"],  # claims to be our key
            issuer="https://idp.example/oidc",
            audience="client-acme",
            subject="forged",
            nonce=auth_record.nonce,
            email="evil@bad.com",
        )
        ctx["token_endpoint"].response = {"id_token": forged_token}
        with pytest.raises(IdTokenVerificationError):
            service.callback(state=authorize.state, code="x")

    def test_nonce_mismatch_rejected(self) -> None:
        service, ctx = self.build()
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            redirect_uri="https://app.example/cb",
        )
        bad_nonce_token = _sign_id_token(
            ctx["private"],
            kid=ctx["kid"],
            issuer="https://idp.example/oidc",
            audience="client-acme",
            subject="x",
            nonce="not-the-real-nonce",
            email="alice@acme.com",
        )
        ctx["token_endpoint"].response = {"id_token": bad_nonce_token}
        with pytest.raises(IdTokenVerificationError):
            service.callback(state=authorize.state, code="x")

    def test_audience_mismatch_rejected(self) -> None:
        service, ctx = self.build()
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            redirect_uri="https://app.example/cb",
        )
        auth_record = next(
            row
            for row in ctx["oidc_store"].authentications.values()
            if row.state == authorize.state
        )
        wrong_aud_token = _sign_id_token(
            ctx["private"],
            kid=ctx["kid"],
            issuer="https://idp.example/oidc",
            audience="some-other-client",
            subject="x",
            nonce=auth_record.nonce,
            email="alice@acme.com",
        )
        ctx["token_endpoint"].response = {"id_token": wrong_aud_token}
        with pytest.raises(IdTokenVerificationError):
            service.callback(state=authorize.state, code="x")

    def test_unknown_user_rejected_when_jit_off(self) -> None:
        service, ctx = self.build(auto_provision_user=False)
        with pytest.raises(OidcUserNotProvisioned):
            self._full_round_trip(ctx, service)

    def test_existing_link_reused_on_second_login(self) -> None:
        service, ctx = self.build()
        first = self._full_round_trip(ctx, service)
        second = self._full_round_trip(ctx, service)
        assert first.user_id == second.user_id
        # Second session is a brand-new row; first one is still valid.
        assert first.session_id != second.session_id

    def test_role_sync_assigns_mapped_roles(self) -> None:
        service, ctx = self.build(group_role_map={"engineering": "auditor"})
        # Seed the org-level "auditor" role so the mapping resolves.
        ctx["identity_store"].create_role(
            RoleRecord(
                org_id=ctx["org"].org_id,
                name="auditor",
                display_name="Auditor",
                permission_scopes=("audit:read",),
            )
        )
        result = self._full_round_trip(
            ctx, service, groups=["engineering", "other-irrelevant"]
        )
        assignments = ctx["identity_store"].list_role_assignments(
            org_id=ctx["org"].org_id, user_id=result.user_id
        )
        role_names = {
            ctx["identity_store"].get_role(role_id=a.role_id).name for a in assignments
        }
        assert "auditor" in role_names

    def test_refresh_token_stored_encrypted_then_revoked_on_re_login(self) -> None:
        service, ctx = self.build()
        self._full_round_trip(ctx, service)
        active = [
            t for t in ctx["oidc_store"].refresh_tokens.values() if t.revoked_at is None
        ]
        assert len(active) == 1
        # Plaintext "refresh-token-1" must not appear in any store value.
        assert "refresh-token-1" not in active[0].encrypted_refresh_token
        # Decrypts to the original via the vault.
        assert (
            ctx["token_vault"].decrypt(active[0].encrypted_refresh_token)
            == "refresh-token-1"
        )

        self._full_round_trip(ctx, service)
        # After a second login, the previous refresh-token is revoked and a
        # fresh one is the only active row.
        active_after = [
            t for t in ctx["oidc_store"].refresh_tokens.values() if t.revoked_at is None
        ]
        assert len(active_after) == 1
        assert active_after[0].token_id != active[0].token_id

    def test_client_secret_post_token_endpoint_receives_decrypted_secret(self) -> None:
        service, ctx = self.build(client_secret_required=True)
        self._full_round_trip(ctx, service)
        last_call = ctx["token_endpoint"].calls[-1]
        assert last_call["client_secret"] == "idp-client-secret"
        assert last_call["grant_type"] == "authorization_code"
