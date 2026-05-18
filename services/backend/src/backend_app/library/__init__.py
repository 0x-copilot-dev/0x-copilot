"""Library destination (Phase 7) — metadata CRUD + blob storage.

Three storage kinds share one destination: **files** (bytes in object
store; metadata + opaque ``blob_ref`` here), **pages** (markdown body
canonical in this table), **datasets** (Parquet/CSV/JSONL bytes in
object store; schema + row_count here).

Authorization (library-prd §7 + cross-audit §1.3 binding 2026-05-17):

* **Reads.** Owner OR ``is_project_member(tenant_id, project_id, user)``
  (any role) when ``project_id IS NOT NULL`` OR tenant admin (compliance
  read, audited at the route layer). Non-readers see **404**, not 403.
* **Writes.** ``owner_user_id`` only.
* **Cross-tenant.** Tenant scoping is the first filter on every query.

ACL is **consumed** via the canonical
:func:`backend_app.projects.acl.is_member` — a second implementation is a bug.

Surfaces:

* CRUD (P7-A1): ``GET /v1/library``, ``GET /v1/library/{id}``,
  ``POST /v1/library/pages``, ``PATCH /v1/library/{id}``,
  ``DELETE /v1/library/{id}``.
* Blob storage (P7-A2): ``blob_store`` port + adapters; bytes never
  proxy through the API — only signed URLs. ``upload_routes`` mints
  upload grants, finalizes uploads, and produces signed download URLs.
* Retrieval (P7-A3, deferred to Phase 7.5): search + embeddings.

Wire shape is canonical at ``packages/api-types/src/library.ts``.
"""

from __future__ import annotations

from backend_app.library.blob_store import (
    DATASET_MIME_SIZE_LIMITS,
    DEFAULT_DATASET_MAX_BYTES,
    DEFAULT_FILE_MAX_BYTES,
    MIME_SIZE_LIMITS,
    SIGNED_URL_MAX_TTL_SECONDS,
    BlobMeta,
    BlobMimeNotAllowedError,
    BlobNotFoundError,
    BlobSizeLimitExceededError,
    BlobStoreError,
    BlobStorePort,
    BlobTokenInvalidError,
    LocalDiskBlobStore,
    S3BlobStore,
    SignedDownloadUrl,
    SignedUploadGrant,
)
from backend_app.library.embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL_ID,
    Chunk,
    EmbeddingRow,
    EmbeddingsStore,
    InMemoryEmbeddingsStore,
    build_embedding_rows,
    chunk_text,
    compute_content_hash,
    extract_text,
)
from backend_app.library.index_jobs import (
    InMemoryLibraryIndexJobsStore,
    IndexJobClaim,
    LibraryIndexJobRecord,
    LibraryIndexJobsStore,
)
from backend_app.library.routes import register_library_routes
from backend_app.library.search import (
    EmbeddingsClientPort,
    InMemoryLibrarySearchIndex,
    LibrarySearchIndex,
    NoopEmbeddingsClient,
    NoopRerankClient,
    RerankClientPort,
    SearchEngine,
    rrf_fuse,
)
from backend_app.library.search_routes import register_library_search_routes
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
    "Chunk",
    "DATASET_MIME_SIZE_LIMITS",
    "DEFAULT_DATASET_MAX_BYTES",
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "DEFAULT_EMBEDDING_MODEL_ID",
    "DEFAULT_FILE_MAX_BYTES",
    "EmbeddingRow",
    "EmbeddingsClientPort",
    "EmbeddingsStore",
    "InMemoryEmbeddingsStore",
    "InMemoryLibraryIndexJobsStore",
    "InMemoryLibrarySearchIndex",
    "InMemoryLibraryStore",
    "IndexJobClaim",
    "LibraryAuditRecord",
    "LibraryConflict",
    "LibraryDatasetRecord",
    "LibraryFileRecord",
    "LibraryForbidden",
    "LibraryIndexJobRecord",
    "LibraryIndexJobsStore",
    "LibraryInvalidRequest",
    "LibraryNotFound",
    "LibraryPageRecord",
    "LibrarySearchIndex",
    "LibraryService",
    "LibraryStore",
    "NoopEmbeddingsClient",
    "NoopRerankClient",
    "RerankClientPort",
    "SearchEngine",
    "BlobMeta",
    "BlobMimeNotAllowedError",
    "BlobNotFoundError",
    "BlobSizeLimitExceededError",
    "BlobStoreError",
    "BlobStorePort",
    "BlobTokenInvalidError",
    "LocalDiskBlobStore",
    "MIME_SIZE_LIMITS",
    "S3BlobStore",
    "SIGNED_URL_MAX_TTL_SECONDS",
    "SignedDownloadUrl",
    "SignedUploadGrant",
    "build_embedding_rows",
    "chunk_text",
    "compute_content_hash",
    "extract_text",
    "register_library_routes",
    "register_library_search_routes",
    "rrf_fuse",
]
