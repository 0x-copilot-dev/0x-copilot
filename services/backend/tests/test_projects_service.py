"""Service-layer tests for :mod:`backend_app.projects.service`.

Covers invariants that are awkward to drive through HTTP — primarily
the atomic ownership-transfer two-step (the PARTIAL UNIQUE on
``(project_id) WHERE role='owner'`` invariant) and the membership-port
synchronization that the route tests rely on indirectly.
"""

from __future__ import annotations

import pytest

from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.service import (
    ProjectConflict,
    ProjectForbidden,
    ProjectInvalidRequest,
    ProjectNotFound,
    ProjectsService,
)
from backend_app.projects.store import InMemoryProjectsStore


def _identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id in ("usr_sarah", "usr_bob", "usr_carol", "usr_dave_admin"):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id="org_acme",
                primary_email=f"{user_id}@acme.com",
                display_name=user_id,
            )
        )
    return store


def _service() -> tuple[ProjectsService, InMemoryProjectsStore]:
    store = InMemoryProjectsStore()
    svc = ProjectsService(store=store, identity_store=_identity())
    return svc, store


class TestComputedRollupCounts:
    """PRD-07 — counts are computed on read from the registered rollup sources."""

    def _wired_service(self):
        from backend_app.library.store import (
            InMemoryLibraryStore,
            LibraryFileRecord,
            LibraryPageRecord,
        )
        from backend_app.projects.rollup_sources import (
            MembersRollupSource,
            StoreRollupSource,
        )

        store = InMemoryProjectsStore()
        library = InMemoryLibraryStore()
        svc = ProjectsService(store=store, identity_store=_identity())
        svc.register_rollup_sources(
            [
                StoreRollupSource(store=library, fields=("files", "library_items")),
                MembersRollupSource(store),
            ]
        )
        return svc, store, library, LibraryFileRecord, LibraryPageRecord

    def test_list_projects_counts_files_and_library_items(self) -> None:
        svc, _store, library, FileRec, PageRec = self._wired_service()
        pid = _seed_project(svc)
        for i in range(2):
            library.insert_file(
                FileRec(
                    tenant_id="org_acme",
                    owner_user_id="usr_sarah",
                    project_id=pid,
                    file_kind="doc",
                    name=f"file-{i}.doc",
                    mime="text/plain",
                    blob_ref=f"blob-{i}",
                    source={"kind": "upload"},
                )
            )
        library.insert_page(
            PageRec(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                project_id=pid,
                title="notes",
                markdown="# notes",
                source={"kind": "manual"},
            )
        )

        page, _ = svc.list_projects(
            tenant_id="org_acme", caller_user_id="usr_sarah", caller_roles=()
        )
        counts = {record.id: c for record, _role, _star, c in page}[pid]
        # files = kind='file' only (2); library_items = all kinds (2 files + 1 page).
        assert counts.files == 2
        assert counts.library_items == 3
        # members = the owner row; chats is null (backend is not entitled — the
        # facade fills it from ai-backend).
        assert counts.members == 1
        assert counts.chats is None


def _seed_project(svc: ProjectsService) -> str:
    record, _, _, _ = svc.create_project(
        tenant_id="org_acme",
        caller_user_id="usr_sarah",
        payload={"name": "Project A", "icon_emoji": "🚀", "color_hue": 210},
    )
    return record.id


class TestCreate:
    def test_owner_membership_row_created_on_create(self) -> None:
        svc, store = _service()
        pid = _seed_project(svc)
        membership = store.get_membership(
            tenant_id="org_acme", project_id=pid, user_id="usr_sarah"
        )
        assert membership is not None
        assert membership.role == "owner"

    def test_duplicate_name_409(self) -> None:
        svc, _ = _service()
        _seed_project(svc)
        with pytest.raises(ProjectConflict) as exc:
            svc.create_project(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                payload={
                    "name": "project A",  # case-insensitive
                    "icon_emoji": "🚀",
                    "color_hue": 210,
                },
            )
        assert exc.value.code == "duplicate_name"


class TestTransferAtomicity:
    def test_owner_swap_preserves_partial_unique(self) -> None:
        """Before and after the transaction, exactly ONE owner-roled
        membership row exists for the project."""

        svc, store = _service()
        pid = _seed_project(svc)
        svc.add_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
            role="editor",
        )
        # Pre-transfer: one owner row (Sarah).
        owner_rows_pre = [
            m
            for m in store.list_memberships_for_project(
                tenant_id="org_acme", project_id=pid, limit=10
            )[0]
            if m.role == "owner"
        ]
        assert len(owner_rows_pre) == 1
        assert owner_rows_pre[0].user_id == "usr_sarah"

        svc.transfer_ownership(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            new_owner_user_id="usr_bob",
        )

        owner_rows_post = [
            m
            for m in store.list_memberships_for_project(
                tenant_id="org_acme", project_id=pid, limit=10
            )[0]
            if m.role == "owner"
        ]
        assert len(owner_rows_post) == 1
        assert owner_rows_post[0].user_id == "usr_bob"

        # And the projects table's owner pointer matches.
        record = store.get_project(tenant_id="org_acme", project_id=pid)
        assert record is not None
        assert record.owner_user_id == "usr_bob"

    def test_transfer_to_self_rejected(self) -> None:
        svc, _ = _service()
        pid = _seed_project(svc)
        with pytest.raises(ProjectInvalidRequest) as exc:
            svc.transfer_ownership(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=[],
                project_id=pid,
                new_owner_user_id="usr_sarah",
            )
        assert "new_owner_is_current_owner" in str(exc.value)

    def test_transfer_with_viewer_demotion(self) -> None:
        svc, store = _service()
        pid = _seed_project(svc)
        svc.add_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
            role="editor",
        )
        svc.transfer_ownership(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            new_owner_user_id="usr_bob",
            previous_owner_new_role="viewer",
        )
        sarah_row = store.get_membership(
            tenant_id="org_acme", project_id=pid, user_id="usr_sarah"
        )
        assert sarah_row is not None
        assert sarah_row.role == "viewer"

    def test_force_transfer_requires_admin(self) -> None:
        svc, _ = _service()
        pid = _seed_project(svc)
        svc.add_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
            role="editor",
        )
        # Non-admin caller → ProjectForbidden.
        with pytest.raises(ProjectForbidden):
            svc.force_transfer_ownership(
                tenant_id="org_acme",
                caller_user_id="usr_carol",
                caller_roles=[],
                project_id=pid,
                new_owner_user_id="usr_bob",
            )

    def test_force_transfer_admin_audit_records_both_ids(self) -> None:
        svc, store = _service()
        pid = _seed_project(svc)
        svc.add_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
            role="editor",
        )
        svc.force_transfer_ownership(
            tenant_id="org_acme",
            caller_user_id="usr_dave_admin",
            caller_roles=["admin"],
            project_id=pid,
            new_owner_user_id="usr_bob",
            reason="owner_offboarded",
        )
        audits = store.list_audit_for_project(tenant_id="org_acme", project_id=pid)
        force = [r for r in audits if r.action == "project.admin_force_transferred"]
        assert len(force) == 1
        ctx = force[0].context or {}
        assert ctx["from_user_id"] == "usr_sarah"
        assert ctx["to_user_id"] == "usr_bob"
        assert ctx["admin_force"] is True
        assert ctx["reason"] == "owner_offboarded"


class TestArchiveStateMachine:
    def test_archive_stamps_archived_at(self) -> None:
        svc, _ = _service()
        pid = _seed_project(svc)
        record = svc.update_project(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            patch={"status": "archived"},
        )
        assert record.status == "archived"
        assert record.archived_at is not None

    def test_archive_to_active_clears_archived_at(self) -> None:
        svc, _ = _service()
        pid = _seed_project(svc)
        svc.update_project(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            patch={"status": "archived"},
        )
        record = svc.update_project(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            patch={"status": "active"},
        )
        assert record.status == "active"
        assert record.archived_at is None

    def test_mutation_on_archived_409(self) -> None:
        svc, _ = _service()
        pid = _seed_project(svc)
        svc.update_project(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            patch={"status": "archived"},
        )
        with pytest.raises(ProjectConflict) as exc:
            svc.update_project(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=[],
                project_id=pid,
                patch={"name": "renamed"},
            )
        assert exc.value.code == "project_archived"


class TestReadAcl404NotForbidden:
    def test_non_member_get_raises_not_found(self) -> None:
        """The 404-not-403 invariant — defense in depth. The service
        layer raises :class:`ProjectNotFound`, not :class:`ProjectForbidden`,
        so the route can't accidentally translate to 403."""

        svc, _ = _service()
        pid = _seed_project(svc)
        with pytest.raises(ProjectNotFound):
            svc.get_project(
                tenant_id="org_acme",
                caller_user_id="usr_bob",
                caller_roles=[],
                project_id=pid,
            )


class TestMembershipPortSync:
    def test_add_member_visible_via_canonical_port(self) -> None:
        svc, store = _service()
        pid = _seed_project(svc)
        svc.add_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
            role="editor",
        )
        # The canonical port returns the role through the store-backed
        # default adapter.
        assert svc._membership_port.is_member(  # noqa: SLF001 — test only
            tenant_id="org_acme", project_id=pid, user_id="usr_bob"
        )

    def test_remove_member_invisible_via_canonical_port(self) -> None:
        svc, _ = _service()
        pid = _seed_project(svc)
        svc.add_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
            role="editor",
        )
        svc.remove_member(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=[],
            project_id=pid,
            target_user_id="usr_bob",
        )
        assert not svc._membership_port.is_member(  # noqa: SLF001
            tenant_id="org_acme", project_id=pid, user_id="usr_bob"
        )
