"""Memory destination (Phase 12 P12-A3) — CRUD + proposals + search + SSE.

Sub-PRD: ``docs/atlas-new-design/destinations/team-memory-cmdk-prd.md``
(§3.2 wire / §4.2 endpoints / §5.2 storage / §6.2 ACL / §9 proposals).

Boundary discipline:

* Memory embeddings ride ``library_embeddings`` with
  ``target_kind="memory"`` (sub-PRD §5.1) — there is no parallel
  ``memory_embeddings`` table. The indexer (``indexer.py``) enqueues
  into the existing ``library_index_jobs`` queue; the Library worker
  drains it.
* Project-scoped reads use the canonical
  :func:`backend_app.projects.acl.is_member`.
* No LLM SDK imports in this tree — the embeddings port goes through
  ai-backend's ``/internal/v1/llm/embed`` with the
  ``Purpose.MEMORY_RETRIEVAL`` tag (sibling P12-A5 adds the enum
  value; the port consumes the string regardless).

Surfaces exported:

* Service + store + record types.
* Route registrar :func:`register_memory_routes` and the SSE
  registrar :func:`register_memory_sse_routes`.
* Hybrid search engine + in-memory adapter.
* The memory indexer that writes into ``library_index_jobs``.
"""

from __future__ import annotations

from backend_app.memory.indexer import MemoryIndexer
from backend_app.memory.routes import register_memory_routes
from backend_app.memory.search import (
    InMemoryMemorySearchIndex,
    MemoryEmbeddingsClient,
    MemorySearchEngine,
    MemorySearchEnvelope,
    MemorySearchIndex,
    MemorySearchResultHit,
)
from backend_app.memory.service import (
    MemoryForbidden,
    MemoryInvalidRequest,
    MemoryNotFound,
    MemoryService,
)
from backend_app.memory.sse import (
    InMemoryMemoryActivityBus,
    MemoryActivityBus,
    MemoryEventEnvelope,
    MemoryEventType,
    MemorySseAdapter,
    register_memory_sse_routes,
)
from backend_app.memory.store import (
    InMemoryMemoryStore,
    MemoryAuditRecord,
    MemoryItemRecord,
    MemoryKindLiteral,
    MemoryProposalRecord,
    MemoryProposalStatusLiteral,
    MemoryScopeLiteral,
    MemoryStore,
    is_valid_sort_token,
)

__all__ = [
    "InMemoryMemoryActivityBus",
    "InMemoryMemorySearchIndex",
    "InMemoryMemoryStore",
    "MemoryActivityBus",
    "MemoryAuditRecord",
    "MemoryEmbeddingsClient",
    "MemoryEventEnvelope",
    "MemoryEventType",
    "MemoryForbidden",
    "MemoryIndexer",
    "MemoryInvalidRequest",
    "MemoryItemRecord",
    "MemoryKindLiteral",
    "MemoryNotFound",
    "MemoryProposalRecord",
    "MemoryProposalStatusLiteral",
    "MemoryScopeLiteral",
    "MemorySearchEngine",
    "MemorySearchEnvelope",
    "MemorySearchIndex",
    "MemorySearchResultHit",
    "MemoryService",
    "MemorySseAdapter",
    "MemoryStore",
    "is_valid_sort_token",
    "register_memory_routes",
    "register_memory_sse_routes",
]
