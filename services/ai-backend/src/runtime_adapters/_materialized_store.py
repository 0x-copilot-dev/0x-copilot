"""Shared in-memory-view + domain policy for the two dict-backed runtime stores.

``InMemoryRuntimeApiStore`` and ``FileRuntimeApiStore`` both answer the runtime
API port by holding the whole dataset as in-process Python dicts and operating
on those dicts. They are the same store twice over: the file-native adapter *is*
the in-memory adapter plus a JSONL persistence sidecar and an ``asyncio`` lock,
and nothing more. The Postgres adapter is different in kind — it keeps no
materialized view and round-trips genuinely different SQL (RLS tenant fences,
``ON CONFLICT`` retry loops) — so it deliberately does **not** extend this base.

The point of this class is to write that shared "materialized-view + domain
policy" exactly once, so a change to the policy (an idempotency key, a sort
order, a scope filter) cannot silently drift between the two backends — which is
the maintain-two-ways divergence class the adapter-collapse analysis set out to
shrink (``docs/audit/ai-backend-smells/ADAPTER-COLLAPSE-REALITY.md``).

Only two things differ between the two backends, and they are the two hooks this
base leaves open:

* :meth:`_state_guard` — the serialization boundary around a back-office-state
  read/write. The in-memory store runs single-threaded under the event loop and
  takes no lock (the default is a no-op context manager); the file store
  overrides it with the shared ``asyncio.Lock`` that guards its append-with-fold
  state ledgers, so a concurrent append and a durability flush cannot interleave.
* the per-table ``_persist_*`` hooks — a durability sidecar for a newly-appended
  row. The in-memory store keeps nothing on disk, so each defaults to a no-op;
  the file store overrides it to append the row to its JSONL ledger.

This base owns no ``__init__``: it is a pure mixin, so the concrete stores stay
the single source of truth for how their in-memory dicts are constructed (and,
for the file store, replayed from disk on :meth:`open`). It declares the dict
attributes it reads as bare annotations only.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, nullcontext

from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
    RuntimeModelCallUsageRecord,
    UsageAttributionEdge,
)


class MaterializedViewStoreBase:
    """In-memory-view + domain policy shared by the in-memory and file stores.

    Extend this from a dict-backed runtime API store. Override :meth:`_state_guard`
    and the relevant ``_persist_*`` hooks only where the backend actually differs
    (locking, durability); leave the domain policy — which lives on this class —
    untouched so both backends answer the port identically.
    """

    # Declared for type-checkers; the concrete stores construct these instance
    # dicts/lists in their own ``__init__`` (and the file store re-folds them from
    # disk on open). ``context_occupancy`` is keyed by
    # ``RuntimeContextOccupancyRecord.idempotency_key`` —
    # ``(model_call_id, attempt_ordinal)`` — the natural key the Postgres UNIQUE
    # constraint enforces, so a redelivered append folds to one row in every
    # backend and a model retry (higher ``attempt_ordinal``) stays a distinct row.
    context_occupancy: dict[tuple[str, int], RuntimeContextOccupancyRecord]
    # Canonical per-LLM-call usage rows. Read here only to prove a usage record
    # exists in-tenant before an attribution edge may point at it (never mutated
    # on the edge path — attribution is a separate immutable relation that must
    # not be able to inflate a usage total).
    model_call_usage: list[RuntimeModelCallUsageRecord]
    # Immutable usage→operation attribution edges, keyed by ``edge_id``; the
    # secondary index maps each edge's natural identity to its ``edge_id`` so a
    # retry delivery folds to one row while a distinct usage record stays distinct.
    usage_attribution_edges: dict[str, tuple[str, UsageAttributionEdge]]
    _usage_attribution_edge_ids_by_key: dict[tuple[object, ...], str]

    # ------------------------------------------------------------------
    # Backend-difference hooks (the only two axes on which file ≠ in_memory).
    # ------------------------------------------------------------------

    def _state_guard(self) -> AbstractAsyncContextManager[object]:
        """Serialization boundary for a back-office-state read/write.

        Defaults to a no-op: the in-memory store runs single-threaded under the
        event loop and holds no lock, exactly as it always has. The file store
        overrides this with its shared state lock so an append's durability
        flush cannot interleave with a concurrent append or read.
        """

        return nullcontext()

    def _persist_context_occupancy(self, record: RuntimeContextOccupancyRecord) -> None:
        """Durably record one newly-appended occupancy snapshot.

        No-op for the in-memory store (it keeps nothing on disk). The file store
        overrides this to append the row to its JSONL ledger. Called inside
        :meth:`_state_guard` and only when the row is genuinely new, so a
        redelivered append costs nothing on disk and reload has nothing to fold
        away.
        """

    def _persist_usage_attribution_edge(
        self, *, org_id: str, edge: UsageAttributionEdge
    ) -> None:
        """Durably record one newly-appended attribution edge.

        No-op for the in-memory store; the file store overrides it to append the
        ``(org_id, edge)`` envelope to its JSONL ledger. Same contract as
        :meth:`_persist_context_occupancy`: runs inside :meth:`_state_guard`, only
        for a genuinely new edge.
        """

    # ------------------------------------------------------------------
    # ContextOccupancyStorePort — read/append only (design §6). An occupancy
    # measurement describes a request already sent, so there is no correcting
    # write; the append is idempotent rather than an upsert and no mutating
    # surface exists here (see ``agent_runtime.api.ports.ContextOccupancyStorePort``).
    # ------------------------------------------------------------------

    async def append_context_occupancy(
        self,
        record: RuntimeContextOccupancyRecord,
    ) -> bool:
        """Append one snapshot keyed by its measured attempt; ``True`` if new.

        Idempotent on ``record.idempotency_key`` rather than ``record.id``: the
        same attempt redelivered under a fresh transport id must not become a
        second row. A duplicate returns ``False`` and leaves the stored row —
        the one measured first — untouched.
        """

        async with self._state_guard():
            if record.idempotency_key in self.context_occupancy:
                return False
            self.context_occupancy[record.idempotency_key] = record
            self._persist_context_occupancy(record)
            return True

    async def list_context_occupancy(
        self,
        *,
        org_id: str,
        run_id: str,
        graph_scope: RuntimeContextGraphScope | None = None,
    ) -> Sequence[RuntimeContextOccupancyRecord]:
        """Return one run's snapshots oldest-first within the tenant scope.

        Ties break on the natural key, never on ``id``: ``id`` is a random UUID
        and would make the per-turn series non-deterministic for two attempts
        measured inside the same clock tick — and would disagree with the
        Postgres backend, which orders by ``created_at, model_call_id,
        attempt_ordinal`` for the same reason. ``graph_scope=None`` returns every
        scope; the caller must keep root and subagent rows apart (§6.2).
        """

        async with self._state_guard():
            return tuple(
                sorted(
                    (
                        record
                        for record in self.context_occupancy.values()
                        if record.org_id == org_id
                        and record.run_id == run_id
                        and (graph_scope is None or record.graph_scope is graph_scope)
                    ),
                    key=lambda record: (record.created_at, *record.idempotency_key),
                )
            )

    # ------------------------------------------------------------------
    # UsageAttributionEdgeStorePort — durable immutable links between a canonical
    # usage row and the outputs it produced. Append is idempotent on the edge's
    # natural identity; the relation is never mutated (see
    # ``agent_runtime.api.ports.UsageAttributionEdgeStorePort``).
    # ------------------------------------------------------------------

    async def append_usage_attribution_edge(
        self,
        *,
        org_id: str,
        edge: UsageAttributionEdge,
    ) -> bool:
        """Append one immutable usage→operation edge; ``True`` if newly persisted.

        Idempotent on the edge's natural identity, so a retry delivery under a
        fresh ``edge_id`` folds to the stored row while a distinct usage record
        (for example a provider retry) stays a distinct edge. A missing or
        foreign usage record fails closed with ``LookupError`` rather than
        creating a dangling cross-tenant link; a reused ``edge_id`` carrying a
        different body raises ``ValueError`` rather than silently overwriting an
        immutable relation.
        """

        async with self._state_guard():
            if not any(
                record.id == edge.usage_record_id and record.org_id == org_id
                for record in self.model_call_usage
            ):
                raise LookupError(
                    "usage attribution requires an in-tenant usage record"
                )

            natural_key = (org_id, *edge.idempotency_key)
            if natural_key in self._usage_attribution_edge_ids_by_key:
                return False

            existing = self.usage_attribution_edges.get(edge.edge_id)
            if existing is not None:
                if existing == (org_id, edge):
                    return False
                raise ValueError("usage attribution edge_id already exists")

            self.usage_attribution_edges[edge.edge_id] = (org_id, edge)
            self._usage_attribution_edge_ids_by_key[natural_key] = edge.edge_id
            self._persist_usage_attribution_edge(org_id=org_id, edge=edge)
            return True

    async def list_usage_attribution_edges_for_usage_records(
        self,
        *,
        org_id: str,
        usage_record_ids: Sequence[str],
    ) -> Sequence[UsageAttributionEdge]:
        """Return the immutable edges for a known org-scoped usage-record set.

        Tenant-scoped and ordered ``(created_at, edge_id)``. An empty request set
        short-circuits before the guard — matching both backends, which never
        acquire the lock for a no-op read.
        """

        requested_ids = set(usage_record_ids)
        if not requested_ids:
            return ()
        async with self._state_guard():
            return tuple(
                sorted(
                    (
                        edge
                        for stored_org_id, edge in self.usage_attribution_edges.values()
                        if stored_org_id == org_id
                        and edge.usage_record_id in requested_ids
                    ),
                    key=lambda edge: (edge.created_at, edge.edge_id),
                )
            )


__all__ = ("MaterializedViewStoreBase",)
