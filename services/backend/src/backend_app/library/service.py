"""Library service — CRUD + ACL + audit (P7-A1).

Route layer is presentation-only; every business-logic decision lives
here so the in-memory + Postgres adapters share one set of
authorization checks, invariants, and audit hooks.

Authorization (library-prd §7 + cross-audit §1.3 binding 2026-05-17):

* **Reads.** Owner OR project-member (via the canonical
  ``backend_app.projects.acl.is_member`` predicate — no
  reimplementation here) OR tenant admin (compliance read; audited at
  the route layer). Non-readers see 404, not 403 (existence-not-leaked
  default).
* **Writes.** ``owner_user_id`` only — project members CANNOT mutate
  metadata or body. Admins do not lift to write either (compliance is
  read-only; GDPR forced-delete is a separate endpoint owned by a
  future ticket).
* **Cross-tenant.** Tenant scoping is the verified bearer's tenant
  claim — never the request body (cross-audit §3.1).

State / invariants:

* Page body edits bump ``version`` + rotate ``version_etag``. Concurrent
  edits MUST pass ``If-Match: <version_etag>``; mismatch → 409.
  P7-A1 enforces version bump at every write; the ``If-Match`` header
  parse is in the route layer (the service receives ``expected_etag``).
* Project re-file (``project_id`` PATCH) is allowed; the new project
  is **not** required to be one the caller is a member of (the owner
  remains the row's authoritative actor; the project is a filing axis,
  not a permission lift — library-prd §7.2 owner-only rule is the
  binding gate).
* Soft-delete sets ``deleted_at``; subsequent reads return 404.

PII / sensitive content:

* Page markdown bodies are NEVER serialized into audit ``before_state``
  / ``after_state``. The service replaces ``markdown`` with a
  content-hash + length stub (library-prd §7.4 binding). Tags are not
  sensitive but free-text — we log them.
* Blob refs / signed URLs are not logged. P7-A1 records ``blob_ref`` in
  the row (opaque to clients) but never in audit context — the
  ``target_id`` is sufficient for the audit trail.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from backend_app.library.store import (
    LibraryAuditRecord,
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryItemRecord,
    LibraryPageRecord,
    LibraryStore,
)
from backend_app.projects.acl import (
    ProjectMembershipPort,
    is_member,
)


_LOGGER = logging.getLogger(__name__)


# Callback the service uses to enqueue an indexing job after every state
# change. Lives behind a callable so the service does not depend on the
# indexer's queue store directly (the wiring composer injects both halves
# at app startup). ``target_kind`` ∈ {"file", "page", "dataset"}.
EnqueueIndexJob = Callable[[str, str, str], None]  # tenant, kind, target_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


_ADMIN_ROLES = frozenset({"admin", "owner"})

_TITLE_MAX = 200
_NAME_MAX = 200
_DESCRIPTION_MAX = 2000
_TAG_MAX_LEN = 64
_TAG_COUNT_MAX = 50
_MARKDOWN_MAX = 1_048_576  # 1 MB — library-prd §5.5 hard cap


# Source kinds accepted on writes. ``user_upload`` is the public default;
# ``agent_save`` / ``connector_sync`` are reserved for internal
# producers (P7-A2 + Phase 10), but P7-A1 accepts them on the public
# POST too so the wire shape is stable.
_VALID_SOURCE_KINDS = frozenset({"user_upload", "agent_save", "connector_sync"})


class LibraryNotFound(Exception):
    """Raised when an item doesn't exist OR the caller has no read rights.

    Collapses both branches so the route layer cannot accidentally
    distinguish them — response is always 404 (cross-audit §1.3
    binding).
    """


class LibraryForbidden(Exception):
    """Raised when the caller can READ but cannot WRITE.

    Used after read access has been established (so the 404-not-403
    rule still applies for the no-read case). Route layer → 403.
    """


class LibraryInvalidRequest(Exception):
    """Client-fixable invariant violation (400)."""


class LibraryConflict(Exception):
    """State-conflict violation (409) — version-etag mismatch on page
    body edits."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LibraryService:
    """CRUD + ACL + audit. Consumes the canonical
    :class:`ProjectMembershipPort` for project-scoped reads — never
    reimplements the membership predicate.
    """

    def __init__(
        self,
        *,
        store: LibraryStore,
        membership_port: ProjectMembershipPort,
        enqueue_index_job: EnqueueIndexJob | None = None,
    ) -> None:
        self._store = store
        self._membership = membership_port
        # Optional retrieval-pipeline hook (P7.5-A2). When wired, every
        # state change enqueues a job onto ``library_index_jobs`` so the
        # background indexer can re-extract + re-embed. When unwired
        # (legacy tests, no-indexer deployments), CRUD continues to work
        # without raising.
        self._enqueue_index_job = enqueue_index_job

    # =================================================================
    # Reads
    # =================================================================

    def get_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
    ) -> LibraryItemRecord:
        """Authorise + return a single item.

        Raises :class:`LibraryNotFound` if the caller can't see it
        (404-not-403; the route never distinguishes "not found" from
        "not authorised").
        """

        record = self._lookup_any(tenant_id=tenant_id, item_id=item_id)
        if record is None:
            raise LibraryNotFound(item_id)
        if not self._can_read(record, caller_user_id, caller_roles):
            raise LibraryNotFound(item_id)
        return record

    def list_items(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str, ...] | None = None,
        owner_user_ids: tuple[str, ...] | None = None,
        source_kinds: tuple[str, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        index_statuses: tuple[str, ...] | None = None,
        file_kinds: tuple[str, ...] | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
    ) -> tuple[tuple[LibraryItemRecord, ...], str | None, dict[str, int]]:
        """List items visible to the caller.

        The store pre-computes the visibility predicate from
        ``readable_project_ids`` (the union of every project the caller
        is a member of) so we make exactly ONE call to the membership
        port. Admins short-circuit visibility (compliance read).
        """

        admin = _is_admin(caller_roles)
        readable_project_ids: tuple[str, ...]
        if admin:
            readable_project_ids = ()
        else:
            readable_project_ids = self._membership.list_projects_for_user(
                tenant_id=tenant_id, user_id=caller_user_id
            )

        return self._store.list_items(
            tenant_id=tenant_id,
            kinds=kinds,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
            source_kinds=source_kinds,
            tags=tags,
            index_statuses=index_statuses,
            file_kinds=file_kinds,
            q=q,
            visible_to_user_id=caller_user_id,
            readable_project_ids=readable_project_ids,
            admin=admin,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )

    # =================================================================
    # Writes — pages
    # =================================================================

    def create_page(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        payload: dict[str, Any],
    ) -> LibraryPageRecord:
        validated = self._validate_page_create(payload)
        source = validated.get("source") or {
            "kind": "user_upload",
            "uploaded_by": caller_user_id,
        }
        _validate_source(source)
        record = LibraryPageRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            project_id=validated.get("project_id"),
            title=validated["title"],
            markdown=validated["markdown"],
            tags=validated.get("tags", []),
            source=source,
        )
        with self._store.transaction():
            stored = self._store.insert_page(record)
            self._store.append_audit(
                LibraryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="library.page_created",
                    target_kind="library_page",
                    target_id=stored.id,
                    # ``markdown`` is intentionally redacted (library-prd
                    # §7.4 sensitive-field handling).
                    after_state=_dump_page_for_audit(stored),
                    context={
                        "project_id": stored.project_id,
                        "source_kind": source.get("kind"),
                        "tag_count": len(stored.tags),
                        "markdown_bytes": len(stored.markdown),
                    },
                )
            )
        self._safe_enqueue(stored)
        return stored

    # =================================================================
    # Writes — metadata PATCH (all three kinds)
    # =================================================================

    def update_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
        patch: dict[str, Any],
        expected_etag: str | None = None,
    ) -> LibraryItemRecord:
        existing = self._lookup_any(tenant_id=tenant_id, item_id=item_id)
        if existing is None:
            raise LibraryNotFound(item_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            # Read gate first — 404-not-403 for the "no read" case.
            raise LibraryNotFound(item_id)
        if existing.owner_user_id != caller_user_id:
            # Read OK (project member / admin) but writes are owner-only.
            raise LibraryForbidden(item_id)

        validated = self._validate_patch(existing, patch)

        # Page body edit invariant: bump version + rotate etag; If-Match
        # must agree with the current etag.
        bump_version = False
        if isinstance(existing, LibraryPageRecord) and "markdown" in validated:
            if expected_etag is not None and expected_etag != existing.version_etag:
                raise LibraryConflict("version_etag_mismatch")
            bump_version = True

        before = _dump_for_audit(existing)
        new_updates = dict(validated)
        new_updates["updated_at"] = _now()
        if bump_version:
            new_updates["version"] = existing.version + 1
            new_updates["version_etag"] = uuid4().hex
        new_record = existing.model_copy(update=new_updates)

        with self._store.transaction():
            stored = self._persist_update(new_record)
            self._store.append_audit(
                LibraryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=_action_for_kind(stored, "updated"),
                    target_kind=_target_kind_for(stored),
                    target_id=stored.id,
                    before_state=before,
                    after_state=_dump_for_audit(stored),
                    context={
                        "changed_fields": sorted(validated.keys()),
                        "project_id": stored.project_id,
                    },
                )
            )
        # Only enqueue when a content-relevant field changed. Tag-only
        # edits do not change the indexable text on files / datasets;
        # for pages the markdown is the only content axis (title is
        # part of the chunk-1 header so we treat a title edit as
        # content-changing too).
        if _patch_changes_indexable_content(stored, validated):
            self._safe_enqueue(stored)
        return stored

    # =================================================================
    # Writes — DELETE (soft)
    # =================================================================

    def delete_item(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        item_id: str,
    ) -> None:
        existing = self._lookup_any(tenant_id=tenant_id, item_id=item_id)
        if existing is None:
            raise LibraryNotFound(item_id)
        if not self._can_read(existing, caller_user_id, caller_roles):
            raise LibraryNotFound(item_id)
        if existing.owner_user_id != caller_user_id:
            raise LibraryForbidden(item_id)

        before = _dump_for_audit(existing)
        with self._store.transaction():
            if isinstance(existing, LibraryFileRecord):
                self._store.soft_delete_file(tenant_id=tenant_id, file_id=item_id)
            elif isinstance(existing, LibraryPageRecord):
                self._store.soft_delete_page(tenant_id=tenant_id, page_id=item_id)
            else:
                self._store.soft_delete_dataset(tenant_id=tenant_id, dataset_id=item_id)
            self._store.append_audit(
                LibraryAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action=_action_for_kind(existing, "deleted"),
                    target_kind=_target_kind_for(existing),
                    target_id=item_id,
                    before_state=before,
                    context={
                        "soft": True,
                        "project_id": existing.project_id,
                    },
                )
            )
        # Soft-delete cascades to embeddings: the indexer's claim
        # handler detects ``deleted_at IS NOT NULL`` and drops the
        # ``library_embeddings`` rows for this target. Enqueue a job
        # so the cascade runs even on a quiet system.
        self._safe_enqueue(existing)

    # =================================================================
    # Helpers
    # =================================================================

    def _lookup_any(self, *, tenant_id: str, item_id: str) -> LibraryItemRecord | None:
        """ID prefix tells us which table to hit — no cross-kind scan.

        The dispatch is order-stable: prefixes are unique per kind. A
        deployment-time ID-collision would be a bug at the issuer; the
        store's ``get_*`` methods already filter by ``tenant_id`` so
        cross-tenant scope is always honored.
        """

        if item_id.startswith("libfile_"):
            return self._store.get_file(tenant_id=tenant_id, file_id=item_id)
        if item_id.startswith("libpage_"):
            return self._store.get_page(tenant_id=tenant_id, page_id=item_id)
        if item_id.startswith("libds_"):
            return self._store.get_dataset(tenant_id=tenant_id, dataset_id=item_id)
        # Unknown prefix — defense-in-depth scan across all three
        # tables; collapses to None when nothing matches. This handles
        # ids that pre-date the prefix convention (none today) and any
        # future cross-tenant probe.
        return (
            self._store.get_file(tenant_id=tenant_id, file_id=item_id)
            or self._store.get_page(tenant_id=tenant_id, page_id=item_id)
            or self._store.get_dataset(tenant_id=tenant_id, dataset_id=item_id)
        )

    def _persist_update(self, record: LibraryItemRecord) -> LibraryItemRecord:
        if isinstance(record, LibraryFileRecord):
            return self._store.update_file(record)
        if isinstance(record, LibraryPageRecord):
            return self._store.update_page(record)
        return self._store.update_dataset(record)

    def _safe_enqueue(self, record: LibraryItemRecord) -> None:
        """Fire-and-forget enqueue.

        Indexing is a downstream optimisation — any failure in the
        enqueue path must not roll back the user's write. We log and
        carry on; the retention/sweeper passes pick up stragglers.
        """

        if self._enqueue_index_job is None:
            return
        try:
            self._enqueue_index_job(
                record.tenant_id,
                _kind_short(record),
                record.id,
            )
        except Exception:  # pragma: no cover — defensive
            _LOGGER.warning("library_indexer.enqueue_failed", exc_info=True)

    def _can_read(
        self,
        record: LibraryItemRecord,
        caller_user_id: str,
        caller_roles: Iterable[str],
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        if _is_admin(caller_roles):
            # Tenant admin compliance read (library-prd §7.1). The
            # actual access is audited at the route layer with a
            # distinct action when the admin is not the owner.
            return True
        if record.project_id is None:
            return False
        # Canonical ACL — single source of truth at
        # backend_app.projects.acl.is_member. No reimplementation.
        return is_member(
            self._membership,
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            user_id=caller_user_id,
        )

    # ----- validation ----------------------------------------------------

    def _validate_page_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise LibraryInvalidRequest("invalid_payload")
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise LibraryInvalidRequest("title_required")
        title = title.strip()
        if len(title) > _TITLE_MAX:
            raise LibraryInvalidRequest("title_too_long")
        markdown = payload.get("markdown")
        if not isinstance(markdown, str):
            raise LibraryInvalidRequest("markdown_required")
        if len(markdown.encode("utf-8")) > _MARKDOWN_MAX:
            raise LibraryInvalidRequest("markdown_too_long")
        tags = _validate_tags(payload.get("tags"))
        project_id = _validate_project_id(payload.get("project_id"))
        result: dict[str, Any] = {
            "title": title,
            "markdown": markdown,
            "tags": tags,
            "project_id": project_id,
        }
        if "source" in payload:
            result["source"] = payload["source"]
        return result

    def _validate_patch(
        self,
        existing: LibraryItemRecord,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise LibraryInvalidRequest("invalid_payload")
        updates: dict[str, Any] = {}

        if "tags" in patch:
            updates["tags"] = _validate_tags(patch["tags"])
        if "project_id" in patch:
            # ``project_id: null`` detaches from any project.
            updates["project_id"] = _validate_project_id(patch["project_id"])

        if isinstance(existing, LibraryPageRecord):
            if "title" in patch:
                title = patch["title"]
                if not isinstance(title, str) or not title.strip():
                    raise LibraryInvalidRequest("title_required")
                title = title.strip()
                if len(title) > _TITLE_MAX:
                    raise LibraryInvalidRequest("title_too_long")
                updates["title"] = title
            if "markdown" in patch:
                markdown = patch["markdown"]
                if not isinstance(markdown, str):
                    raise LibraryInvalidRequest("markdown_invalid")
                if len(markdown.encode("utf-8")) > _MARKDOWN_MAX:
                    raise LibraryInvalidRequest("markdown_too_long")
                updates["markdown"] = markdown
            if "name" in patch:
                # Pages don't have a ``name`` column; surface a 400 so
                # the FE picks the right field instead of silently
                # dropping the request.
                raise LibraryInvalidRequest("name_invalid_on_page")
            if "description" in patch:
                raise LibraryInvalidRequest("description_invalid_on_page")
        else:
            # Files + datasets share ``name``; only datasets have
            # ``description``.
            if "name" in patch:
                name = patch["name"]
                if not isinstance(name, str) or not name.strip():
                    raise LibraryInvalidRequest("name_required")
                name = name.strip()
                if len(name) > _NAME_MAX:
                    raise LibraryInvalidRequest("name_too_long")
                updates["name"] = name
            if "title" in patch:
                raise LibraryInvalidRequest("title_invalid_on_non_page")
            if "markdown" in patch:
                raise LibraryInvalidRequest("markdown_invalid_on_non_page")
            if isinstance(existing, LibraryDatasetRecord) and "description" in patch:
                description = patch["description"]
                if description is None:
                    updates["description"] = None
                else:
                    if not isinstance(description, str):
                        raise LibraryInvalidRequest("description_invalid")
                    if len(description) > _DESCRIPTION_MAX:
                        raise LibraryInvalidRequest("description_too_long")
                    updates["description"] = description
            elif "description" in patch:
                # Files don't carry a description column.
                raise LibraryInvalidRequest("description_invalid_on_file")

        if not updates:
            raise LibraryInvalidRequest("empty_patch")
        return updates


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _is_admin(caller_roles: Iterable[str]) -> bool:
    return any(role in _ADMIN_ROLES for role in caller_roles)


def _validate_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LibraryInvalidRequest("tags_invalid")
    if len(value) > _TAG_COUNT_MAX:
        raise LibraryInvalidRequest("tags_too_many")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise LibraryInvalidRequest("tag_invalid_entry")
        tag = item.strip()
        if not tag:
            continue
        if len(tag) > _TAG_MAX_LEN:
            raise LibraryInvalidRequest("tag_too_long")
        if tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def _validate_project_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LibraryInvalidRequest("project_id_invalid")
    return value.strip()


def _validate_source(source: Any) -> None:
    if not isinstance(source, dict):
        raise LibraryInvalidRequest("source_invalid")
    kind = source.get("kind")
    if kind not in _VALID_SOURCE_KINDS:
        raise LibraryInvalidRequest("source_kind_invalid")


def _kind_short(record: LibraryItemRecord) -> str:
    """Indexer queue uses the short kind name (``file`` / ``page`` /
    ``dataset``) — distinct from the audit ``target_kind`` which uses
    the ``library_<kind>`` form."""

    if isinstance(record, LibraryFileRecord):
        return "file"
    if isinstance(record, LibraryPageRecord):
        return "page"
    return "dataset"


# Patch fields that change the indexable text. Tag-only edits do not
# trigger re-embedding because the embedding model never sees the
# tags column (tags live in the tsvector, not in the embedding chunk).
_CONTENT_FIELDS_PAGE: frozenset[str] = frozenset({"markdown", "title"})
_CONTENT_FIELDS_FILE: frozenset[str] = frozenset({"name"})
_CONTENT_FIELDS_DATASET: frozenset[str] = frozenset({"name", "description"})


def _patch_changes_indexable_content(
    record: LibraryItemRecord, patch: dict[str, Any]
) -> bool:
    if isinstance(record, LibraryPageRecord):
        return bool(_CONTENT_FIELDS_PAGE.intersection(patch))
    if isinstance(record, LibraryFileRecord):
        return bool(_CONTENT_FIELDS_FILE.intersection(patch))
    return bool(_CONTENT_FIELDS_DATASET.intersection(patch))


def _target_kind_for(record: LibraryItemRecord) -> str:
    if isinstance(record, LibraryFileRecord):
        return "library_file"
    if isinstance(record, LibraryPageRecord):
        return "library_page"
    return "library_dataset"


def _action_for_kind(record: LibraryItemRecord, verb: str) -> str:
    """Dotted audit action — ``library.file_created`` etc. Keeps the
    audit topology kind-aware for SIEM filters."""

    if isinstance(record, LibraryFileRecord):
        return f"library.file_{verb}"
    if isinstance(record, LibraryPageRecord):
        return f"library.page_{verb}"
    return f"library.dataset_{verb}"


def _dump_page_for_audit(record: LibraryPageRecord) -> dict[str, Any]:
    """Redact ``markdown`` (PII risk per library-prd §7.4) and replace
    with hash + length. Tags + title are not sensitive."""

    body_bytes = record.markdown.encode("utf-8")
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "owner_user_id": record.owner_user_id,
        "project_id": record.project_id,
        "kind": record.kind,
        "title": record.title,
        # Body content NEVER serialized — content-hash + byte length only.
        "markdown_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "markdown_bytes": len(body_bytes),
        "version": record.version,
        "version_etag": record.version_etag,
        "tags": list(record.tags),
        "index_status": record.index_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "deleted_at": (record.deleted_at.isoformat() if record.deleted_at else None),
    }


def _dump_file_for_audit(record: LibraryFileRecord) -> dict[str, Any]:
    """Files: omit ``blob_ref`` / ``thumbnail_blob_ref`` from audit
    context (library-prd §7.4 — no cleartext object-store URLs in audit
    rows). The ``target_id`` is the audit linkage."""

    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "owner_user_id": record.owner_user_id,
        "project_id": record.project_id,
        "kind": record.kind,
        "file_kind": record.file_kind,
        "name": record.name,
        "mime": record.mime,
        "size_bytes": record.size_bytes,
        "checksum_sha256": record.checksum_sha256,
        "tags": list(record.tags),
        "index_status": record.index_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "deleted_at": (record.deleted_at.isoformat() if record.deleted_at else None),
    }


def _dump_dataset_for_audit(record: LibraryDatasetRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "owner_user_id": record.owner_user_id,
        "project_id": record.project_id,
        "kind": record.kind,
        "name": record.name,
        "description": record.description,
        "row_count": record.row_count,
        "size_bytes": record.size_bytes,
        "format": record.format,
        "checksum_sha256": record.checksum_sha256,
        "tags": list(record.tags),
        "schema_columns": [col.get("name") for col in record.columns_schema],
        "index_status": record.index_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "deleted_at": (record.deleted_at.isoformat() if record.deleted_at else None),
    }


def _dump_for_audit(record: LibraryItemRecord) -> dict[str, Any]:
    if isinstance(record, LibraryFileRecord):
        return _dump_file_for_audit(record)
    if isinstance(record, LibraryPageRecord):
        return _dump_page_for_audit(record)
    return _dump_dataset_for_audit(record)


__all__ = [
    "LibraryConflict",
    "LibraryForbidden",
    "LibraryInvalidRequest",
    "LibraryNotFound",
    "LibraryService",
]
