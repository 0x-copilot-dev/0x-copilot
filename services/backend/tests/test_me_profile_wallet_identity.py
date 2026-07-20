"""Honest wallet identity on /me/profile (Issues 3 + 4).

A SIWE account has no real email — the ``<address>@wallet.invalid`` placeholder
anchored at signup must never be shown as the user's address. These tests pin
the projection directly: the placeholder/chain helpers, the by-user wallet
reverse lookup, and ``_hydrate`` (which materialises the response). The trivial
``app.state.siwe_store`` → route wiring runs in the full-app boot (the SIWE
auth block is gated on a live session service, absent in this unit harness).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend_app.contracts import WalletIdentityRecord
from backend_app.identity.siwe import (
    WALLET_PLACEHOLDER_EMAIL_DOMAIN,
    chain_display_name,
    display_address,
    is_placeholder_email,
)
from backend_app.identity.siwe_store import InMemorySiweStore
from backend_app.routes.me_profile import _hydrate

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
# Two canonical EIP-55 addresses, stored lowercase (WalletIdentityRecord
# validates address format, so these must be real).
_ADDR = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
_ADDR2 = "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359"


def _user(email: str, *, verified: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        user_id="usr_1",
        primary_email=email,
        email_verified_at=_AT if verified else None,
        display_name="Someone",
        updated_at=_AT,
    )


class TestPlaceholderEmail:
    def test_recognizes_wallet_placeholder(self) -> None:
        assert is_placeholder_email(f"0xabc@{WALLET_PLACEHOLDER_EMAIL_DOMAIN}") is True
        # Case-insensitive.
        assert is_placeholder_email("0xABC@Wallet.Invalid") is True

    def test_real_email_is_not_placeholder(self) -> None:
        assert is_placeholder_email("sarah@acme.com") is False
        assert is_placeholder_email(None) is False
        assert is_placeholder_email("") is False


class TestChainDisplayName:
    def test_known_chains(self) -> None:
        assert chain_display_name(1) == "Ethereum"
        assert chain_display_name(8453) == "Base"
        assert chain_display_name(42161) == "Arbitrum One"
        assert chain_display_name(4663) == "Robinhood Chain"

    def test_unknown_chain_falls_back(self) -> None:
        assert chain_display_name(999999) == "Chain 999999"


class TestWalletReverseLookup:
    def test_by_user_returns_first_linked(self) -> None:
        store = InMemorySiweStore()
        # Linked LATER (Jan 2), chain 1.
        store.create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR,
                org_id="org",
                user_id="u1",
                chain_id=1,
                created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        # Linked EARLIER (Jan 1), chain 8453 — this is "the" profile wallet.
        store.create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR2,
                org_id="org",
                user_id="u1",
                chain_id=8453,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        found = store.get_wallet_identity_by_user(org_id="org", user_id="u1")
        assert found is not None
        assert found.chain_id == 8453

    def test_none_for_non_wallet_user(self) -> None:
        store = InMemorySiweStore()
        assert store.get_wallet_identity_by_user(org_id="org", user_id="nope") is None


class TestHydrateProjection:
    def test_wallet_user_projection(self) -> None:
        user = _user(f"{_ADDR}@{WALLET_PLACEHOLDER_EMAIL_DOMAIN}")
        wallet = WalletIdentityRecord(
            address=_ADDR, org_id="org", user_id="usr_1", chain_id=8453
        )
        resp = _hydrate(user, None, wallet=wallet, auth_method="siwe")
        assert resp.email_is_placeholder is True
        # The honest anchor: checksummed address + chain (never the placeholder).
        assert resp.wallet_address == display_address(_ADDR)
        assert resp.chain_id == 8453
        assert resp.chain_name == "Base"
        assert resp.auth_method == "siwe"

    def test_email_user_projection(self) -> None:
        resp = _hydrate(
            _user("sarah@acme.com", verified=True), None, auth_method="google"
        )
        assert resp.email_is_placeholder is False
        assert resp.wallet_address is None
        assert resp.chain_id is None
        assert resp.chain_name is None
        assert resp.auth_method == "google"


class TestLinkedIdentities:
    """Account-linking PRD FR-L4 — the linked_identities list (PR2 reads)."""

    def test_list_wallets_by_user_oldest_first(self) -> None:
        store = InMemorySiweStore()
        store.create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR,
                org_id="org",
                user_id="u1",
                chain_id=1,
                created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        store.create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR2,
                org_id="org",
                user_id="u1",
                chain_id=8453,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        wallets = store.list_wallets_by_user(org_id="org", user_id="u1")
        assert [w.chain_id for w in wallets] == [8453, 1]
        # The singular lookup still returns the first-linked wallet.
        first = store.get_wallet_identity_by_user(org_id="org", user_id="u1")
        assert first is not None and first.chain_id == 8453

    def test_list_oidc_identities_by_user_excludes_unlinked(self) -> None:
        from backend_app.contracts import OidcIdentityRecord
        from backend_app.identity.oidc_store import InMemoryOidcStore

        store = InMemoryOidcStore()
        store.create_identity(
            OidcIdentityRecord(
                org_id="org",
                user_id="u1",
                provider_id="google",
                subject="sub-1",
                email_at_link="sarah@gmail.com",
            )
        )
        # A different user's identity and an unlinked row must not appear.
        store.create_identity(
            OidcIdentityRecord(
                org_id="org", user_id="u2", provider_id="google", subject="sub-2"
            )
        )
        unlinked = OidcIdentityRecord(
            org_id="org",
            user_id="u1",
            provider_id="google",
            subject="sub-3",
            unlinked_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        store.identities[unlinked.identity_id] = unlinked
        idents = store.list_identities_by_user(org_id="org", user_id="u1")
        assert [i.subject for i in idents] == ["sub-1"]
        assert idents[0].email_at_link == "sarah@gmail.com"

    def test_resolve_linked_identities_projection(self) -> None:
        from backend_app.contracts import OidcIdentityRecord
        from backend_app.identity.oidc_store import InMemoryOidcStore
        from backend_app.routes.me_profile import _resolve_linked_identities

        siwe = InMemorySiweStore()
        siwe.create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR, org_id="org", user_id="u1", chain_id=8453
            )
        )
        oidc = InMemoryOidcStore()
        oidc.create_identity(
            OidcIdentityRecord(
                org_id="org",
                user_id="u1",
                provider_id="google",
                subject="sub-1",
                email_at_link="sarah@gmail.com",
            )
        )
        linked = _resolve_linked_identities(siwe, oidc, "org", "u1")
        kinds = {entry.kind for entry in linked}
        assert kinds == {"wallet", "oidc"}
        wallet_entry = next(e for e in linked if e.kind == "wallet")
        # Checksummed for display, chain named, id present for future unlink.
        assert wallet_entry.address == display_address(_ADDR)
        assert wallet_entry.chain_name == "Base"
        assert wallet_entry.id.startswith("wid_")
        oidc_entry = next(e for e in linked if e.kind == "oidc")
        assert oidc_entry.provider == "google"
        assert oidc_entry.email == "sarah@gmail.com"

    def test_resolve_linked_identities_absent_stores(self) -> None:
        from backend_app.routes.me_profile import _resolve_linked_identities

        assert _resolve_linked_identities(None, None, "org", "u1") == []
