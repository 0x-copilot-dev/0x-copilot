"""Memory indexer enqueue — write into the existing Library index queue.

Sub-PRD §5.1 binding: memory embeddings live in ``library_embeddings``
under ``target_kind="memory"``. There is intentionally NO parallel
``memory_embeddings`` table. The Library indexer worker
(:mod:`backend_app.jobs.library_indexer`) already drains
``library_index_jobs`` rows; once the worker learns the ``memory``
target_kind handler (sibling P12-A5 widens the dispatcher), memory rows
ride the same code path.

For P12-A3 our responsibility is the enqueue side only:

* Compose a :class:`MemoryIndexer` that holds a
  :class:`LibraryIndexJobsStore` reference.
* On every memory create / update / delete the service layer calls
  :meth:`MemoryIndexer.enqueue` which inserts a job with
  ``target_kind="memory"`` and ``target_id=<memory_id>``. The existing
  worker's claim / retry / idempotency machinery does the rest.

The store call is wrapped in a defensive ``try`` block at the service
layer (best-effort) — a queue blip must never block a memory write. The
worker has a stuck-claim reaper + retry policy so any missed enqueue is
recovered manually via the re-index admin path (out of scope here).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend_app.library.index_jobs import LibraryIndexJobsStore


@dataclass
class MemoryIndexer:
    """Thin enqueue wrapper onto :class:`LibraryIndexJobsStore`.

    The job row carries ``target_kind="memory"`` so the existing worker
    can branch on it once P12-A5 widens its dispatcher; until then the
    pending jobs simply queue (the worker today only claims rows whose
    target_kind it recognises, so memory rows wait safely). The enqueue
    is idempotent — the in-flight UNIQUE index on
    ``(tenant_id, target_kind, target_id)`` collapses re-enqueues into
    one pending row.
    """

    jobs_store: LibraryIndexJobsStore

    def enqueue(self, *, tenant_id: str, memory_id: str) -> None:
        # ``enqueue`` on the in-memory adapter returns the job record;
        # we ignore the return — the service layer only cares that the
        # row landed. Postgres adapter follows the same Protocol.
        self.jobs_store.enqueue(
            tenant_id=tenant_id,
            target_kind="memory",  # type: ignore[arg-type]
            target_id=memory_id,
        )


__all__ = ["MemoryIndexer"]
