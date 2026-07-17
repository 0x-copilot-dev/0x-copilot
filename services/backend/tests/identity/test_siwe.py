"""Sign-In-With-Ethereum (EIP-4361) tests.

REAL signature vectors: every flow test generates a throwaway secp256k1
key with ``eth_account`` inside the test, signs the canonical message via
EIP-191 ``personal_sign``, and drives the full verify path — no mocked
recovery. Mirrors the structure of ``test_oidc_google.py`` (service-level
flow fixture + route-level contract checks).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, RoleRecord
from backend_app.identity import (
    InMemoryIdentityStore,
    InMemoryRateLimiter,
    InMemorySessionStore,
    InMemorySiweStore,
    SIWE_GLOBAL_ORG_ID,
    SIWE_PROVIDER_ID,
    SIWE_STATEMENT,
    SessionService,
    SiweAddressInvalid,
    SiweChainNotAllowed,
    SiweDomainMismatch,
    SiweExpiredMessage,
    SiweMessageInvalid,
    SiweNonceExpired,
    SiweNonceInvalid,
    SiweRateLimited,
    SiweSelfSignupDisabled,
    SiweService,
    SiweSignatureInvalid,
    build_siwe_message,
    display_address,
    normalize_wallet_address,
    parse_allowed_chain_ids,
    parse_siwe_message,
    truncated_display_address,
)


_AUTH_SECRET = "test-auth-secret-siwe-0123456789"
_TEST_SERVICE_TOKEN = "test-service-token"
_ORIGIN = "http://localhost:5173"
_DOMAIN = "localhost:5173"


def _sign(account: Any, message: str) -> str:
    return account.sign_message(encode_defunct(text=message)).signature.to_0x_hex()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestAddressHelpers:
    def test_normalize_lowercases_valid_addresses(self) -> None:
        account = Account.create()
        assert normalize_wallet_address(account.address) == account.address.lower()
        # All-lowercase input carries no checksum — accepted as-is.
        assert (
            normalize_wallet_address(account.address.lower()) == account.address.lower()
        )

    def test_normalize_rejects_malformed(self) -> None:
        for bad in (
            "",
            "0x123",
            "not-an-address",
            "0x" + "g" * 40,
            "0x" + "a" * 39,
            "0x" + "a" * 41,
            42,
            None,
        ):
            with pytest.raises(SiweAddressInvalid):
                normalize_wallet_address(bad)

    def test_normalize_rejects_bad_eip55_checksum(self) -> None:
        account = Account.create()
        checksummed = account.address
        # Flip the case of one alphabetic character → invalid checksum.
        for index, char in enumerate(checksummed[2:], start=2):
            if char.isalpha():
                tampered = (
                    checksummed[:index] + char.swapcase() + checksummed[index + 1 :]
                )
                break
        with pytest.raises(SiweAddressInvalid):
            normalize_wallet_address(tampered)

    def test_display_round_trip(self) -> None:
        account = Account.create()
        assert display_address(account.address.lower()) == account.address
        truncated = truncated_display_address(account.address.lower())
        assert truncated == f"{account.address[:6]}…{account.address[-4:]}"


class TestChainAllowlistParsing:
    def test_default_when_unset_or_blank(self) -> None:
        assert parse_allowed_chain_ids(None) == frozenset({1, 8453, 42161, 4663})
        assert parse_allowed_chain_ids("  ") == frozenset({1, 8453, 42161, 4663})

    def test_parses_comma_separated(self) -> None:
        assert parse_allowed_chain_ids("1, 10,8453") == frozenset({1, 10, 8453})

    def test_garbage_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            parse_allowed_chain_ids("1,mainnet")
        with pytest.raises(ValueError):
            parse_allowed_chain_ids(",,")


class TestMessageParse:
    def _message(self, **overrides: Any) -> str:
        account = Account.create()
        kwargs: dict[str, Any] = dict(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=1,
            nonce="a" * 32,
            issued_at=datetime.now(timezone.utc),
            expiration_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        kwargs.update(overrides)
        return build_siwe_message(**kwargs)

    def test_build_parse_round_trip(self) -> None:
        account = Account.create()
        issued = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
        text = build_siwe_message(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=8453,
            nonce="f00dfeed" * 4,
            issued_at=issued,
            expiration_time=issued + timedelta(minutes=5),
        )
        parsed = parse_siwe_message(text)
        assert parsed.domain == _DOMAIN
        assert parsed.address == account.address  # EIP-55 casing preserved
        assert parsed.address_lower == account.address.lower()
        assert parsed.statement == SIWE_STATEMENT
        assert parsed.uri == _ORIGIN
        assert parsed.version == "1"
        assert parsed.chain_id == 8453
        assert parsed.nonce == "f00dfeed" * 4
        assert parsed.issued_at == "2026-07-17T12:00:00Z"
        assert parsed.expiration_time == "2026-07-17T12:05:00Z"

    def test_rejects_wrong_preamble(self) -> None:
        text = self._message().replace(
            "wants you to sign in with your Ethereum account:",
            "wants you to sign in:",
        )
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(text)

    def test_rejects_version_2(self) -> None:
        text = self._message().replace("Version: 1", "Version: 2")
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(text)

    def test_rejects_missing_expiration_time(self) -> None:
        lines = self._message().split("\n")
        text = "\n".join(line for line in lines if not line.startswith("Expiration"))
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(text)

    def test_rejects_out_of_order_fields(self) -> None:
        lines = self._message().split("\n")
        nonce_idx = next(i for i, line in enumerate(lines) if line.startswith("Nonce:"))
        chain_idx = next(
            i for i, line in enumerate(lines) if line.startswith("Chain ID:")
        )
        lines[nonce_idx], lines[chain_idx] = lines[chain_idx], lines[nonce_idx]
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message("\n".join(lines))

    def test_rejects_short_or_symbolic_nonce(self) -> None:
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(self._message(nonce="short"))
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(self._message(nonce="abc-def-ghi-jkl"))

    def test_rejects_non_rfc3339_timestamps(self) -> None:
        text = self._message()
        issued_line = next(
            line for line in text.split("\n") if line.startswith("Issued At:")
        )
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(text.replace(issued_line, "Issued At: yesterday"))

    def test_rejects_trailing_garbage(self) -> None:
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message(self._message() + "\nExtra: field")

    def test_rejects_empty_message(self) -> None:
        with pytest.raises(SiweMessageInvalid):
            parse_siwe_message("")

    def test_accepts_optional_trailer_fields(self) -> None:
        text = (
            self._message()
            + "\nRequest ID: req-123"
            + "\nResources:"
            + "\n- https://example.com/resource"
        )
        parsed = parse_siwe_message(text)
        assert parsed.request_id == "req-123"
        assert parsed.resources == ("https://example.com/resource",)


# ---------------------------------------------------------------------------
# Service-level flow fixture
# ---------------------------------------------------------------------------


class SiweFlowFixtureMixin:
    def build(
        self,
        *,
        allow_self_signup: bool = True,
        seed_admin_role: bool = True,
        allowed_chain_ids: frozenset[int] | None = None,
        rate_limiter: InMemoryRateLimiter | None = None,
        expected_origin: str = _ORIGIN,
    ) -> tuple[SiweService, dict[str, Any]]:
        identity_store = InMemoryIdentityStore()
        siwe_store = InMemorySiweStore()
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
                    permission_scopes=("admin:users", "admin:idp", "runtime:use"),
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
        service = SiweService(
            identity_store=identity_store,
            siwe_store=siwe_store,
            sessions=sessions,
            expected_origin=expected_origin,
            allowed_chain_ids=allowed_chain_ids,
            allow_self_signup=allow_self_signup,
            rate_limiter=rate_limiter,
        )
        return service, {
            "identity_store": identity_store,
            "siwe_store": siwe_store,
            "sessions": sessions,
        }

    def signed_message(
        self,
        service: SiweService,
        account: Any,
        *,
        chain_id: int = 8453,
        domain: str = _DOMAIN,
        uri: str = _ORIGIN,
        nonce: str | None = None,
        issued_at: datetime | None = None,
        expiration_time: datetime | None = None,
    ) -> tuple[str, str]:
        if nonce is None:
            nonce = service.mint_nonce(address=account.address, chain_id=chain_id).nonce
        now = datetime.now(timezone.utc)
        message = build_siwe_message(
            domain=domain,
            address=account.address,
            uri=uri,
            chain_id=chain_id,
            nonce=nonce,
            issued_at=issued_at or now,
            expiration_time=expiration_time or now + timedelta(minutes=5),
        )
        return message, _sign(account, message)

    def round_trip(self, service: SiweService, account: Any, **kwargs: Any) -> Any:
        message, signature = self.signed_message(service, account, **kwargs)
        return service.verify(message=message, signature=signature)


class TestNonceMint(SiweFlowFixtureMixin):
    def test_mints_single_use_nonce_with_ttl(self) -> None:
        service, ctx = self.build()
        account = Account.create()
        result = service.mint_nonce(address=account.address, chain_id=1)
        assert result.nonce.isalnum() and len(result.nonce) >= 8
        record = next(iter(ctx["siwe_store"].nonces.values()))
        assert record.address == account.address.lower()
        assert record.chain_id == 1
        assert record.consumed_at is None
        ttl = (record.expires_at - record.issued_at).total_seconds()
        assert 0 < ttl <= 600  # contract cap: 10 minutes

    def test_default_allowlist_includes_robinhood_chain(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        # 4663 (Robinhood Chain) is allowlisted by default.
        assert service.mint_nonce(address=account.address, chain_id=4663).nonce

    def test_rejects_disallowed_chain(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        with pytest.raises(SiweChainNotAllowed):
            service.mint_nonce(address=account.address, chain_id=999)

    def test_rejects_malformed_address(self) -> None:
        service, _ctx = self.build()
        with pytest.raises(SiweAddressInvalid):
            service.mint_nonce(address="0xnothex", chain_id=1)

    def test_rate_limited_after_burst(self) -> None:
        service, _ctx = self.build(rate_limiter=InMemoryRateLimiter())
        account = Account.create()
        with pytest.raises(SiweRateLimited) as excinfo:
            for _ in range(31):
                service.mint_nonce(
                    address=account.address, chain_id=1, ip="203.0.113.9"
                )
        assert excinfo.value.retry_after_seconds >= 1


class TestVerifyHappyPaths(SiweFlowFixtureMixin):
    def test_first_login_provisions_personal_org_wallet_link_session(self) -> None:
        service, ctx = self.build()
        account = Account.create()
        result = self.round_trip(service, account)

        store = ctx["identity_store"]
        address_lower = account.address.lower()
        truncated = f"{account.address[:6]}…{account.address[-4:]}"

        # Personal org named from the truncated EIP-55 address.
        org = next(iter(store.organizations.values()))
        assert org.display_name == f"{truncated}'s Workspace"

        user = store.get_user(org_id=org.org_id, user_id=result.user_id)
        assert user is not None
        assert user.display_name == truncated
        # No wallet email exists — placeholder on the reserved .invalid TLD.
        assert user.primary_email == f"{address_lower}@wallet.invalid"
        assert user.email_verified_at is None

        members = store.list_members(org_id=org.org_id)
        assert [m.user_id for m in members] == [user.user_id]
        assert members[0].source.value == "siwe"

        # Sole member of a personal org is its admin.
        assignments = store.list_role_assignments(
            org_id=org.org_id, user_id=user.user_id
        )
        role_names = {store.get_role(role_id=a.role_id).name for a in assignments}
        assert role_names == {"admin"}

        # Wallet link exists, stored lowercase.
        link = ctx["siwe_store"].get_wallet_identity(address=address_lower)
        assert link is not None
        assert link.user_id == user.user_id
        assert link.org_id == org.org_id
        assert link.address == address_lower
        assert link.chain_id == 8453

        # Session shape mirrors the OIDC callback result.
        assert result.session_id.startswith("sid_")
        assert "." in result.bearer_token
        assert result.requires_mfa is False
        assert result.return_to is None

        # Audit trail lands in the NEW personal org.
        actions = [e.action for e in store.list_identity_audit(org_id=org.org_id)]
        assert "siwe.self_signup_org_created" in actions
        assert "siwe.user_provisioned" in actions
        # Sessions record the pseudo-provider id.
        session_record = next(iter(ctx["sessions"]._store.sessions.values()))
        assert session_record.auth_provider_id == SIWE_PROVIDER_ID

    def test_second_login_links_to_existing_user_and_org(self) -> None:
        service, ctx = self.build()
        account = Account.create()
        first = self.round_trip(service, account)
        second = self.round_trip(service, account)
        assert first.user_id == second.user_id
        assert first.session_id != second.session_id
        assert len(ctx["identity_store"].organizations) == 1
        actions = [
            e.action
            for e in ctx["identity_store"].list_identity_audit(
                org_id=next(iter(ctx["identity_store"].organizations))
            )
        ]
        assert "siwe.verify_succeeded" in actions

    def test_checksum_normalization_lowercase_message_address(self) -> None:
        # A wallet that renders the address all-lowercase still verifies,
        # links to the same stored (lowercase) identity, and the recovered
        # signer matches case-insensitively.
        service, ctx = self.build()
        account = Account.create()
        first = self.round_trip(service, account)

        nonce = service.mint_nonce(address=account.address.lower(), chain_id=8453)
        now = datetime.now(timezone.utc)
        message = build_siwe_message(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=8453,
            nonce=nonce.nonce,
            issued_at=now,
            expiration_time=now + timedelta(minutes=5),
        )
        # Force the address line to lowercase (no EIP-55 checksum → legal).
        message = message.replace(account.address, account.address.lower(), 1)
        result = service.verify(message=message, signature=_sign(account, message))
        assert result.user_id == first.user_id
        assert len(ctx["identity_store"].organizations) == 1

    def test_two_wallets_get_two_personal_orgs(self) -> None:
        service, ctx = self.build()
        first = self.round_trip(service, Account.create())
        second = self.round_trip(service, Account.create())
        assert first.user_id != second.user_id
        assert len(ctx["identity_store"].organizations) == 2

    def test_slug_collision_gets_random_suffix(self) -> None:
        service, ctx = self.build()
        account = Account.create()
        address_lower = account.address.lower()
        taken = f"{address_lower[:6]}-{address_lower[-4:]}"
        ctx["identity_store"].create_organization(
            OrganizationRecord(display_name="Taken", slug=taken)
        )
        self.round_trip(service, account)
        slugs = {o.slug for o in ctx["identity_store"].organizations.values()}
        assert taken in slugs
        assert any(s.startswith(f"{taken}-") for s in slugs)


class TestVerifyRejections(SiweFlowFixtureMixin):
    def test_nonce_replay_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(service, account)
        service.verify(message=message, signature=signature)
        with pytest.raises(SiweNonceInvalid):
            service.verify(message=message, signature=signature)

    def test_unknown_nonce_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(service, account, nonce="feedbead" * 4)
        with pytest.raises(SiweNonceInvalid):
            service.verify(message=message, signature=signature)

    def test_expired_nonce_rejected(self) -> None:
        service, ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(
            service,
            account,
            # Keep the MESSAGE window open while the NONCE row expires, so
            # the failure is attributable to the nonce specifically.
            expiration_time=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        for nonce_id, record in ctx["siwe_store"].nonces.items():
            ctx["siwe_store"].nonces[nonce_id] = record.model_copy(
                update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
            )
        with pytest.raises(SiweNonceExpired):
            service.verify(message=message, signature=signature)

    def test_expired_message_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        now = datetime.now(timezone.utc)
        message, signature = self.signed_message(
            service,
            account,
            issued_at=now - timedelta(minutes=10),
            expiration_time=now - timedelta(minutes=1),
        )
        with pytest.raises(SiweExpiredMessage):
            service.verify(message=message, signature=signature)

    def test_wrong_domain_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(
            service, account, domain="evil.example", uri="https://evil.example"
        )
        with pytest.raises(SiweDomainMismatch):
            service.verify(message=message, signature=signature)

    def test_matching_domain_but_foreign_uri_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(
            service, account, uri="https://evil.example/callback"
        )
        with pytest.raises(SiweDomainMismatch):
            service.verify(message=message, signature=signature)

    def test_tampered_message_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(service, account)
        tampered = message.replace("Chain ID: 8453", "Chain ID: 1")
        with pytest.raises(SiweSignatureInvalid):
            service.verify(message=tampered, signature=signature)

    def test_signature_from_wrong_key_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        interloper = Account.create()
        message, _ = self.signed_message(service, account)
        with pytest.raises(SiweSignatureInvalid):
            service.verify(message=message, signature=_sign(interloper, message))

    def test_garbage_signature_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, _ = self.signed_message(service, account)
        with pytest.raises(SiweSignatureInvalid):
            service.verify(message=message, signature="0xdeadbeef")

    def test_chain_allowlist_enforced_on_verify(self) -> None:
        # Nonce minted while a chain was allowed; verify must still refuse
        # messages for chains outside the CURRENT allowlist.
        service, _ctx = self.build(allowed_chain_ids=frozenset({1, 999}))
        account = Account.create()
        message, signature = self.signed_message(service, account, chain_id=999)
        strict, _ = self.build(allowed_chain_ids=frozenset({1}))
        with pytest.raises(SiweChainNotAllowed):
            strict.verify(message=message, signature=signature)

    def test_wrong_statement_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        nonce = service.mint_nonce(address=account.address, chain_id=8453).nonce
        now = datetime.now(timezone.utc)
        message = build_siwe_message(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=8453,
            nonce=nonce,
            issued_at=now,
            expiration_time=now + timedelta(minutes=5),
            statement="Sign in to Definitely Not Atlas",
        )
        with pytest.raises(SiweMessageInvalid):
            service.verify(message=message, signature=_sign(account, message))

    def test_nonce_minted_for_other_address_rejected(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        other = Account.create()
        foreign_nonce = service.mint_nonce(address=other.address, chain_id=8453).nonce
        message, signature = self.signed_message(service, account, nonce=foreign_nonce)
        with pytest.raises(SiweNonceInvalid):
            service.verify(message=message, signature=signature)

    def test_failed_signature_does_not_burn_the_nonce(self) -> None:
        service, _ctx = self.build()
        account = Account.create()
        message, signature = self.signed_message(service, account)
        with pytest.raises(SiweSignatureInvalid):
            service.verify(message=message, signature="0x" + "ab" * 65)
        # Same nonce still valid with the honest signature.
        assert service.verify(message=message, signature=signature).user_id

    def test_signup_refused_when_flag_off(self) -> None:
        service, ctx = self.build(allow_self_signup=False)
        account = Account.create()
        message, signature = self.signed_message(service, account)
        with pytest.raises(SiweSelfSignupDisabled):
            service.verify(message=message, signature=signature)
        # Nothing provisioned.
        assert len(ctx["identity_store"].organizations) == 0
        attempts = ctx["identity_store"].list_login_attempts(org_id=SIWE_GLOBAL_ORG_ID)
        assert any(a.outcome.value == "unknown_user" for a in attempts)

    def test_flag_off_still_allows_already_linked_wallet(self) -> None:
        service, ctx = self.build(allow_self_signup=True)
        account = Account.create()
        first = self.round_trip(service, account)
        strict = SiweService(
            identity_store=ctx["identity_store"],
            siwe_store=ctx["siwe_store"],
            sessions=ctx["sessions"],
            expected_origin=_ORIGIN,
            allow_self_signup=False,
        )
        second = self.round_trip(strict, account)
        assert second.user_id == first.user_id


# ---------------------------------------------------------------------------
# Route-level: internal endpoints speak the frozen wire contract
# ---------------------------------------------------------------------------


class TestSiweRoutes(SiweFlowFixtureMixin):
    def _client(self, monkeypatch, **env: str) -> TestClient:
        monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", _AUTH_SECRET)
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _TEST_SERVICE_TOKEN)
        monkeypatch.delenv("ENTERPRISE_DEPLOYMENT_PROFILE", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        monkeypatch.setenv("SIWE_ORIGIN", _ORIGIN)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return TestClient(create_app())

    def _headers(self) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _TEST_SERVICE_TOKEN,
            "x-enterprise-org-id": "-",
            "x-enterprise-user-id": "anonymous",
        }

    def test_nonce_route_contract(self, monkeypatch) -> None:
        client = self._client(monkeypatch)
        account = Account.create()
        response = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 8453},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"nonce", "expires_at"}
        assert len(body["nonce"]) >= 8

    def test_nonce_route_422_on_invalid_address(self, monkeypatch) -> None:
        client = self._client(monkeypatch)
        response = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": "0xzz", "chain_id": 1},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "invalid_address"

    def test_nonce_route_400_on_disallowed_chain(self, monkeypatch) -> None:
        client = self._client(monkeypatch)
        account = Account.create()
        response = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 999},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "chain_not_allowed"

    def test_chain_allowlist_env_override(self, monkeypatch) -> None:
        client = self._client(monkeypatch, SIWE_ALLOWED_CHAIN_IDS="10")
        account = Account.create()
        ok = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 10},
        )
        assert ok.status_code == 200
        rejected = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 1},
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == "chain_not_allowed"

    def test_verify_route_end_to_end_and_error_codes(self, monkeypatch) -> None:
        client = self._client(monkeypatch)
        account = Account.create()
        nonce_body = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 8453},
        ).json()
        now = datetime.now(timezone.utc)
        message = build_siwe_message(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=8453,
            nonce=nonce_body["nonce"],
            issued_at=now,
            expiration_time=now + timedelta(minutes=5),
        )
        response = client.post(
            "/internal/v1/auth/siwe/verify",
            headers=self._headers(),
            json={"message": message, "signature": _sign(account, message)},
        )
        assert response.status_code == 200
        body = response.json()
        # Same session-establishing shape as the OIDC callback.
        assert set(body) == {
            "user_id",
            "session_id",
            "bearer_token",
            "expires_at",
            "return_to",
            "requires_mfa",
        }
        assert body["requires_mfa"] is False

        # Replay → nonce_invalid.
        replay = client.post(
            "/internal/v1/auth/siwe/verify",
            headers=self._headers(),
            json={"message": message, "signature": _sign(account, message)},
        )
        assert replay.status_code == 400
        assert replay.json()["detail"] == "nonce_invalid"

        # Garbage signature → signature_invalid (fresh nonce).
        nonce2 = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 8453},
        ).json()["nonce"]
        message2 = build_siwe_message(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=8453,
            nonce=nonce2,
            issued_at=now,
            expiration_time=now + timedelta(minutes=5),
        )
        bad_sig = client.post(
            "/internal/v1/auth/siwe/verify",
            headers=self._headers(),
            json={"message": message2, "signature": "0x" + "cd" * 65},
        )
        assert bad_sig.status_code == 400
        assert bad_sig.json()["detail"] == "signature_invalid"

        # Unparseable message → message_invalid.
        broken = client.post(
            "/internal/v1/auth/siwe/verify",
            headers=self._headers(),
            json={"message": "not a siwe message", "signature": "0x00"},
        )
        assert broken.status_code == 400
        assert broken.json()["detail"] == "message_invalid"

    def test_verify_route_403_when_self_signup_disabled(self, monkeypatch) -> None:
        # single_tenant_self_hosted profile: allow_self_signup=False.
        client = self._client(
            monkeypatch,
            ENTERPRISE_DEPLOYMENT_PROFILE="single_tenant_self_hosted",
        )
        account = Account.create()
        nonce = client.post(
            "/internal/v1/auth/siwe/nonce",
            headers=self._headers(),
            json={"address": account.address, "chain_id": 1},
        ).json()["nonce"]
        now = datetime.now(timezone.utc)
        message = build_siwe_message(
            domain=_DOMAIN,
            address=account.address,
            uri=_ORIGIN,
            chain_id=1,
            nonce=nonce,
            issued_at=now,
            expiration_time=now + timedelta(minutes=5),
        )
        response = client.post(
            "/internal/v1/auth/siwe/verify",
            headers=self._headers(),
            json={"message": message, "signature": _sign(account, message)},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "self_signup_disabled"
