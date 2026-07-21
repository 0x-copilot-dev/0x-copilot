"""Principal/tenant separation — expand-stage invariants (ADR 0001).

Stage 1 adds the `principal` above the (org, user) tuple and dual-writes it on
every new user. These tests pin the invariant that NO user is ever created
without a principal, that the id is the deterministic 1:1 ``prn_<user_id>``
(so app writes and the migration backfill agree), and that provisioning — the
real self-signup path — carries it end to end. No reader depends on it yet.
"""

from __future__ import annotations

from backend_app.contracts import (
    OrganizationMemberSource,
    OrganizationRecord,
    PrincipalRecord,
    UserRecord,
)
from backend_app.identity.provisioning import provision_personal_org
from backend_app.identity.store import InMemoryIdentityStore, _default_principal_id


def _store_with_org() -> tuple[InMemoryIdentityStore, str]:
    store = InMemoryIdentityStore()
    org = store.create_organization(
        OrganizationRecord(org_id="org_x", display_name="X", slug="x")
    )
    return store, org.org_id


class TestCreateUserAutoMintsPrincipal:
    def test_user_without_principal_gets_deterministic_one(self) -> None:
        store, org_id = _store_with_org()
        user = store.create_user(
            UserRecord(
                user_id="usr_1", org_id=org_id, primary_email="a@x.io", display_name="A"
            )
        )
        assert user.principal_id == "prn_usr_1" == _default_principal_id("usr_1")
        principal = store.get_principal(principal_id="prn_usr_1")
        assert principal is not None
        assert principal.display_name == "A"

    def test_explicit_principal_id_is_honored(self) -> None:
        store, org_id = _store_with_org()
        user = store.create_user(
            UserRecord(
                user_id="usr_2",
                org_id=org_id,
                primary_email="b@x.io",
                display_name="B",
                principal_id="prn_shared",
            )
        )
        # A caller that supplies a principal owns its existence — the store
        # does not mint a second one (this is the future link path).
        assert user.principal_id == "prn_shared"

    def test_create_principal_is_retrievable(self) -> None:
        store, _ = _store_with_org()
        store.create_principal(PrincipalRecord(principal_id="prn_z", display_name="Z"))
        got = store.get_principal(principal_id="prn_z")
        assert got is not None and got.principal_id == "prn_z"
        assert store.get_principal(principal_id="prn_missing") is None


class TestProvisioningCarriesPrincipal:
    def test_provision_personal_org_sets_principal_on_the_user(self) -> None:
        store = InMemoryIdentityStore()
        _org, user = provision_personal_org(
            identity_store=store,
            org_display_name="Acme",
            slug_base="acme",
            primary_email="founder@acme.test",
            user_display_name="Founder",
            email_verified_at=None,
            member_source=OrganizationMemberSource.SIWE,
            audit_events=lambda _o, _u: (),
        )
        assert user.principal_id == f"prn_{user.user_id}"
        assert store.get_principal(principal_id=user.principal_id) is not None
        # And the stored user (not just the returned copy) carries it.
        stored = store.get_user(org_id=user.org_id, user_id=user.user_id)
        assert stored is not None and stored.principal_id == user.principal_id


# ---------------------------------------------------------------------------
# Stage 2a — auth-identity EDGE tables dual-write the principal (ADR 0001).
# ---------------------------------------------------------------------------

_ADDR = "0x" + "a" * 40


class TestIdentityEdgesDualWritePrincipal:
    """Every auth-identity edge (wallet / OIDC / SAML) fills principal_id from
    its user when a caller supplies none — the same 1:1 ``prn_<user_id>`` the
    0040 backfill uses — and honors an explicit principal (the future
    explicit-link path). No resolver reads it yet (that is Stage 2b)."""

    def test_wallet_identity_auto_fills_principal(self) -> None:
        from backend_app.contracts import WalletIdentityRecord
        from backend_app.identity.siwe_store import InMemorySiweStore

        rec = InMemorySiweStore().create_wallet_identity(
            WalletIdentityRecord(
                address=_ADDR, org_id="org_a", user_id="usr_a", chain_id=8453
            )
        )
        assert rec.principal_id == "prn_usr_a"

    def test_oidc_identity_auto_fills_principal(self) -> None:
        from backend_app.contracts import OidcIdentityRecord
        from backend_app.identity.oidc_store import InMemoryOidcStore

        rec = InMemoryOidcStore().create_identity(
            OidcIdentityRecord(
                org_id="org_a", user_id="usr_a", provider_id="prov", subject="sub"
            )
        )
        assert rec.principal_id == "prn_usr_a"

    def test_saml_identity_auto_fills_principal(self) -> None:
        from backend_app.contracts import SamlIdentityRecord
        from backend_app.identity.saml_store import InMemorySamlStore

        rec = InMemorySamlStore().create_identity(
            SamlIdentityRecord(
                org_id="org_a",
                user_id="usr_a",
                provider_id="prov",
                name_id="nid",
                name_id_format="fmt",
            )
        )
        assert rec.principal_id == "prn_usr_a"

    def test_explicit_principal_on_edge_is_honored(self) -> None:
        from backend_app.contracts import WalletIdentityRecord
        from backend_app.identity.siwe_store import InMemorySiweStore

        rec = InMemorySiweStore().create_wallet_identity(
            WalletIdentityRecord(
                address="0x" + "b" * 40,
                org_id="org_a",
                user_id="usr_a",
                chain_id=1,
                principal_id="prn_shared",
            )
        )
        assert rec.principal_id == "prn_shared"

    def test_edge_principal_matches_the_users_principal(self) -> None:
        # The edge's auto-filled principal is exactly the one create_user
        # minted for that user — proving app writes are internally consistent
        # (both sides use the same prn_<user_id> convention).
        from backend_app.contracts import WalletIdentityRecord
        from backend_app.identity.siwe_store import InMemorySiweStore

        store, org_id = _store_with_org()
        user = store.create_user(
            UserRecord(
                user_id="usr_c",
                org_id=org_id,
                primary_email="c@x.io",
                display_name="C",
            )
        )
        edge = InMemorySiweStore().create_wallet_identity(
            WalletIdentityRecord(
                address="0x" + "c" * 40,
                org_id=org_id,
                user_id="usr_c",
                chain_id=1,
            )
        )
        assert edge.principal_id == user.principal_id
