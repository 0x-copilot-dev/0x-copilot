"""Persistence provider ports beyond the narrow FastAPI producer surface."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.persistence.records import (
    CheckpointRecord,
    CitationRecord,
    ContextPayloadRecord,
    DraftRecord,
    DraftStatus,
    MemoryItemRecord,
    MemoryScopeRecord,
    SourceAggregate,
    SubagentSnapshot,
)


class OptimisticConflict(RuntimeError):
    """Raised when an expected version no longer matches the latest persisted row."""

    def __init__(
        self, *, draft_id: str, expected_version: int, actual_version: int
    ) -> None:
        super().__init__(
            f"draft {draft_id} expected version {expected_version} but latest is {actual_version}"
        )
        self.draft_id = draft_id
        self.expected_version = expected_version
        self.actual_version = actual_version


@runtime_checkable
class MemoryMetadataPort(Protocol):
    """Memory scope and memory item metadata boundary."""

    def upsert_scope(self, record: MemoryScopeRecord) -> MemoryScopeRecord:
        """Create or update a memory namespace record."""

    def get_scope(
        self,
        *,
        org_id: str,
        scope_id: str,
    ) -> MemoryScopeRecord | None:
        """Return a memory scope by tenant and ID."""

    def list_items(
        self,
        *,
        org_id: str,
        scope_id: str,
        include_deleted: bool = False,
    ) -> Sequence[MemoryItemRecord]:
        """Return memory item metadata for one scope."""

    def upsert_item(self, record: MemoryItemRecord) -> MemoryItemRecord:
        """Create or update a memory item metadata row."""


@runtime_checkable
class PayloadStoragePort(Protocol):
    """Large payload storage by reference."""

    def put_payload(
        self,
        *,
        record: ContextPayloadRecord,
        content: bytes,
    ) -> ContextPayloadRecord:
        """Persist a payload blob and its metadata reference."""

    def get_payload_ref(
        self,
        *,
        org_id: str,
        payload_id: str,
    ) -> ContextPayloadRecord | None:
        """Return a payload reference without loading the blob."""

    def delete_expired_payloads(self, *, now: datetime) -> int:
        """Delete payloads whose retention window has expired."""


@runtime_checkable
class CheckpointStorePort(Protocol):
    """Runtime checkpoint metadata and blob-reference boundary."""

    def save_checkpoint_ref(self, record: CheckpointRecord) -> CheckpointRecord:
        """Persist one checkpoint metadata record."""

    def load_checkpoint_ref(
        self,
        *,
        org_id: str,
        thread_id: str,
        checkpoint_namespace: str,
        checkpoint_version: int,
    ) -> CheckpointRecord | None:
        """Load a checkpoint metadata record by unique checkpoint key."""

    def list_thread_checkpoints(
        self,
        *,
        org_id: str,
        thread_id: str,
    ) -> Sequence[CheckpointRecord]:
        """Return checkpoint refs for one runtime thread in creation order."""


@runtime_checkable
class DraftStorePort(Protocol):
    """Versioned, append-only draft artifact persistence boundary.

    Each successful write inserts one new ``DraftRecord`` row sharing the same
    ``draft_id`` with an incremented ``version``. Readers always select the
    largest ``version`` for a given ``(org_id, draft_id)``.
    """

    def insert_version(self, record: DraftRecord) -> DraftRecord:
        """Persist one new draft version. ``version`` must be ``latest+1``.

        Raises :class:`OptimisticConflict` if a row with that
        ``(org_id, draft_id, version)`` already exists.
        """

    def latest(self, *, org_id: str, draft_id: str) -> DraftRecord | None:
        """Return the most recent version of a draft, or ``None`` if missing."""

    def get_version(
        self,
        *,
        org_id: str,
        draft_id: str,
        version: int,
    ) -> DraftRecord | None:
        """Return one specific version by ``(org_id, draft_id, version)``."""

    def latest_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[DraftRecord]:
        """Return the latest version of every draft in a conversation."""

    def expect_status(
        self,
        *,
        org_id: str,
        draft_id: str,
        expected_version: int,
        expected_status: DraftStatus | None = None,
    ) -> DraftRecord:
        """Return latest if it matches expected version (and status, if given).

        Raises :class:`OptimisticConflict` on version mismatch. Raises
        :class:`KeyError` if the draft is unknown.
        """


@runtime_checkable
class SubagentStorePort(Protocol):
    """Read-only projection of SUBAGENT_* events into one row per ``task_id``.

    The store does not persist anything new — every snapshot is computed from
    the existing ``runtime_events`` rows the worker already writes. This is
    deliberate: events are the single source of truth for subagent lifecycle,
    and the dormant ``runtime_async_tasks`` table can be wired later without
    breaking this read path.
    """

    def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        running_only: bool,
        limit: int,
    ) -> Sequence[SubagentSnapshot]:
        """Return one snapshot per ``task_id`` ordered most-recent-first.

        ``running_only=True`` returns only ``queued`` / ``running`` snapshots.
        """


@runtime_checkable
class SourceStorePort(Protocol):
    """Read-only aggregate of ``runtime_citations`` rows by unique source doc."""

    def aggregate_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str | None,
        limit: int,
    ) -> Sequence[SourceAggregate]:
        """Return one aggregate per ``(source_connector, source_doc_id)``.

        Ordering is ``citation_count`` descending, then ``last_cited_at``
        descending. ``run_id`` scopes to a single run when supplied.
        """


@runtime_checkable
class CitationStorePort(Protocol):
    """Idempotent citation persistence boundary (PR 1.1).

    The :class:`agent_runtime.capabilities.citations.CitationLedger` is the
    only intended caller. Tools, provider adapters, and replay paths all
    funnel through the ledger so the (run, connector, doc_id) idempotency
    key is enforced in one place.
    """

    def insert_or_get(self, record: CitationRecord) -> CitationRecord:
        """Insert one row and return it; on idempotency conflict return existing.

        Conflict key is ``(run_id, source_connector, source_doc_id)`` —
        matching the unique index installed by migration 0015.
        """

    def list_for_run(self, *, org_id: str, run_id: str) -> Sequence[CitationRecord]:
        """Return citations for one run in ``ordinal`` order.

        Used to seal ``final_response.citations`` and to rebuild the
        registry on resume after a worker crash.
        """

    def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[CitationRecord]:
        """Return citations for a whole conversation in ``created_at`` order.

        Powers the Workspace pane Sources tab when reading archived runs.
        """
