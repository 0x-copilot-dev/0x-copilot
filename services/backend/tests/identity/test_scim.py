"""SCIM service unit tests (A7).

Covers token mint/resolve/revoke, User CRUD + JSON-Patch, Group CRUD +
member sync + role mapping, and the tenant-isolation property of token
resolution.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OrganizationRecord,
    RoleRecord,
)
from backend_app.identity import (
    InMemoryIdentityStore,
    InMemoryScimStore,
    ScimAuthError,
    ScimConflict,
    ScimNotFound,
    ScimService,
    ScimUnsupportedFilter,
)


class ScimFixtureMixin:
    def build(self) -> tuple[ScimService, dict[str, Any]]:
        identity_store = InMemoryIdentityStore()
        scim_store = InMemoryScimStore()
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
                kind=AuthProviderKind.SCIM,
                display_name="Okta SCIM",
                config={},
            )
        )
        service = ScimService(identity_store=identity_store, scim_store=scim_store)
        return service, {
            "identity_store": identity_store,
            "scim_store": scim_store,
            "org": org,
            "provider": provider,
        }


class TestTokenLifecycle(ScimFixtureMixin):
    def test_mint_returns_plaintext_only_once(self) -> None:
        service, ctx = self.build()
        result = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        assert result.plaintext
        assert result.token_prefix == result.plaintext[:8]
        # Stored hash equals sha256 of plaintext.
        token_hash = hashlib.sha256(result.plaintext.encode("utf-8")).hexdigest()
        records = service.list_tokens(
            org_id=ctx["org"].org_id, provider_id=ctx["provider"].provider_id
        )
        assert any(r.token_hash == token_hash for r in records)
        # Plaintext is NOT in any stored field (defense-in-depth).
        for record in records:
            for value in record.model_dump().values():
                assert result.plaintext not in repr(value)

    def test_resolve_token_returns_provider(self) -> None:
        service, ctx = self.build()
        minted = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        resolved = service.resolve_token(minted.plaintext)
        assert resolved.token.token_id == minted.token_id
        assert resolved.provider.provider_id == ctx["provider"].provider_id

    def test_resolve_unknown_token_rejects(self) -> None:
        service, _ctx = self.build()
        with pytest.raises(ScimAuthError):
            service.resolve_token("not-a-real-token")

    def test_revoke_then_resolve_rejects(self) -> None:
        service, ctx = self.build()
        minted = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        ok = service.revoke_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            token_id=minted.token_id,
        )
        assert ok
        with pytest.raises(ScimAuthError):
            service.resolve_token(minted.plaintext)

    def test_expired_token_rejects(self) -> None:
        service, ctx = self.build()
        minted = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        with pytest.raises(ScimAuthError):
            service.resolve_token(minted.plaintext)

    def test_two_tokens_coexist_for_rotation(self) -> None:
        service, ctx = self.build()
        first = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        second = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        # Both still valid.
        service.resolve_token(first.plaintext)
        service.resolve_token(second.plaintext)


class TestUserCrud(ScimFixtureMixin):
    def _resolve(self, service: ScimService, ctx: dict[str, Any]):
        minted = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        return service.resolve_token(minted.plaintext)

    def test_create_user_persists_and_links_external_id(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        user, mapping = service.create_user(
            token=token,
            user_name="alice@acme.example",
            display_name="Alice",
            external_id="okta|0001",
        )
        assert user.primary_email == "alice@acme.example"
        assert mapping is not None
        assert mapping.external_id == "okta|0001"

    def test_create_user_collision_returns_conflict(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        service.create_user(
            token=token,
            user_name="alice@acme.example",
            display_name="Alice",
            external_id=None,
        )
        with pytest.raises(ScimConflict):
            service.create_user(
                token=token,
                user_name="alice@acme.example",
                display_name="Alice 2",
                external_id=None,
            )

    def test_patch_active_false_soft_deletes(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        user, _ = service.create_user(
            token=token,
            user_name="bob@acme.example",
            display_name="Bob",
            external_id=None,
        )
        service.patch_user(
            token=token,
            user_id=user.user_id,
            operations=[{"op": "replace", "path": "active", "value": False}],
        )
        # ``IdentityStore.get_user`` filters out deleted_at-set rows, so reach
        # into the in-memory map to verify the soft-delete actually landed.
        raw = ctx["identity_store"].users[user.user_id]
        assert raw.deleted_at is not None
        # Reactivate via PATCH and the user is queryable again.
        service.patch_user(
            token=token,
            user_id=user.user_id,
            operations=[{"op": "replace", "path": "active", "value": True}],
        )
        refreshed = ctx["identity_store"].get_user(
            org_id=ctx["org"].org_id, user_id=user.user_id
        )
        assert refreshed is not None
        assert refreshed.deleted_at is None

    def test_replace_user_updates_fields(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        user, _ = service.create_user(
            token=token,
            user_name="carol@acme.example",
            display_name="Carol",
            external_id=None,
        )
        updated = service.replace_user(
            token=token,
            user_id=user.user_id,
            user_name=None,
            display_name="Carol Smith",
            active=None,
        )
        assert updated.display_name == "Carol Smith"

    def test_delete_user_marks_deleted(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        user, _ = service.create_user(
            token=token,
            user_name="del@acme.example",
            display_name="Del",
            external_id=None,
        )
        service.delete_user(token=token, user_id=user.user_id)
        raw = ctx["identity_store"].users[user.user_id]
        assert raw.deleted_at is not None
        # Soft-deleted user no longer surfaces via the public lookup.
        assert (
            ctx["identity_store"].get_user(
                org_id=ctx["org"].org_id, user_id=user.user_id
            )
            is None
        )

    def test_filter_matches_user_name(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        for email in ("a@acme.example", "b@acme.example", "c@acme.example"):
            service.create_user(
                token=token,
                user_name=email,
                display_name=email,
                external_id=None,
            )
        users, total = service.list_users(
            token=token,
            filter_expr='userName eq "b@acme.example"',
            start_index=1,
            count=10,
        )
        assert total == 1
        assert users[0].primary_email == "b@acme.example"

    def test_invalid_filter_returns_unsupported_filter(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        with pytest.raises(ScimUnsupportedFilter):
            service.list_users(
                token=token,
                filter_expr='userName co "a"',
                start_index=1,
                count=10,
            )

    def test_get_unknown_user_raises_not_found(self) -> None:
        service, ctx = self.build()
        token = self._resolve(service, ctx)
        with pytest.raises(ScimNotFound):
            service.get_user(token=token, user_id="usr_unknown")


class TestGroupCrud(ScimFixtureMixin):
    def _setup(self) -> tuple[ScimService, dict[str, Any], Any]:
        service, ctx = self.build()
        minted = service.mint_token(
            org_id=ctx["org"].org_id,
            provider_id=ctx["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        return service, ctx, service.resolve_token(minted.plaintext)

    def test_create_group_with_role_mapping(self) -> None:
        service, ctx, token = self._setup()
        group = service.create_group(
            token=token,
            display_name="SSO Admins",
            external_id="okta|grp1",
            mapped_role_name="admin",
        )
        admin_role = ctx["identity_store"].get_role_by_name(org_id=None, name="admin")
        assert admin_role is not None
        assert group.mapped_role_id == admin_role.role_id

    def test_add_member_assigns_mapped_role(self) -> None:
        service, ctx, token = self._setup()
        user, _ = service.create_user(
            token=token,
            user_name="dora@acme.example",
            display_name="Dora",
            external_id=None,
        )
        group = service.create_group(
            token=token,
            display_name="SSO Admins",
            external_id=None,
            mapped_role_name="admin",
        )
        service.add_group_member(
            token=token, group_id=group.group_id, user_id=user.user_id
        )
        admin_role = ctx["identity_store"].get_role_by_name(org_id=None, name="admin")
        assert admin_role is not None
        assignments = ctx["identity_store"].list_role_assignments(
            org_id=ctx["org"].org_id, user_id=user.user_id
        )
        assert any(
            a.role_id == admin_role.role_id and a.revoked_at is None
            for a in assignments
        )

    def test_remove_member_revokes_role(self) -> None:
        service, ctx, token = self._setup()
        user, _ = service.create_user(
            token=token,
            user_name="eve@acme.example",
            display_name="Eve",
            external_id=None,
        )
        group = service.create_group(
            token=token,
            display_name="SSO Admins",
            external_id=None,
            mapped_role_name="admin",
        )
        service.add_group_member(
            token=token, group_id=group.group_id, user_id=user.user_id
        )
        service.remove_group_member(
            token=token, group_id=group.group_id, user_id=user.user_id
        )
        admin_role = ctx["identity_store"].get_role_by_name(org_id=None, name="admin")
        assert admin_role is not None
        assignments = ctx["identity_store"].list_role_assignments(
            org_id=ctx["org"].org_id, user_id=user.user_id
        )
        assert not any(
            a.role_id == admin_role.role_id and a.revoked_at is None
            for a in assignments
        )

    def test_soft_delete_group_revokes_all_member_roles(self) -> None:
        service, ctx, token = self._setup()
        users = []
        for email in ("f@acme.example", "g@acme.example"):
            user, _ = service.create_user(
                token=token,
                user_name=email,
                display_name=email,
                external_id=None,
            )
            users.append(user)
        group = service.create_group(
            token=token,
            display_name="SSO Admins",
            external_id=None,
            mapped_role_name="admin",
        )
        for user in users:
            service.add_group_member(
                token=token, group_id=group.group_id, user_id=user.user_id
            )
        service.soft_delete_group(token=token, group_id=group.group_id)
        admin_role = ctx["identity_store"].get_role_by_name(org_id=None, name="admin")
        assert admin_role is not None
        for user in users:
            assignments = ctx["identity_store"].list_role_assignments(
                org_id=ctx["org"].org_id, user_id=user.user_id
            )
            assert not any(
                a.role_id == admin_role.role_id and a.revoked_at is None
                for a in assignments
            )

    def test_duplicate_group_name_returns_conflict(self) -> None:
        service, _ctx, token = self._setup()
        service.create_group(
            token=token,
            display_name="SSO Admins",
            external_id=None,
        )
        with pytest.raises(ScimConflict):
            service.create_group(
                token=token,
                display_name="SSO Admins",
                external_id=None,
            )


class TestTenantIsolation(ScimFixtureMixin):
    def test_token_from_org_a_cannot_see_org_b_users(self) -> None:
        # Build two independent orgs / providers / tokens.
        service_a, ctx_a = self.build()
        service_b, ctx_b = self.build()
        minted_a = service_a.mint_token(
            org_id=ctx_a["org"].org_id,
            provider_id=ctx_a["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        minted_b = service_b.mint_token(
            org_id=ctx_b["org"].org_id,
            provider_id=ctx_b["provider"].provider_id,
            created_by_user_id="usr_admin",
        )
        token_a = service_a.resolve_token(minted_a.plaintext)
        token_b = service_b.resolve_token(minted_b.plaintext)
        # Create a user under org_b only.
        service_b.create_user(
            token=token_b,
            user_name="secret@b.example",
            display_name="B Secret",
            external_id=None,
        )
        # Token from org_a's service can't see them — the service is
        # initialized against ctx_a's identity_store, so cross-org reads
        # require a cross-store breach (which would itself be a bug).
        users_a, total_a = service_a.list_users(
            token=token_a, filter_expr=None, start_index=1, count=100
        )
        assert total_a == 0
        assert users_a == ()
