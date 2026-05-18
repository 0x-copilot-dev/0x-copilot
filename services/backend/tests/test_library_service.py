"""Tests for :class:`LibraryService` — Phase 7 P7-A1.

Direct service-layer tests (route-independent). Coverage:

* Audit row written on every state change (create / update / delete).
* Page markdown body REDACTED from audit ``before_state`` /
  ``after_state`` — content-hash + length only (library-prd §7.4
  sensitive-field handling).
* PATCH validation — page-only fields on files/datasets rejected; size
  + type bounds enforced.
* Cross-kind project filtering — ``project_id`` filter applies to all
  three kinds; counts_by_kind is computed BEFORE pagination so the
  destination header strip stays stable across pages.
* ID-prefix dispatch — ``get_item`` finds files / pages / datasets by
  their prefix.
"""

from __future__ import annotations

import hashlib

import pytest

from backend_app.library.service import (
    LibraryConflict,
    LibraryForbidden,
    LibraryInvalidRequest,
    LibraryNotFound,
    LibraryService,
)
from backend_app.library.store import (
    InMemoryLibraryStore,
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryPageRecord,
)
from backend_app.projects.acl import InMemoryProjectMembershipAdapter


def _make_service(
    *,
    memberships: dict[tuple[str, str], dict[str, str]] | None = None,
) -> tuple[LibraryService, InMemoryLibraryStore]:
    store = InMemoryLibraryStore()
    adapter = InMemoryProjectMembershipAdapter(memberships=memberships or {})
    service = LibraryService(store=store, membership_port=adapter)
    return service, store


class TestAuditAndRedaction:
    def test_page_create_redacts_markdown_in_audit(self) -> None:
        service, store = _make_service()
        body = "secret credentials: abc123"
        page = service.create_page(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload={
                "title": "secrets",
                "markdown": body,
            },
        )
        audit = store.list_audit_for_target(tenant_id="org_acme", target_id=page.id)
        assert len(audit) == 1
        row = audit[0]
        assert row.action == "library.page_created"
        # The raw body bytes MUST NOT appear in the audit row.
        assert row.after_state is not None
        assert "markdown" not in row.after_state
        # Replaced with content-hash + length.
        assert (
            row.after_state["markdown_sha256"]
            == hashlib.sha256(body.encode()).hexdigest()
        )
        assert row.after_state["markdown_bytes"] == len(body.encode())
        # And the context never carries the raw body either.
        assert row.context is not None
        assert "markdown" not in row.context

    def test_file_update_does_not_leak_blob_ref_into_audit(self) -> None:
        service, store = _make_service()
        store.insert_file(
            LibraryFileRecord(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                file_kind="pdf",
                name="contract.pdf",
                mime="application/pdf",
                blob_ref="s3://internal/key/with/sensitive/path",
                source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
            )
        )
        file_id = next(iter(store.files))
        service.update_item(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            item_id=file_id,
            patch={"name": "Renamed.pdf"},
        )
        audit = store.list_audit_for_target(tenant_id="org_acme", target_id=file_id)
        assert len(audit) == 1
        # before/after dumps MUST NOT include blob_ref / thumbnail_blob_ref
        # (library-prd §7.4 — no cleartext object-store URLs in audit
        # rows). The target_id is the audit linkage.
        for state in (audit[0].before_state, audit[0].after_state):
            assert state is not None
            assert "blob_ref" not in state
            assert "thumbnail_blob_ref" not in state


class TestStateAndInvariants:
    def test_owner_only_writes(self) -> None:
        service, store = _make_service(
            memberships={("org_acme", "prj_test"): {"usr_bob": "editor"}}
        )
        record = service.create_page(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload={
                "title": "shared",
                "markdown": "hi",
                "project_id": "prj_test",
            },
        )
        # Bob (project member) can READ the page filed under prj_test...
        got = service.get_item(
            tenant_id="org_acme",
            caller_user_id="usr_bob",
            caller_roles=(),
            item_id=record.id,
        )
        assert got.id == record.id

        # ...but CANNOT mutate it (owner-only writes).
        with pytest.raises(LibraryForbidden):
            service.update_item(
                tenant_id="org_acme",
                caller_user_id="usr_bob",
                caller_roles=(),
                item_id=record.id,
                patch={"tags": ["new"]},
            )

    def test_non_member_non_admin_gets_not_found_not_forbidden(self) -> None:
        service, store = _make_service(
            memberships={("org_acme", "prj_test"): {"usr_bob": "editor"}}
        )
        record = service.create_page(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload={
                "title": "p",
                "markdown": "x",
                "project_id": "prj_test",
            },
        )
        # Carol — no membership, no admin → NotFound (404-not-403).
        with pytest.raises(LibraryNotFound):
            service.get_item(
                tenant_id="org_acme",
                caller_user_id="usr_carol",
                caller_roles=(),
                item_id=record.id,
            )

    def test_page_body_etag_concurrency(self) -> None:
        service, _ = _make_service()
        page = service.create_page(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload={"title": "p", "markdown": "v1"},
        )
        # Stale etag → 409.
        with pytest.raises(LibraryConflict) as exc:
            service.update_item(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                item_id=page.id,
                patch={"markdown": "v2"},
                expected_etag="bogus",
            )
        assert exc.value.code == "version_etag_mismatch"

        # Correct etag → version bump + new etag.
        updated = service.update_item(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            item_id=page.id,
            patch={"markdown": "v2"},
            expected_etag=page.version_etag,
        )
        assert isinstance(updated, LibraryPageRecord)
        assert updated.version == 2
        assert updated.version_etag != page.version_etag

    def test_patch_rejects_kind_mismatched_fields(self) -> None:
        service, store = _make_service()
        # Files don't carry ``title`` / ``markdown`` / ``description``;
        # PATCH must reject those instead of silently dropping.
        store.insert_file(
            LibraryFileRecord(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                file_kind="pdf",
                name="a.pdf",
                mime="application/pdf",
                blob_ref="blob",
                source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
            )
        )
        file_id = next(iter(store.files))
        with pytest.raises(LibraryInvalidRequest):
            service.update_item(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                item_id=file_id,
                patch={"title": "wrong field"},
            )
        with pytest.raises(LibraryInvalidRequest):
            service.update_item(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                item_id=file_id,
                patch={"markdown": "wrong field"},
            )

    def test_id_prefix_dispatches_to_correct_kind(self) -> None:
        service, store = _make_service()
        # One of each kind in the same tenant.
        store.insert_file(
            LibraryFileRecord(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                file_kind="pdf",
                name="a.pdf",
                mime="application/pdf",
                blob_ref="b",
                source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
            )
        )
        store.insert_page(
            LibraryPageRecord(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                title="page",
                markdown="md",
                source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
            )
        )
        store.insert_dataset(
            LibraryDatasetRecord(
                tenant_id="org_acme",
                owner_user_id="usr_sarah",
                name="ds",
                blob_ref="b",
                source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
            )
        )
        file_id = next(iter(store.files))
        page_id = next(iter(store.pages))
        ds_id = next(iter(store.datasets))
        assert isinstance(
            service.get_item(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                item_id=file_id,
            ),
            LibraryFileRecord,
        )
        assert isinstance(
            service.get_item(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                item_id=page_id,
            ),
            LibraryPageRecord,
        )
        assert isinstance(
            service.get_item(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                caller_roles=(),
                item_id=ds_id,
            ),
            LibraryDatasetRecord,
        )


class TestListAcrossKinds:
    def test_counts_by_kind_stable_across_pages(self) -> None:
        service, store = _make_service()
        # 3 pages + 2 files, all owned by Sarah.
        for i in range(3):
            service.create_page(
                tenant_id="org_acme",
                caller_user_id="usr_sarah",
                payload={"title": f"page-{i}", "markdown": "x"},
            )
        for i in range(2):
            store.insert_file(
                LibraryFileRecord(
                    tenant_id="org_acme",
                    owner_user_id="usr_sarah",
                    file_kind="pdf",
                    name=f"f-{i}.pdf",
                    mime="application/pdf",
                    blob_ref="b",
                    source={"kind": "user_upload", "uploaded_by": "usr_sarah"},
                )
            )
        page1, cursor, counts = service.list_items(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            limit=2,
        )
        assert len(page1) == 2
        # Counts are tot-set counts across all matching rows, not
        # just the current page.
        assert counts == {"file": 2, "page": 3, "dataset": 0}

        page2, cursor2, counts2 = service.list_items(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
            limit=2,
            cursor=cursor,
        )
        # Stable across pages.
        assert counts2 == counts

    def test_tenant_isolation_filters_first(self) -> None:
        service, store = _make_service()
        # Cross-tenant row.
        store.insert_page(
            LibraryPageRecord(
                tenant_id="org_zeta",
                owner_user_id="usr_alice_other",
                title="zeta-page",
                markdown="x",
                source={"kind": "user_upload", "uploaded_by": "usr_alice_other"},
            )
        )
        # Same-tenant row.
        service.create_page(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            payload={"title": "acme-page", "markdown": "x"},
        )
        rows, _, _ = service.list_items(
            tenant_id="org_acme",
            caller_user_id="usr_sarah",
            caller_roles=(),
        )
        # Only the org_acme row visible.
        assert len(rows) == 1
        assert rows[0].tenant_id == "org_acme"
