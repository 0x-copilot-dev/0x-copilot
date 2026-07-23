"""Library store — adapter contract + in-memory implementation.

Storage shape mirrors :mod:`backend_app.library.schema.sql`. The
in-memory adapter is the dev / test default; the Postgres adapter
(deployment-injected) implements the same Protocol.

Authorization is **not** enforced here. The service layer
(:class:`LibraryService`) composes the store with the canonical
:class:`ProjectMembershipPort` to decide read / write authority; the
store exposes raw queries scoped to ``tenant_id``.

Soft-delete (``deleted_at``) keeps rows visible to compliance reads
(``include_deleted=True``) but invisible to the public list / get
paths. The cleanup job in ``jobs/library_retention.py`` (out of scope
for P7-A1) hard-deletes after the retention window (library-prd §5.3:
files 90d, pages 365d, datasets 90d).

Three kinds, three tables — the union is composed at the service /
route layer. Keeping each kind in its own table preserves kind-specific
columns (file: mime/size/blob; page: markdown/version; dataset:
schema/row_count) and lets the index strategy stay per-kind
(library-prd §5.2).
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _file_id() -> str:
    return f"libfile_{uuid4().hex}"


def _page_id() -> str:
    return f"libpage_{uuid4().hex}"


def _dataset_id() -> str:
    return f"libds_{uuid4().hex}"


def _audit_id() -> str:
    return f"audlib_{uuid4().hex}"


def _etag() -> str:
    return uuid4().hex


LibraryItemKindLiteral = Literal["file", "page", "dataset"]


# ---------------------------------------------------------------------------
# Records (Pydantic; shared with the Postgres + in-memory adapters)
# ---------------------------------------------------------------------------


class LibraryFileRecord(BaseModel):
    """One row in the ``library_files`` table."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_file_id)
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    kind: Literal["file"] = "file"
    file_kind: str  # doc | image | pdf | sheet | slide | other
    name: str
    mime: str
    size_bytes: int = 0
    blob_ref: str
    thumbnail_blob_ref: str | None = None
    # ``source`` is a discriminated union on the wire; stored as JSONB.
    # The store does no validation — service layer enforces shape.
    source: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    index_status: str = "pending"
    index_error: str | None = None
    checksum_sha256: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_accessed_at: datetime | None = None
    deleted_at: datetime | None = None


class LibraryPageRecord(BaseModel):
    """One row in the ``library_pages`` table."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_page_id)
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    kind: Literal["page"] = "page"
    title: str
    markdown: str
    version: int = 1
    version_etag: str = Field(default_factory=_etag)
    source: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    index_status: str = "pending"
    index_error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_accessed_at: datetime | None = None
    deleted_at: datetime | None = None


class LibraryDatasetRecord(BaseModel):
    """One row in the ``library_datasets`` table.

    NB: storage column is named ``columns_schema`` (not ``schema``) so it
    cannot collide with Pydantic's reserved ``model_*`` namespace via
    the legacy ``schema`` attribute. The wire field name (``schema``)
    is rebuilt at the route layer in :func:`_to_wire`.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: str = Field(default_factory=_dataset_id)
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    kind: Literal["dataset"] = "dataset"
    name: str
    description: str | None = None
    columns_schema: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    size_bytes: int = 0
    blob_ref: str
    format: str = "parquet"  # parquet | csv | jsonl
    source: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    index_status: str = "pending"
    index_error: str | None = None
    checksum_sha256: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_accessed_at: datetime | None = None
    deleted_at: datetime | None = None


# Union type used by service + route layer for kind-agnostic returns.
LibraryItemRecord = LibraryFileRecord | LibraryPageRecord | LibraryDatasetRecord


class LibraryAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    The audit-chain integration (``packages/audit-chain``) signs + chains
    rows in production. The in-memory adapter appends raw rows for tests;
    the Postgres adapter writes through the chain signer (same pattern
    as ``projects.store.ProjectAuditRecord``).
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str  # library_file | library_page | library_dataset
    target_id: str
    # NOTE: We deliberately do NOT log raw page markdown into audit rows
    # (library-prd §7.4 binding). The service layer is responsible for
    # redacting body content before constructing this record; the store
    # writes the dict it receives.
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None  # cross-audit §1.4 — what + why
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class LibraryStore(Protocol):
    """Adapter contract for the Postgres + in-memory library stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- files ---------------------------------------------------------

    def insert_file(self, record: LibraryFileRecord) -> LibraryFileRecord: ...

    def get_file(
        self, *, tenant_id: str, file_id: str, include_deleted: bool = False
    ) -> LibraryFileRecord | None: ...

    def update_file(self, record: LibraryFileRecord) -> LibraryFileRecord: ...

    def soft_delete_file(self, *, tenant_id: str, file_id: str) -> bool: ...

    # -- pages ---------------------------------------------------------

    def insert_page(self, record: LibraryPageRecord) -> LibraryPageRecord: ...

    def get_page(
        self, *, tenant_id: str, page_id: str, include_deleted: bool = False
    ) -> LibraryPageRecord | None: ...

    def update_page(self, record: LibraryPageRecord) -> LibraryPageRecord: ...

    def soft_delete_page(self, *, tenant_id: str, page_id: str) -> bool: ...

    # -- datasets ------------------------------------------------------

    def insert_dataset(self, record: LibraryDatasetRecord) -> LibraryDatasetRecord: ...

    def get_dataset(
        self, *, tenant_id: str, dataset_id: str, include_deleted: bool = False
    ) -> LibraryDatasetRecord | None: ...

    def update_dataset(self, record: LibraryDatasetRecord) -> LibraryDatasetRecord: ...

    def soft_delete_dataset(self, *, tenant_id: str, dataset_id: str) -> bool: ...

    # -- unified list --------------------------------------------------

    def list_items(
        self,
        *,
        tenant_id: str,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str, ...] | None = None,
        owner_user_ids: tuple[str, ...] | None = None,
        source_kinds: tuple[str, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        index_statuses: tuple[str, ...] | None = None,
        file_kinds: tuple[str, ...] | None = None,
        q: str | None = None,
        visible_to_user_id: str | None = None,
        readable_project_ids: tuple[str, ...] = (),
        admin: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[LibraryItemRecord, ...], str | None, dict[str, int]]: ...

    # -- rollup counts (PRD-07) ----------------------------------------

    def count_by_project(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        caller_user_id: str,
        caller_roles: tuple[str, ...],
    ) -> dict[str, dict[str, int]]: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: LibraryAuditRecord) -> LibraryAuditRecord: ...

    def list_audit_for_target(
        self, *, tenant_id: str, target_id: str
    ) -> tuple[LibraryAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryLibraryStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is
    the first filter on every query; soft-delete (``deleted_at``) hides
    rows from default list / get; multi-axis filtering composes
    in-process (the Postgres adapter pushes the same predicates into
    SQL with the indexes from library-prd §5.2).
    """

    files: dict[str, LibraryFileRecord] = field(default_factory=dict)
    pages: dict[str, LibraryPageRecord] = field(default_factory=dict)
    datasets: dict[str, LibraryDatasetRecord] = field(default_factory=dict)
    audits: list[LibraryAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # Same shape as :class:`InMemoryProjectsStore` so the service
        # layer composes against both stores without branching.
        yield

    # -- files ---------------------------------------------------------

    def insert_file(self, record: LibraryFileRecord) -> LibraryFileRecord:
        self.files[record.id] = record
        return record

    def get_file(
        self, *, tenant_id: str, file_id: str, include_deleted: bool = False
    ) -> LibraryFileRecord | None:
        record = self.files.get(file_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def update_file(self, record: LibraryFileRecord) -> LibraryFileRecord:
        self.files[record.id] = record
        return record

    def soft_delete_file(self, *, tenant_id: str, file_id: str) -> bool:
        record = self.files.get(file_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.files[file_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    # -- pages ---------------------------------------------------------

    def insert_page(self, record: LibraryPageRecord) -> LibraryPageRecord:
        self.pages[record.id] = record
        return record

    def get_page(
        self, *, tenant_id: str, page_id: str, include_deleted: bool = False
    ) -> LibraryPageRecord | None:
        record = self.pages.get(page_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def update_page(self, record: LibraryPageRecord) -> LibraryPageRecord:
        self.pages[record.id] = record
        return record

    def soft_delete_page(self, *, tenant_id: str, page_id: str) -> bool:
        record = self.pages.get(page_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.pages[page_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    # -- datasets ------------------------------------------------------

    def insert_dataset(self, record: LibraryDatasetRecord) -> LibraryDatasetRecord:
        self.datasets[record.id] = record
        return record

    def get_dataset(
        self, *, tenant_id: str, dataset_id: str, include_deleted: bool = False
    ) -> LibraryDatasetRecord | None:
        record = self.datasets.get(dataset_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def update_dataset(self, record: LibraryDatasetRecord) -> LibraryDatasetRecord:
        self.datasets[record.id] = record
        return record

    def soft_delete_dataset(self, *, tenant_id: str, dataset_id: str) -> bool:
        record = self.datasets.get(dataset_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.datasets[dataset_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    # -- unified list --------------------------------------------------

    def list_items(
        self,
        *,
        tenant_id: str,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str, ...] | None = None,
        owner_user_ids: tuple[str, ...] | None = None,
        source_kinds: tuple[str, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        index_statuses: tuple[str, ...] | None = None,
        file_kinds: tuple[str, ...] | None = None,
        q: str | None = None,
        visible_to_user_id: str | None = None,
        readable_project_ids: tuple[str, ...] = (),
        admin: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[LibraryItemRecord, ...], str | None, dict[str, int]]:
        """Cross-kind unified list with project-aware visibility scoping.

        ``visible_to_user_id`` + ``readable_project_ids`` + ``admin``
        together encode the cross-audit §1.3 read predicate:

        * ``admin=True`` short-circuits visibility (compliance read).
        * Otherwise: row is visible iff
          ``owner_user_id == visible_to_user_id`` OR (``project_id`` is
          not None AND ``project_id IN readable_project_ids``).

        The list returns ``counts_by_kind`` computed on the same
        filtered set (after visibility + filter[*] but before pagination)
        so the list response can render the "Files N · Pages M ·
        Datasets K" strip without a second round-trip.
        """

        all_records: list[LibraryItemRecord] = []
        if kinds is None or "file" in kinds:
            all_records.extend(self.files.values())
        if kinds is None or "page" in kinds:
            all_records.extend(self.pages.values())
        if kinds is None or "dataset" in kinds:
            all_records.extend(self.datasets.values())

        q_normalized = q.strip().lower() if q else None
        tags_set = set(tags) if tags else None
        owner_set = set(owner_user_ids) if owner_user_ids else None
        project_set = set(project_ids) if project_ids else None
        source_set = set(source_kinds) if source_kinds else None
        index_status_set = set(index_statuses) if index_statuses else None
        file_kind_set = set(file_kinds) if file_kinds else None
        readable_set = set(readable_project_ids)

        candidates: list[LibraryItemRecord] = []
        for record in all_records:
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None and not include_deleted:
                continue

            # Visibility gate (canonical ACL applied at service layer
            # for read-by-id; for list we accept the pre-computed
            # ``readable_project_ids`` set so the service makes ONE call
            # to the membership port instead of N).
            if visible_to_user_id is not None and not admin:
                visible = record.owner_user_id == visible_to_user_id or (
                    record.project_id is not None and record.project_id in readable_set
                )
                if not visible:
                    continue

            # Public filter[*] axes (multi-value OR per cross-audit §1.5).
            if owner_set is not None and record.owner_user_id not in owner_set:
                continue
            if project_set is not None and record.project_id not in project_set:
                continue
            if source_set is not None:
                if record.source.get("kind") not in source_set:
                    continue
            if tags_set is not None and not tags_set.intersection(record.tags):
                continue
            if index_status_set is not None and record.index_status not in (
                index_status_set
            ):
                continue
            if file_kind_set is not None:
                # ``file_kind`` is meaningful only on files; reject
                # non-file rows when this filter is set.
                if not isinstance(record, LibraryFileRecord):
                    continue
                if record.file_kind not in file_kind_set:
                    continue
            if q_normalized:
                haystack = _haystack(record)
                if q_normalized not in haystack:
                    continue

            candidates.append(record)

        # Counts-by-kind computed BEFORE pagination so the destination
        # header strip stays stable across pages.
        counts: dict[str, int] = {"file": 0, "page": 0, "dataset": 0}
        for record in candidates:
            counts[record.kind] = counts.get(record.kind, 0) + 1

        candidates.sort(key=_sort_key(sort), reverse=_sort_descending(sort))
        start = _decode_cursor(cursor)
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor, counts

    # -- rollup counts (PRD-07) ----------------------------------------

    def count_by_project(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        caller_user_id: str,
        caller_roles: tuple[str, ...],
    ) -> dict[str, dict[str, int]]:
        """Group live library rows by project into ``files`` + ``library_items``.

        ``files`` counts only ``kind='file'`` rows (the design's "N files");
        ``library_items`` counts every kind (file + page + dataset). The
        ``project_ids`` are already the caller's readable projects, so every
        project-scoped row in them is visible to the caller (own row OR
        ``project_id ∈ readable_project_ids`` — the same predicate ``list_items``
        applies), which is why no extra per-row visibility gate is needed here.
        Soft-deleted rows are excluded.
        """

        wanted = set(project_ids)
        result: dict[str, dict[str, int]] = {}
        for record in (
            *self.files.values(),
            *self.pages.values(),
            *self.datasets.values(),
        ):
            if record.tenant_id != tenant_id or record.deleted_at is not None:
                continue
            pid = record.project_id
            if pid is None or pid not in wanted:
                continue
            bucket = result.setdefault(pid, {"files": 0, "library_items": 0})
            bucket["library_items"] += 1
            if record.kind == "file":
                bucket["files"] += 1
        return result

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: LibraryAuditRecord) -> LibraryAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_target(
        self, *, tenant_id: str, target_id: str
    ) -> tuple[LibraryAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == target_id
        )


# ---------------------------------------------------------------------------
# Sort + haystack helpers
# ---------------------------------------------------------------------------


_VALID_SORTS: frozenset[str] = frozenset(
    {
        "updated_at:desc",
        "updated_at:asc",
        "created_at:desc",
        "created_at:asc",
        "name:asc",
        "name:desc",
        "last_accessed_at:desc",
        "size_bytes:desc",
    }
)


def _sort_descending(sort: str) -> bool:
    return sort.endswith(":desc")


def _record_name(record: LibraryItemRecord) -> str:
    if isinstance(record, LibraryPageRecord):
        return record.title
    return record.name


def _record_size(record: LibraryItemRecord) -> int:
    if isinstance(record, LibraryPageRecord):
        # Page "size" proxy = markdown length; consistent ordering for
        # size_bytes:desc across kinds.
        return len(record.markdown)
    return record.size_bytes


def _sort_key(sort: str):
    field_name, _ = sort.split(":", 1) if ":" in sort else (sort, "desc")
    if field_name == "name":
        return lambda r: (_record_name(r).lower(), r.id)
    if field_name == "created_at":
        return lambda r: (r.created_at, r.id)
    if field_name == "last_accessed_at":
        return lambda r: (
            r.last_accessed_at or datetime.min.replace(tzinfo=timezone.utc),
            r.id,
        )
    if field_name == "size_bytes":
        return lambda r: (_record_size(r), r.id)
    return lambda r: (r.updated_at, r.id)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


def _haystack(record: LibraryItemRecord) -> str:
    """Free-text search target. For pages we include the FIRST 2 KB of
    markdown to match the Postgres tsvector spec (library-prd §5.1):
    ``to_tsvector('simple', title || ' ' || substring(markdown,1,2048))``.
    Body bytes are NEVER logged elsewhere — this is an in-process match
    only.
    """

    if isinstance(record, LibraryPageRecord):
        return (
            f"{record.title} {record.markdown[:2048]} {' '.join(record.tags)}".lower()
        )
    if isinstance(record, LibraryDatasetRecord):
        return (
            f"{record.name} {record.description or ''} {' '.join(record.tags)}".lower()
        )
    return f"{record.name} {' '.join(record.tags)}".lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iter_audit_rows_for_bulk(
    records: Iterable[LibraryAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[LibraryAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryLibraryStore",
    "LibraryAuditRecord",
    "LibraryDatasetRecord",
    "LibraryFileRecord",
    "LibraryItemKindLiteral",
    "LibraryItemRecord",
    "LibraryPageRecord",
    "LibraryStore",
    "iter_audit_rows_for_bulk",
]
