"""Library destination (Phase 7 — P7-A1 metadata + CRUD).

Three storage kinds share one destination: **files** (bytes in object
store; metadata + opaque ``blob_ref`` here), **pages** (markdown body
canonical in this table), **datasets** (Parquet/CSV/JSONL bytes in
object store; schema + row_count here).

Authorization (library-prd §7 + cross-audit §1.3 binding 2026-05-17):

* **Reads.** Owner OR ``is_project_member(tenant_id, project_id, user)``
  (any role) when ``project_id IS NOT NULL`` OR tenant admin (compliance
  read, audited at the route layer). Non-readers see **404**, not 403
  (existence-not-leaked default).
* **Writes.** ``owner_user_id`` only. Project members CANNOT mutate
  metadata or body. Tenant admin compliance reads do not lift to write.
* **Cross-tenant.** Tenant scoping is the first filter on every query;
  the verified bearer's tenant claim is the source of truth — never the
  request body (cross-audit §3.1).

ACL is **consumed** via the canonical
:func:`backend_app.projects.acl.is_member` — a second implementation of
the membership query in another destination is a bug. The library
service composes a :class:`ProjectMembershipPort` adapter (default: the
in-memory adapter wired against the in-process projects store; the
Postgres adapter reads the same ``project_memberships`` table the
projects service writes).

Scope of P7-A1 (this package):

* CRUD: ``GET /v1/library``, ``GET /v1/library/{id}``,
  ``POST /v1/library/pages``, ``PATCH /v1/library/{id}``,
  ``DELETE /v1/library/{id}``.
* Tables + soft-delete + audit.
* Multi-value OR filters per cross-audit §1.5.

Out of scope (other agents own these):

* File upload signed-URL handshake + dataset ingest finalize — **P7-A2**.
* ``GET /v1/library/{id}/preview`` and ``/download`` signed-URL routes
  — **P7-A2**.
* ``POST /v1/library/search`` + SSE search stream + embeddings
  + library_indexer + Purpose.LIBRARY_RETRIEVAL/INDEXING — **P7-A3**.

Wire shape is canonical at ``packages/api-types/src/library.ts``; the
Python mirrors live in :mod:`backend_app.library.routes`.
"""

from __future__ import annotations

from backend_app.library.routes import register_library_routes
from backend_app.library.service import (
    LibraryConflict,
    LibraryForbidden,
    LibraryInvalidRequest,
    LibraryNotFound,
    LibraryService,
)
from backend_app.library.store import (
    InMemoryLibraryStore,
    LibraryAuditRecord,
    LibraryDatasetRecord,
    LibraryFileRecord,
    LibraryPageRecord,
    LibraryStore,
)


__all__ = [
    "InMemoryLibraryStore",
    "LibraryAuditRecord",
    "LibraryConflict",
    "LibraryDatasetRecord",
    "LibraryFileRecord",
    "LibraryForbidden",
    "LibraryInvalidRequest",
    "LibraryNotFound",
    "LibraryPageRecord",
    "LibraryService",
    "LibraryStore",
    "register_library_routes",
]
