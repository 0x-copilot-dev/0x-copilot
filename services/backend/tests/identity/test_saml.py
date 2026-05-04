"""SAML service unit tests (A5).

We exercise the full state machine — authorize, ACS happy path, replay,
verifier failures, JIT provisioning, role sync, cross-tenant rejection —
against the in-memory store and ``FakeSamlVerifier``. The integration
test that uses real signed XML lives behind ``pytest.importorskip`` in
``test_saml_integration.py`` (kept separate so a host without xmlsec1
still gets a green identity suite).
"""

from __future__ import annotations

from typing import Any

import pytest

from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleRecord,
)
from backend_app.identity import (
    FakeSamlVerifier,
    InMemoryIdentityStore,
    InMemorySamlStore,
    InMemorySessionStore,
    ParsedSamlAssertion,
    SamlAudienceMismatch,
    SamlConfigError,
    SamlIdpInitiatedDisabled,
    SamlInResponseToMismatch,
    SamlProviderDisabled,
    SamlReplayDetected,
    SamlService,
    SamlSignatureError,
    SamlUserNotProvisioned,
    SessionService,
)


_AUTH_SECRET = "test-auth-secret-must-be-at-least-32-chars-long-12345"


def _provider_config(
    *,
    auto_provision_user: bool = True,
    allow_idp_initiated: bool = False,
    group_role_map: dict[str, str] | None = None,
    attribute_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "idp_entity_id": "https://idp.example/entity",
        "idp_sso_url": "https://idp.example/sso",
        "idp_x509_cert": "MIIDfake_cert==",
        "sp_entity_id": "https://sp.example/sp",
        "sp_acs_url": "https://sp.example/v1/auth/saml/prv_acme/acs",
        "attribute_map": attribute_map
        or {"email": "email", "display_name": "name", "groups": "groups"},
        "allow_idp_initiated": allow_idp_initiated,
        "auto_provision_user": auto_provision_user,
        "group_role_map": group_role_map or {},
    }


class SamlServiceFixtureMixin:
    def build(
        self,
        *,
        auto_provision_user: bool = True,
        allow_idp_initiated: bool = False,
        group_role_map: dict[str, str] | None = None,
        attribute_map: dict[str, str] | None = None,
        provider_enabled: bool = True,
    ) -> tuple[SamlService, dict[str, Any]]:
        identity_store = InMemoryIdentityStore()
        saml_store = InMemorySamlStore()
        sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        org = identity_store.create_organization(
            OrganizationRecord(display_name="Acme", slug="acme")
        )
        identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="Employee",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )
        identity_store.create_role(
            RoleRecord(
                name="admin",
                display_name="Admin",
                is_system=True,
                permission_scopes=("admin:users",),
            )
        )
        provider = identity_store.create_auth_provider(
            AuthProviderRecord(
                org_id=org.org_id,
                kind=AuthProviderKind.SAML,
                display_name="Acme SAML",
                enabled=provider_enabled,
                config=_provider_config(
                    auto_provision_user=auto_provision_user,
                    allow_idp_initiated=allow_idp_initiated,
                    group_role_map=group_role_map,
                    attribute_map=attribute_map,
                ),
            )
        )
        verifier = FakeSamlVerifier()
        service = SamlService(
            identity_store=identity_store,
            saml_store=saml_store,
            sessions=sessions,
            verifier=verifier,
        )
        return service, {
            "identity_store": identity_store,
            "saml_store": saml_store,
            "sessions": sessions,
            "org": org,
            "provider": provider,
            "verifier": verifier,
        }


class TestAuthorize(SamlServiceFixtureMixin):
    def test_authorize_persists_pending_request(self) -> None:
        service, ctx = self.build()
        result = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            relay_state="/dashboard",
        )
        assert result.request_id == "fake-req-1"
        assert result.sso_url.startswith("https://idp.example/sso")
        # Pending row stored, replay placeholder assertion id.
        rows = list(ctx["saml_store"].authentications.values())
        assert len(rows) == 1
        assert rows[0].request_id == "fake-req-1"
        assert rows[0].assertion_id.startswith("pending:")
        assert rows[0].relay_state == "/dashboard"

    def test_authorize_rejects_wrong_kind(self) -> None:
        service, ctx = self.build()
        identity_store = ctx["identity_store"]
        wrong = identity_store.create_auth_provider(
            AuthProviderRecord(
                org_id=ctx["org"].org_id,
                kind=AuthProviderKind.OIDC,
                display_name="OIDC",
                config={},
            )
        )
        with pytest.raises(SamlConfigError):
            service.authorize(org_id=ctx["org"].org_id, provider_id=wrong.provider_id)

    def test_authorize_rejects_disabled_provider(self) -> None:
        service, ctx = self.build(provider_enabled=False)
        with pytest.raises(SamlProviderDisabled):
            service.authorize(
                org_id=ctx["org"].org_id,
                provider_id=ctx["provider"].provider_id,
            )


class TestConsumeHappyPath(SamlServiceFixtureMixin):
    def test_consume_links_existing_user_and_mints_session(self) -> None:
        service, ctx = self.build()
        # Pre-stage authorize so we have a pending row.
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            assertion_id="assertion-1",
            in_response_to=authorize.request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"], "name": ["Alice"]},
        )
        result = service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<base64>",
            relay_state="/return",
            expected_in_response_to=authorize.request_id,
        )
        assert result.bearer_token
        assert result.relay_state == "/return"
        # JIT provisioned the user.
        users = ctx["identity_store"].list_users(org_id=ctx["org"].org_id)
        assert len(users) == 1
        assert users[0].primary_email == "alice@acme.example"
        # Identity link recorded.
        identity = ctx["saml_store"].get_identity_by_name_id(
            provider_id=ctx["provider"].provider_id,
            name_id="alice@acme.example",
        )
        assert identity is not None
        assert identity.user_id == users[0].user_id

    def test_consume_relinks_existing_identity_without_provisioning(self) -> None:
        service, ctx = self.build(auto_provision_user=False)
        # Pre-create user + linked identity.
        from backend_app.contracts import (
            OrganizationMemberRecord,
            OrganizationMemberSource,
            SamlIdentityRecord,
            UserRecord,
        )

        user = ctx["identity_store"].create_user(
            UserRecord(
                org_id=ctx["org"].org_id,
                primary_email="bob@acme.example",
                display_name="Bob",
            )
        )
        ctx["identity_store"].add_member(
            OrganizationMemberRecord(
                org_id=ctx["org"].org_id,
                user_id=user.user_id,
                source=OrganizationMemberSource.SAML,
            )
        )
        ctx["saml_store"].create_identity(
            SamlIdentityRecord(
                org_id=ctx["org"].org_id,
                user_id=user.user_id,
                provider_id=ctx["provider"].provider_id,
                name_id="bob@acme.example",
                name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            )
        )
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="bob@acme.example",
            name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            assertion_id="assertion-2",
            in_response_to=authorize.request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["bob@acme.example"]},
        )
        result = service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<base64>",
            expected_in_response_to=authorize.request_id,
        )
        assert result.user_id == user.user_id
        # No new user.
        assert len(ctx["identity_store"].list_users(org_id=ctx["org"].org_id)) == 1


class TestConsumeReplayAndValidation(SamlServiceFixtureMixin):
    def test_replay_rejected(self) -> None:
        service, ctx = self.build()
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            assertion_id="assertion-replay",
            in_response_to=authorize.request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"]},
        )
        ctx["verifier"].next_assertion = assertion
        service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<b1>",
            expected_in_response_to=authorize.request_id,
        )
        # Second authorize so we have a fresh pending row, but the SAME
        # assertion_id should still be refused by the replay guard.
        authorize_2 = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            assertion_id="assertion-replay",  # SAME id
            in_response_to=authorize_2.request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"]},
        )
        with pytest.raises(SamlReplayDetected):
            service.consume(
                provider_id=ctx["provider"].provider_id,
                saml_response_b64="<b2>",
                expected_in_response_to=authorize_2.request_id,
            )

    def test_signature_failure_recorded_in_audit(self) -> None:
        service, ctx = self.build()
        ctx["verifier"].next_error = SamlSignatureError("bad sig")
        with pytest.raises(SamlSignatureError):
            service.consume(
                provider_id=ctx["provider"].provider_id,
                saml_response_b64="<bad>",
                expected_in_response_to=None,
            )
        audits = ctx["identity_store"].list_identity_audit(org_id=ctx["org"].org_id)
        assert any(a.action == "saml.acs_failed" for a in audits)
        attempts = ctx["identity_store"].list_login_attempts(
            org_id=ctx["org"].org_id, email=None, user_id=None
        )
        assert any(a.failure_reason == "bad sig" for a in attempts)

    def test_audience_mismatch_propagates(self) -> None:
        service, ctx = self.build()
        ctx["verifier"].next_error = SamlAudienceMismatch("aud != sp_entity_id")
        with pytest.raises(SamlAudienceMismatch):
            service.consume(
                provider_id=ctx["provider"].provider_id,
                saml_response_b64="<x>",
                expected_in_response_to=None,
            )

    def test_in_response_to_mismatch_when_no_pending_row(self) -> None:
        service, ctx = self.build()
        # No prior authorize → no pending request_id.
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="email",
            assertion_id="assertion-orphan",
            in_response_to="never-issued",
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"]},
        )
        with pytest.raises(SamlInResponseToMismatch):
            service.consume(
                provider_id=ctx["provider"].provider_id,
                saml_response_b64="<x>",
                expected_in_response_to="never-issued",
            )


class TestIdpInitiated(SamlServiceFixtureMixin):
    def test_idp_initiated_rejected_when_disabled(self) -> None:
        service, ctx = self.build(allow_idp_initiated=False)
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="email",
            assertion_id="assertion-idp-1",
            in_response_to=None,
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"]},
        )
        with pytest.raises(SamlIdpInitiatedDisabled):
            service.consume(
                provider_id=ctx["provider"].provider_id,
                saml_response_b64="<x>",
                expected_in_response_to=None,
            )

    def test_idp_initiated_admitted_when_enabled(self) -> None:
        service, ctx = self.build(allow_idp_initiated=True)
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="alice@acme.example",
            name_id_format="email",
            assertion_id="assertion-idp-2",
            in_response_to=None,
            issuer="https://idp.example/entity",
            attributes={"email": ["alice@acme.example"]},
        )
        result = service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<x>",
            expected_in_response_to=None,
        )
        assert result.bearer_token


class TestProvisioningGate(SamlServiceFixtureMixin):
    def test_unknown_user_with_jit_off_rejects(self) -> None:
        service, ctx = self.build(auto_provision_user=False)
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="newbie@acme.example",
            name_id_format="email",
            assertion_id="assertion-jit",
            in_response_to=authorize.request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["newbie@acme.example"]},
        )
        with pytest.raises(SamlUserNotProvisioned):
            service.consume(
                provider_id=ctx["provider"].provider_id,
                saml_response_b64="<x>",
                expected_in_response_to=authorize.request_id,
            )


class TestRoleSync(SamlServiceFixtureMixin):
    def test_groups_attribute_assigns_roles(self) -> None:
        service, ctx = self.build(group_role_map={"sso-admins": "admin"})
        authorize = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="charlie@acme.example",
            name_id_format="email",
            assertion_id="assertion-roles",
            in_response_to=authorize.request_id,
            issuer="https://idp.example/entity",
            attributes={
                "email": ["charlie@acme.example"],
                "groups": ["sso-admins", "ignored-group"],
            },
        )
        result = service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<x>",
            expected_in_response_to=authorize.request_id,
        )
        assignments = ctx["identity_store"].list_role_assignments(
            org_id=ctx["org"].org_id, user_id=result.user_id
        )
        names = []
        for assignment in assignments:
            role = ctx["identity_store"].get_role(role_id=assignment.role_id)
            if role is not None:
                names.append(role.name)
        assert "admin" in names

    def test_role_sync_idempotent(self) -> None:
        service, ctx = self.build(group_role_map={"sso-admins": "admin"})
        # Fire two separate logins for the same user.
        authorize_1 = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="dora@acme.example",
            name_id_format="email",
            assertion_id="assertion-r1",
            in_response_to=authorize_1.request_id,
            issuer="https://idp.example/entity",
            attributes={
                "email": ["dora@acme.example"],
                "groups": ["sso-admins"],
            },
        )
        result = service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<x>",
            expected_in_response_to=authorize_1.request_id,
        )
        authorize_2 = service.authorize(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
        )
        ctx["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="dora@acme.example",
            name_id_format="email",
            assertion_id="assertion-r2",
            in_response_to=authorize_2.request_id,
            issuer="https://idp.example/entity",
            attributes={
                "email": ["dora@acme.example"],
                "groups": ["sso-admins"],
            },
        )
        service.consume(
            provider_id=ctx["provider"].provider_id,
            saml_response_b64="<x>",
            expected_in_response_to=authorize_2.request_id,
        )
        # Still exactly one role assignment for `admin`.
        assignments = ctx["identity_store"].list_role_assignments(
            org_id=ctx["org"].org_id, user_id=result.user_id
        )
        admin_count = 0
        for assignment in assignments:
            role = ctx["identity_store"].get_role(role_id=assignment.role_id)
            if role is not None and role.name == "admin":
                admin_count += 1
        assert admin_count == 1


class TestCrossTenantIsolation(SamlServiceFixtureMixin):
    def test_assertion_for_other_org_provider_does_not_leak(self) -> None:
        # Two orgs, two providers. An assertion linked to provider_a's
        # name_id cannot be replayed against provider_b — the (provider_id,
        # name_id) lookup is per-provider, not per-tenant-shared.
        service_a, ctx_a = self.build()
        # Build a second org via the same fixture and capture its store.
        service_b, ctx_b = self.build()

        authorize_a = service_a.authorize(
            org_id=ctx_a["org"].org_id,
            provider_id=ctx_a["provider"].provider_id,
        )
        ctx_a["verifier"].next_assertion = ParsedSamlAssertion(
            name_id="emma@acme.example",
            name_id_format="email",
            assertion_id="assertion-a",
            in_response_to=authorize_a.request_id,
            issuer="https://idp.example/entity",
            attributes={"email": ["emma@acme.example"]},
        )
        result_a = service_a.consume(
            provider_id=ctx_a["provider"].provider_id,
            saml_response_b64="<x>",
            expected_in_response_to=authorize_a.request_id,
        )
        # service_b knows nothing about emma — get_identity_by_name_id on
        # provider_b returns None, JIT is on, but the verifier's assertion
        # would have to claim org_b's provider_id, which it cannot.
        identity_in_b = ctx_b["saml_store"].get_identity_by_name_id(
            provider_id=ctx_b["provider"].provider_id,
            name_id="emma@acme.example",
        )
        assert identity_in_b is None
        # Sanity: emma exists only under org_a.
        users_a = ctx_a["identity_store"].list_users(org_id=ctx_a["org"].org_id)
        users_b = ctx_b["identity_store"].list_users(org_id=ctx_b["org"].org_id)
        assert any(u.user_id == result_a.user_id for u in users_a)
        assert not any(u.primary_email == "emma@acme.example" for u in users_b)


class TestMetadata(SamlServiceFixtureMixin):
    def test_metadata_returns_xml_with_sp_entity_id(self) -> None:
        service, ctx = self.build()
        xml = service.metadata(
            org_id=ctx["org"].org_id, provider_id=ctx["provider"].provider_id
        )
        assert "https://sp.example/sp" in xml
        assert "AssertionConsumerService" in xml
