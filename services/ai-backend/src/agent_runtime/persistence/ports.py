"""Persistence provider ports beyond the narrow FastAPI producer surface."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.persistence.records import (
    CitationRecord,
    DraftRecord,
    DraftStatus,
    ShareRecipientRecord,
    ShareRecord,
    SourceAggregate,
    SubagentSnapshot,
    ToolOrdinalBindingRecord,
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


class ConversationOrdinalConflict(RuntimeError):
    """Raised when an ordinal allocator races another writer for the same conversation.

    Two allocators trying to bind ordinals concurrently against the same
    ``(conversation_id, tool_call_id)`` row will see the unique constraint
    catch the loser. The losing caller catches this exception, reloads its
    in-memory state from the store, and returns the canonical ordinal that
    the winner persisted. The exception is not user-facing — it lives
    inside the allocator's retry loop.
    """

    def __init__(
        self,
        *,
        conversation_id: str,
        tool_call_id: str,
        attempted_ordinal: int,
        existing_ordinal: int | None = None,
    ) -> None:
        super().__init__(
            f"conversation {conversation_id} tool_call_id {tool_call_id} "
            f"attempted ordinal {attempted_ordinal}; existing ordinal "
            f"{existing_ordinal!r}"
        )
        self.conversation_id = conversation_id
        self.tool_call_id = tool_call_id
        self.attempted_ordinal = attempted_ordinal
        self.existing_ordinal = existing_ordinal


class RuntimeEventSequenceConflict(RuntimeError):
    """Raised when ``append_event`` exhausts its retry budget.

    The Postgres adapter's lock-free ``append_event`` lets concurrent
    appenders race for the next ``sequence_no``; the
    ``UNIQUE(run_id, sequence_no)`` index catches the loser. The adapter
    retries internally; only sustained contention that exceeds the retry
    budget surfaces this exception. Like :class:`ConversationOrdinalConflict`,
    it is not user-facing — callers either retry at a higher layer or
    let it propagate as a transient persistence failure.
    """

    def __init__(self, *, run_id: str, attempts: int) -> None:
        super().__init__(
            f"runtime_events sequence allocation for run {run_id} failed "
            f"after {attempts} attempts due to UNIQUE conflicts"
        )
        self.run_id = run_id
        self.attempts = attempts


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

    async def insert_many_or_get(
        self, records: Sequence[CitationRecord]
    ) -> Sequence[CitationRecord]:
        """Insert N rows and return them in input order.

        Idempotent on ``(run_id, source_connector, source_doc_id)`` per
        record — matching the unique index installed by migration 0015.
        For records that already exist, the persisted row is returned in
        place of the input. Output preserves input order so the caller's
        ordinal binding map stays consistent.
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


@runtime_checkable
class ConversationToolOrdinalStorePort(Protocol):
    """Persistent ``(conversation_ordinal ↔ tool_call_id)`` binding store (PR 04).

    Owned by the
    :class:`agent_runtime.capabilities.conversation_ordinals.ConversationOrdinalAllocator`.
    Every ordinal allocation writes a row; the allocator is restored on
    bind / approval-resume by reading the bindings back. The store
    replaces the prior positional-event-counting seeder, which produced
    different answers in the runtime allocator, the cross-turn observation
    builder, and the FE.

    The port intentionally exposes only two methods — the allocator owns
    every other concern (in-memory cache of the binding map, write-through,
    retry on conflict). Backends are responsible for translating the
    ``UNIQUE(conversation_id, tool_call_id)`` constraint into a
    :class:`ConversationOrdinalConflict` exception.
    """

    async def record(
        self,
        *,
        org_id: str,
        conversation_id: str,
        conversation_ordinal: int,
        tool_call_id: str,
        tool_name: str,
        run_id: str,
    ) -> ToolOrdinalBindingRecord:
        """Insert one binding and return the canonical row.

        Idempotent on ``(conversation_id, tool_call_id)``: a retry for
        the same ``tool_call_id`` returns the previously-persisted row
        without bumping ``conversation_ordinal``. If the same
        ``tool_call_id`` is already bound to a *different* ordinal (a
        concurrent allocator beat us with a different counter value),
        raise :class:`ConversationOrdinalConflict` so the caller can
        reload state and retry.
        """

    async def load(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[ToolOrdinalBindingRecord]:
        """Return all bindings for a conversation, sorted by ordinal asc.

        The allocator constructs its in-memory counter + binding map
        from this read at run start and after every approval resume.
        """


@runtime_checkable
class ShareStorePort(Protocol):
    """Conversation share + recipient persistence boundary (PR 6.1).

    Mutations are typed (no merge-patch on the port — the service composes
    the diff and calls the right method). Reads are scoped:

    * ``get_by_id`` / ``list_for_conversation`` are creator-side reads,
      always called within a tenant connection scoped to the share's org.
    * ``find_by_token_hash`` is the only **org-agnostic** read — the
      recipient endpoint resolves a token before we know which tenant
      it belongs to. Implementations must enforce the cross-tenant guard
      themselves (Postgres uses BYPASSRLS via the admin role; in-memory
      just returns the matching row regardless of org).
    """

    async def insert_share(
        self,
        *,
        share: ShareRecord,
        recipients: Sequence[ShareRecipientRecord],
    ) -> ShareRecord:
        """Insert one share row + zero-or-many recipient rows in one TX."""

    async def get_by_id(self, *, org_id: str, share_id: str) -> ShareRecord | None:
        """Return a share by id within the tenant scope, or ``None``."""

    async def list_for_conversation(
        self, *, org_id: str, conversation_id: str, include_revoked: bool
    ) -> Sequence[ShareRecord]:
        """Return shares created on a conversation (creator-side popover)."""

    async def find_by_token_hash(self, *, share_token_hash: str) -> ShareRecord | None:
        """Org-agnostic token lookup. The service enforces tenant + recipient gating."""

    async def list_recipients(
        self, *, org_id: str, share_id: str
    ) -> Sequence[ShareRecipientRecord]:
        """Return recipient rows for a specific-mode share (empty otherwise)."""

    async def replace_recipients(
        self,
        *,
        org_id: str,
        share_id: str,
        recipients: Sequence[ShareRecipientRecord],
    ) -> tuple[Sequence[str], Sequence[str]]:
        """Diff-replace recipients. Returns ``(added_user_ids, removed_user_ids)``."""

    async def update_share(
        self,
        *,
        org_id: str,
        share_id: str,
        sources_visible_to_viewer: bool | None = None,
        expires_at: datetime | None = None,
        clear_expires_at: bool = False,
    ) -> ShareRecord | None:
        """Apply mutable updates. Omitted fields stay untouched.

        ``clear_expires_at=True`` explicitly nulls the column (caller passes
        ``expires_at=None`` to clear). The flag distinguishes "leave alone"
        from "set to None" since both look like ``None`` on the wire.
        """

    async def revoke_share(
        self, *, org_id: str, share_id: str, now: datetime
    ) -> ShareRecord | None:
        """Stamp ``revoked_at``. Idempotent (returns the row either way)."""
