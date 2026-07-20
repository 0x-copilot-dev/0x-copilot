"""Authenticated wallet link — SiweService.link_wallet (PRD FR-L1/L3/L6/M1).

Real signature vectors like test_siwe.py: throwaway secp256k1 keys signed via
EIP-191 personal_sign drive the full proof pipeline — no mocked recovery. The
distinguishing behaviors under test: the wallet binds to the CALLER (never
provisions), no session is minted, re-linking my own wallet is a no-op, and a
wallet owned by another account raises the merge trigger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from backend_app.contracts import (
    OrganizationRecord,
    UserRecord,
    WalletIdentityRecord,
)
from backend_app.identity import (
    InMemoryIdentityStore,
    InMemorySessionStore,
    InMemorySiweStore,
    SessionService,
    SiweNonceInvalid,
    SiweService,
    SiweSignatureInvalid,
    SiweWalletAlreadyLinked,
    build_siwe_message,
    display_address,
)

_AUTH_SECRET = "test-auth-secret-siwe-0123456789"
_ORIGIN = "http://localhost:5173"
_DOMAIN = "localhost:5173"


def _sign(account: Any, message: str) -> str:
    return account.sign_message(encode_defunct(text=message)).signature.to_0x_hex()


def _build() -> tuple[SiweService, InMemoryIdentityStore, InMemorySiweStore]:
    identity_store = InMemoryIdentityStore()
    siwe_store = InMemorySiweStore()
    sessions = SessionService(
        store=InMemorySessionStore(),
        auth_secret=_AUTH_SECRET,
        dev_mint_allowed=True,
    )
    identity_store.create_organization(
        OrganizationRecord(org_id="org_caller", display_name="Caller", slug="caller")
    )
    identity_store.create_user(
        UserRecord(
            user_id="usr_caller",
            org_id="org_caller",
            primary_email="caller@acme.com",
            display_name="Caller",
        )
    )
    service = SiweService(
        identity_store=identity_store,
        siwe_store=siwe_store,
        sessions=sessions,
        expected_origin=_ORIGIN,
        # Linking must work even where self-signup is OFF — the caller is
        # already provisioned (PRD §6.2).
        allow_self_signup=False,
    )
    return service, identity_store, siwe_store


def _signed(
    service: SiweService, account: Any, *, chain_id: int = 8453
) -> tuple[str, str]:
    nonce = service.mint_nonce(address=account.address, chain_id=chain_id).nonce
    now = datetime.now(timezone.utc)
    message = build_siwe_message(
        domain=_DOMAIN,
        address=account.address,
        uri=_ORIGIN,
        chain_id=chain_id,
        nonce=nonce,
        issued_at=now,
        expiration_time=now + timedelta(minutes=5),
    )
    return message, _sign(account, message)


class TestLinkWallet:
    def test_links_to_caller_without_provision_or_session(self) -> None:
        service, identity_store, siwe_store = _build()
        account = Account.create()
        message, signature = _signed(service, account)

        result = service.link_wallet(
            org_id="org_caller",
            user_id="usr_caller",
            message=message,
            signature=signature,
        )

        assert result.status == "linked"
        assert result.address == display_address(account.address)
        assert result.chain_id == 8453
        assert result.chain_name == "Base"
        # Bound to the CALLER — no new org/user was provisioned.
        row = siwe_store.get_wallet_identity(address=account.address.lower())
        assert row is not None
        assert (row.org_id, row.user_id) == ("org_caller", "usr_caller")
        assert len(identity_store.users) == 1
        # Its own audit action — not a login.
        actions = [e.action for e in identity_store.identity_audit_events]
        assert "siwe.wallet_linked" in actions
        assert "siwe.verify_succeeded" not in actions

    def test_relink_same_wallet_is_idempotent_noop(self) -> None:
        service, _identity_store, siwe_store = _build()
        account = Account.create()
        message, signature = _signed(service, account)
        first = service.link_wallet(
            org_id="org_caller",
            user_id="usr_caller",
            message=message,
            signature=signature,
        )
        # A SECOND link of the same wallet (fresh nonce) — FR-L6 no-op.
        message2, signature2 = _signed(service, account)
        second = service.link_wallet(
            org_id="org_caller",
            user_id="usr_caller",
            message=message2,
            signature=signature2,
        )
        assert second.status == "already_linked"
        assert second.wallet_id == first.wallet_id
        assert len(siwe_store.wallet_identities) == 1

    def test_wallet_owned_by_another_account_raises_merge_trigger(self) -> None:
        service, _identity_store, siwe_store = _build()
        account = Account.create()
        # The wallet already belongs to a DIFFERENT account.
        siwe_store.create_wallet_identity(
            WalletIdentityRecord(
                address=account.address.lower(),
                org_id="org_other",
                user_id="usr_other",
                chain_id=1,
            )
        )
        message, signature = _signed(service, account)
        with pytest.raises(SiweWalletAlreadyLinked) as exc_info:
            service.link_wallet(
                org_id="org_caller",
                user_id="usr_caller",
                message=message,
                signature=signature,
            )
        assert exc_info.value.org_id == "org_other"
        assert exc_info.value.user_id == "usr_other"

    def test_invalid_signature_refused(self) -> None:
        service, _identity_store, siwe_store = _build()
        account = Account.create()
        interloper = Account.create()
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
        )
        # Signed by a DIFFERENT key than the address in the message.
        with pytest.raises(SiweSignatureInvalid):
            service.link_wallet(
                org_id="org_caller",
                user_id="usr_caller",
                message=message,
                signature=_sign(interloper, message),
            )
        assert len(siwe_store.wallet_identities) == 0

    def test_nonce_replay_refused(self) -> None:
        service, _identity_store, _siwe_store = _build()
        account = Account.create()
        message, signature = _signed(service, account)
        service.link_wallet(
            org_id="org_caller",
            user_id="usr_caller",
            message=message,
            signature=signature,
        )
        # Replaying the SAME signed message — the nonce is consumed.
        with pytest.raises(SiweNonceInvalid):
            service.link_wallet(
                org_id="org_caller",
                user_id="usr_caller",
                message=message,
                signature=signature,
            )


class TestUnlinkRoutes:
    """FR-L5 — unlink with the last-method guard, over the full app."""

    def _client(self) -> tuple[Any, InMemorySiweStore]:
        from fastapi.testclient import TestClient

        from backend_app.app import create_app
        from backend_app.contracts import (
            OrganizationMemberRecord,
            OrganizationMemberSource,
            WalletIdentityRecord,
        )

        identity_store = InMemoryIdentityStore()
        siwe_store = InMemorySiweStore()
        identity_store.create_organization(
            OrganizationRecord(
                org_id="org_caller", display_name="Caller", slug="caller"
            )
        )
        identity_store.create_user(
            UserRecord(
                user_id="usr_caller",
                org_id="org_caller",
                primary_email="caller@acme.com",
                display_name="Caller",
            )
        )
        identity_store.add_member(
            OrganizationMemberRecord(
                org_id="org_caller",
                user_id="usr_caller",
                source=OrganizationMemberSource.SIWE,
            )
        )
        wallet_a = Account.create()
        wallet_b = Account.create()
        for account, chain in ((wallet_a, 8453), (wallet_b, 1)):
            siwe_store.create_wallet_identity(
                WalletIdentityRecord(
                    address=account.address.lower(),
                    org_id="org_caller",
                    user_id="usr_caller",
                    chain_id=chain,
                )
            )
        sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        app = create_app(
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
            identity_store=identity_store,
            siwe_store=siwe_store,
            session_service=sessions,
        )
        return TestClient(app), siwe_store

    def test_unlink_wallet_then_last_method_guard(self) -> None:
        client, siwe_store = self._client()
        wallets = siwe_store.list_wallets_by_user(
            org_id="org_caller", user_id="usr_caller"
        )
        assert len(wallets) == 2
        params = {"org_id": "org_caller", "user_id": "usr_caller"}

        # Two wallets → the first unlink succeeds.
        first = client.delete(
            f"/internal/v1/me/identities/wallet/{wallets[0].wallet_id}",
            params=params,
        )
        assert first.status_code == 204
        assert (
            len(
                siwe_store.list_wallets_by_user(
                    org_id="org_caller", user_id="usr_caller"
                )
            )
            == 1
        )

        # One wallet left → FR-L5: refuse to remove the last sign-in method.
        second = client.delete(
            f"/internal/v1/me/identities/wallet/{wallets[1].wallet_id}",
            params=params,
        )
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "last_sign_in_method"

    def test_unlink_foreign_wallet_is_404(self) -> None:
        client, siwe_store = self._client()
        # A wallet id belonging to nobody / another user → 404, never leaked.
        response = client.delete(
            "/internal/v1/me/identities/wallet/wid_deadbeef",
            params={"org_id": "org_caller", "user_id": "usr_caller"},
        )
        assert response.status_code == 404
