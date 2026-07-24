"""File-native runtime store: persistence + event store + queue ports on disk.

This is the desktop ``single_user_desktop`` backend. It implements the same
async port surface as ``InMemoryRuntimeApiStore`` and the Postgres adapter, but
persists to plaintext JSONL folders (Claude-Code-session style) plus a
content-addressed object store and a disposable SQLite catalog index.

Design (locked; see the PR description):

* **Single-writer, in-process.** The desktop runs one worker; subagents are
  in-process async tasks. Concurrency control is therefore small in-process
  ``asyncio.Lock``s — one per conversation for session writes, one per approval
  batch for the atomic resume flip, and one for the back-office ledgers. There
  is **no** cross-process ``flock``, no WAL commit-markers, no hash-chained
  generations, no tail-repair — those were explicitly cut.
* **Folders/JSONL are canonical.** Conversations/messages/runs/events/subagents
  live under ``workspaces/<ws>/sessions/<conv>/``; the back-office tables live
  as append-with-fold ledgers under ``state/``. The in-memory dicts this class
  holds are a *materialized view* rebuilt from those files on :meth:`open` —
  not a fallback. Every mutation writes through to disk with ``fsync`` on the
  important records.
* **The SQLite index is disposable.** It is rebuilt from the materialized dicts
  on every :meth:`open`; deleting ``index/`` loses nothing. Listing / lookup
  reads (conversations, messages, a run's events, latest sequence) are served
  through it so it is genuinely load-bearing.

Runtime wirings that hang off this store (built on the file-store PR2 seams;
all gated to the ``file`` backend, no effect on postgres/in-memory/web):

* ``self.object_store`` is the offload target — the worker's
  :class:`~runtime_worker.tool_result_offload.ToolResultOffloader` parks
  oversized tool output there via ``ContextPayloadManager`` /
  :class:`~runtime_adapters.file.offload.FileOffloadWriter`.
* Deep Agents' ``CompositeBackend`` routes ``/subagents/`` reads to
  :class:`~runtime_adapters.file.subagent_trace_backend.FileSubagentTraceBackend`
  (canonical per-subagent JSONL) and ``/large_tool_results/`` reads to
  :class:`~runtime_adapters.file.large_tool_result_backend.FileLargeToolResultBackend`
  (the object store).
* The LangGraph checkpointer is a durable ``AsyncSqliteSaver`` at
  ``index/checkpoints.sqlite3`` (see ``execution/deep_agent_builder.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any

from starlette import status

from agent_runtime.api.constants import Messages
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from copilot_audit_chain import AuditChainSigner, ChainVerificationResult
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
    ApprovalBatchStatus,
    BatchItemDecision,
    BatchOutcomeStatus,
    BatchTransitionOutcome,
    BudgetRecord,
    BudgetReservationRecord,
    BudgetStateRecord,
    ChargeOutcome,
    CompressionEventRecord,
    ModelPricingRecord,
    OutboxStatus,
    RetentionKind,
    RetentionPolicyRecord,
    RetentionSweepOutcome,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
    ToolBudgetEnforcement,
    ToolBudgetRecord,
    ToolInvocationRecord,
    UsageConversationAggregateRecord,
    UsageDailyConnectorRow,
    UsageDailyOrgRow,
    UsageDailyPurposeRow,
    UsageDailySubagentRow,
    UsageDailyUserRow,
)
from runtime_adapters.base import RuntimeAdapterHelpers, StatusTransition, _Fields
from runtime_adapters.file._audit_manifest import (
    AuditManifest,
    AuditManifestVerifier,
)
from runtime_adapters.file._capacity import FileStoreCleanupReport, QuotaGuard
from runtime_adapters.file._catalog_index import CatalogIndex
from runtime_adapters.file._deletion import (
    LegalHoldPolicy,
    ObjectReachabilityScanner,
    SessionEraser,
)
from runtime_adapters.file._health import (
    ConversationHealth,
    FileStoreHealthTracker,
    FileStoreRepairReason,
    StoreHealthReport,
)
from runtime_adapters.file._jsonl import JsonlCorruptionError, JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._telemetry import FileStoreTelemetry
from runtime_adapters.file.repair import StoreRepair
from runtime_adapters.file.export_import import (
    ConversationArchiver,
    ExportManifest,
    ImportOutcome,
)
from runtime_adapters.file._state_ledger import StateLedger
from runtime_adapters.file.object_store import FileObjectStore
from runtime_adapters.file.search import ConversationSearchHit
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ACTIVE_RUN_STATUSES,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationBucket,
    ConversationRecord,
    ConversationStatus,
    CreateConversationRequest,
    CreateRunRequest,
    HistoryDeletionResponse,
    matches_conversation_bucket,
    MessageRecord,
    MessageRole,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
    RuntimeRunCommand,
    RuntimeStageCommitCommand,
    RunHistoryEntry,
    RunRecord,
    WorkspaceDefaultsRecord,
)


class _Tables:
    """State-ledger file basenames (one JSONL per back-office table)."""

    APPROVALS = "approvals"
    TOOL_INVOCATIONS = "tool_invocations"
    APPROVAL_DECISIONS = "approval_decisions"
    APPROVAL_BATCHES = "approval_batches"
    APPROVAL_BATCH_ITEMS = "approval_batch_items"
    RUN_USAGE = "run_usage"
    MODEL_CALL_USAGE = "model_call_usage"
    PRICING = "pricing"
    USER_DAILY = "usage_daily_user"
    ORG_DAILY = "usage_daily_org"
    CONNECTOR_DAILY = "usage_daily_connector"
    SUBAGENT_DAILY = "usage_daily_subagent"
    PURPOSE_DAILY = "usage_daily_purpose"
    BUDGETS = "budgets"
    BUDGET_STATES = "budget_states"
    BUDGET_RESERVATIONS = "budget_reservations"
    RETENTION_POLICIES = "retention_policies"
    DELETION_EVIDENCE = "deletion_evidence"
    WORKSPACE_DEFAULTS = "workspace_defaults"
    AUDIT_LOG = "audit_log"
    QUEUE = "queue"


class _DeletionFields:
    """Audit-record keys for physical-deletion (bytes-gone) events."""

    EVENT_TYPE = "runtime_data_purged"
    CONVERSATIONS_DELETED = "conversations_deleted"
    MESSAGES_DELETED = "messages_deleted"
    RUNS_DELETED = "runs_deleted"
    EVENTS_DELETED = "events_deleted"
    OBJECTS_GARBAGE_COLLECTED = "objects_garbage_collected"
    SKIPPED_LEGAL_HOLD = "skipped_legal_hold"
    TRIGGER = "trigger"
    TRIGGER_USER_REQUEST = "user_history_delete"
    TRIGGER_RETENTION_SWEEP = "retention_sweep"


class _PurgeOutcome:
    """Mutable tally returned by :meth:`FileRuntimeApiStore._purge_conversations`."""

    __slots__ = (
        "conversations",
        "messages",
        "runs",
        "events",
        "objects",
        "skipped_legal_hold",
        "retained_events",
        "audit_event_id",
    )

    def __init__(self) -> None:
        self.conversations = 0
        self.messages = 0
        self.runs = 0
        self.events = 0
        self.objects = 0
        self.skipped_legal_hold = 0
        self.retained_events = 0
        self.audit_event_id: str | None = None


class FileRuntimeApiStore:
    """On-disk implementation of persistence, event store, and queue ports."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 0,
        retention_days: int = 0,
        compaction_enabled: bool = True,
    ) -> None:
        self._layout = FileStoreLayout(Path(root))
        # Boot-time bounded-growth compaction of the append-with-fold state
        # ledgers (default ON; a kill switch for the persistence path).
        self._compaction_enabled = compaction_enabled
        self._index = CatalogIndex(self._layout.index_db_path)
        # Structured logs + metrics for the store's key operations, and a live
        # record of "this chat needs repair" signals. Both are best-effort and
        # never alter the store's fail-closed read/write behaviour.
        self._telemetry = FileStoreTelemetry()
        self._health = FileStoreHealthTracker()
        # Capacity controls — both OFF by default (unlimited / keep forever) so
        # a store built without explicit limits behaves exactly as before.
        self._quota = QuotaGuard(
            self._layout,
            max_bytes=max_bytes,
            on_reject=lambda incoming: self._telemetry.quota_rejected(
                incoming_bytes=incoming
            ),
        )
        self._retention_days = max(retention_days, 0)
        self.object_store = FileObjectStore(self._layout, quota=self._quota)
        self._reachability = ObjectReachabilityScanner(self._layout)
        self._session_eraser = SessionEraser(self._layout)

        # ---- materialized view (rebuilt from disk on open) --------------
        self.conversations: dict[str, ConversationRecord] = {}
        self.messages: dict[str, MessageRecord] = {}
        self.runs: dict[str, RunRecord] = {}
        self.events_by_run: dict[str, list[RuntimeEventEnvelope]] = {}
        self.approval_requests: dict[str, ApprovalRequestRecord] = {}
        self.approval_decisions: dict[str, ApprovalDecisionRecord] = {}
        self.approval_batches: dict[str, ApprovalBatchRecord] = {}
        self.approval_batch_items: dict[str, ApprovalBatchItemRecord] = {}
        # PRD-08 D1b — per-run tool-invocation ledger (Activity meta counters),
        # keyed by ``invocation_id``; persisted so a run's counts survive a
        # desktop restart (the file store is the desktop substrate).
        self.tool_invocations: dict[str, ToolInvocationRecord] = {}
        self.run_usage: dict[str, RuntimeRunUsageRecord] = {}
        self.model_call_usage: list[RuntimeModelCallUsageRecord] = []
        self.pricing_rows: list[ModelPricingRecord] = []
        self.user_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailyUserRow
        ] = {}
        self.org_daily_usage: dict[tuple[str, str, str, str], UsageDailyOrgRow] = {}
        self.connector_daily_usage: dict[
            tuple[str, str, str, str], UsageDailyConnectorRow
        ] = {}
        self.subagent_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailySubagentRow
        ] = {}
        self.purpose_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailyPurposeRow
        ] = {}
        self.compression_events: list[CompressionEventRecord] = []
        self.budgets: dict[str, BudgetRecord] = {}
        self.budget_states: dict[tuple[str, str], BudgetStateRecord] = {}
        self.budget_reservations: dict[str, BudgetReservationRecord] = {}
        self.retention_policies: dict[str, tuple[RetentionPolicyRecord, ...]] = {}
        self.deletion_evidence: list = []
        self.workspace_defaults: dict[str, WorkspaceDefaultsRecord] = {}
        self.tool_budgets: dict[str, ToolBudgetRecord] = {
            "seed_default": ToolBudgetRecord(
                id="seed_default",
                org_id=None,
                tool_name="*",
                max_calls_per_run=6,
                enforcement=ToolBudgetEnforcement.HARD,
            ),
        }
        self.audit_log: list[tuple[str, dict[str, object]]] = []
        self._audit_chain_signer = AuditChainSigner.from_env(
            environment_env_var="RUNTIME_ENVIRONMENT"
        )
        self._audit_chain_heads_by_org: dict[str, bytes] = {}
        self._audit_chain_counts_by_org: dict[str, int] = {}
        self._conversation_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency_fingerprint: dict[
            tuple[str, str, str], tuple[str, str]
        ] = {}

        # ---- queue (outbox) ---------------------------------------------
        self.run_commands: list[RuntimeRunCommand] = []
        self.cancel_commands: list[RuntimeCancelCommand] = []
        self.approval_commands: list[RuntimeApprovalResolvedCommand] = []
        self.stage_commit_commands: list[RuntimeStageCommitCommand] = []
        self._queue_order: list[str] = []
        self._queue_payloads: dict[str, dict[str, object]] = {}
        self._queue_statuses: dict[str, OutboxStatus] = {}
        self._queue_attempts: dict[str, int] = {}
        self._queue_available_at: dict[str, datetime] = {}
        self._queue_claims: dict[str, RuntimeWorkerClaim] = {}
        # On-disk line count of the raw queue op-log, for boot compaction: the
        # queue is append-only (enqueue + a status/attempts op per claim +
        # terminal status) and never prunes completed/dead commands, so both its
        # replay and every claim_next scan are O(history) without compaction.
        self._queue_line_count = 0

        # ---- locks (in-process, single-writer) --------------------------
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._approval_batch_locks: dict[str, asyncio.Lock] = {}
        self._state_lock = asyncio.Lock()

        # ---- ledgers ----------------------------------------------------
        self._ledgers: dict[str, StateLedger] = {}

    @property
    def layout(self) -> FileStoreLayout:
        """On-disk layout resolver — used by the factory to wire satellites."""

        return self._layout

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def open(self) -> None:
        """Create the scaffold, replay canonical JSONL, rebuild the index."""

        self._layout.ensure_scaffold()
        self._ledgers = {}
        # Interior corruption still fails closed (the store refuses to open with
        # a truncated view); we record which conversation is at fault + emit a
        # corruption metric/log on the way out so a caller that catches this can
        # ask store_health() / needs_repair_ids() which chat needs repair.
        try:
            self._load_sessions_from_disk()
        except JsonlCorruptionError as exc:
            self._record_interior_corruption(exc)
            raise
        self._load_state_from_disk()
        self._load_queue_from_disk()
        # Boot is single-writer (nothing is serving yet): fold each bloated
        # state ledger back to its live set so replay cost tracks live state,
        # not total history. Crash-safe (atomic rewrite) and ratio-gated, so
        # small/new stores are untouched. Never folds session streams (their
        # monotonic sequence_no underpins stream resume) or the audit log.
        self._compact_state_ledgers()
        self._compact_queue_ledger()
        catalog_discarded = self._index.connect()
        if catalog_discarded:
            # The disposable catalog was torn and discarded — a rebuild from the
            # canonical JSONL follows immediately. Surface it as a health signal.
            self._telemetry.catalog_discarded()
            self._health.mark_catalog_rebuilt()
        with self._telemetry.index_rebuild(
            catalog_discarded=catalog_discarded, records=len(self.conversations)
        ):
            self._rebuild_index()
        self._telemetry.store_opened(
            conversations=len(self.conversations),
            catalog_rebuilt=self._health.catalog_rebuilt,
        )
        # Startup is the file store's background-maintenance seam: a desktop app
        # is not always running, so boot is the natural cadence to reap history
        # past the retention window. Gated OFF by default (retention_days == 0),
        # so this is a no-op unless the desktop profile configured a window.
        if self._retention_days > 0:
            await self.sweep_expired_conversations()

    async def close(self) -> None:
        """Release the index connection. JSONL is already durable on disk."""

        self._index.close()

    async def migrate(self) -> None:
        """No schema migration: JSONL is schemaless, the index is rebuilt."""

    # ==================================================================
    # Health / needs-repair ("this chat needs repair")
    # ==================================================================

    async def store_health(self) -> StoreHealthReport:
        """Whole-store health verdict for the "needs repair" UX.

        Runs the offline, non-raising :meth:`StoreRepair.diagnose` over the
        on-disk truth and reports every conversation that needs repair (interior
        corruption, dangling object refs, or unreadable metadata), reusing the
        repair module's diagnosis vocabulary. Also surfaces whether the
        disposable catalog was discarded/rebuilt this session. Safe to call even
        when :meth:`open` failed closed on interior corruption.
        """

        diagnosis = StoreRepair(self._layout.root).diagnose()
        unhealthy = tuple(
            ConversationHealth.from_diagnosis(conversation)
            for conversation in diagnosis.conversations
            if not conversation.healthy
        )
        return StoreHealthReport(
            healthy=all(c.healthy for c in diagnosis.conversations)
            and not self._health.catalog_rebuilt,
            catalog_rebuilt=self._health.catalog_rebuilt,
            orphan_object_count=len(diagnosis.orphan_objects),
            conversations=unhealthy,
        )

    async def conversation_health(
        self, *, org_id: str, conversation_id: str
    ) -> ConversationHealth:
        """Health verdict for one conversation (fresh, non-raising diagnosis)."""

        conversation_dir = self._layout.conversation_dir(org_id, conversation_id)
        if not conversation_dir.exists():
            return ConversationHealth.clean(conversation_id)
        diagnosis = StoreRepair(self._layout.root).diagnose_conversation(
            conversation_dir
        )
        health = ConversationHealth.from_diagnosis(diagnosis)
        # A diagnosis reads the on-disk id from metadata; keep the caller's id if
        # the metadata is unreadable so the client can still key the response.
        if health.conversation_id is None:
            return health.model_copy(update={"conversation_id": conversation_id})
        return health

    def needs_repair_ids(self) -> frozenset[str]:
        """Conversation ids flagged needs-repair on the live paths this session.

        Cheap in-memory accessor (no disk scan) for a listing/summary flag: it
        reflects the interior-corruption and catalog-discard signals observed on
        :meth:`open`. The exhaustive verdict is :meth:`store_health`.
        """

        return self._health.needs_repair_ids()

    def _record_interior_corruption(self, exc: JsonlCorruptionError) -> None:
        """Emit the corruption metric/log + flag the conversation for repair."""

        conversation_id = self._conversation_id_from_stream_path(exc.path)
        self._telemetry.interior_corruption(
            conversation_id=conversation_id, line_number=exc.line_number
        )
        if conversation_id is not None:
            self._health.mark_needs_repair(
                conversation_id, FileStoreRepairReason.INTERIOR_CORRUPTION
            )

    def _conversation_id_from_stream_path(self, path: Path) -> str | None:
        """Recover the logical conversation id from a corrupt stream's path.

        Session directories are named by a one-way hash of the conversation id,
        so the id is read back from the conversation's metadata rather than the
        directory name (subagent streams live one level deeper).
        """

        conversation_dir = path.parent
        if conversation_dir.name == self._layout.SUBAGENTS_DIR:
            conversation_dir = conversation_dir.parent
        try:
            meta = JsonlIo.read_json(conversation_dir / self._layout.CONVERSATION_META)
        except (OSError, ValueError):
            return None
        if isinstance(meta, dict):
            conversation_id = meta.get("conversation_id")
            if isinstance(conversation_id, str):
                return conversation_id
        return None

    # ----- lock helpers --------------------------------------------------

    def _conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[conversation_id] = lock
        return lock

    def _approval_batch_lock(self, batch_id: str) -> asyncio.Lock:
        lock = self._approval_batch_locks.get(batch_id)
        if lock is None:
            lock = asyncio.Lock()
            self._approval_batch_locks[batch_id] = lock
        return lock

    def _ledger(self, table: str) -> StateLedger:
        ledger = self._ledgers.get(table)
        if ledger is None:
            ledger = StateLedger(self._layout.state_path(table))
            self._ledgers[table] = ledger
        return ledger

    # ==================================================================
    # Persistence write-through helpers (session data)
    # ==================================================================

    def _persist_conversation(self, conversation: ConversationRecord) -> None:
        doc = conversation.model_dump(mode="json")
        line = JsonlIo.dumps(doc)
        JsonlIo.rewrite_json(
            self._layout.conversation_meta_path(
                conversation.org_id, conversation.conversation_id
            ),
            doc,
        )
        self._index.upsert_conversation(doc)
        self._telemetry.append_committed(kind="conversation", size=len(line))

    def _persist_message(self, message: MessageRecord) -> None:
        doc = message.model_dump(mode="json")
        line = JsonlIo.dumps(doc)
        JsonlIo.append_line(
            self._layout.messages_path(message.org_id, message.conversation_id), doc
        )
        self._index.upsert_message(doc)
        self._telemetry.append_committed(kind="message", size=len(line))

    def _persist_run(self, run: RunRecord) -> None:
        doc = run.model_dump(mode="json")
        line = JsonlIo.dumps(doc)
        JsonlIo.append_line(
            self._layout.runs_path(run.org_id, run.conversation_id), doc
        )
        self._index.upsert_run(doc)
        self._telemetry.append_committed(kind="run", size=len(line))

    def _event_stream_path(
        self, envelope: RuntimeEventEnvelope, *, org_id: str
    ) -> Path:
        """Resolve the canonical JSONL stream a single envelope is appended to.

        Main-agent events land in the run's ``events.jsonl``; a subagent event
        (``task_id`` set) lands in its own per-subagent stream. Shared by the
        single-event and batched-append paths so both route identically.
        """

        if envelope.task_id:
            return self._layout.subagent_path(
                org_id, envelope.conversation_id, envelope.task_id
            )
        return self._layout.events_path(org_id, envelope.conversation_id)

    def _persist_event(self, envelope: RuntimeEventEnvelope, *, org_id: str) -> None:
        doc = envelope.model_dump(mode="json")
        line = JsonlIo.dumps(doc)
        JsonlIo.append_line(self._event_stream_path(envelope, org_id=org_id), doc)
        index_doc = {**doc, "org_id": org_id}
        self._index.insert_events([index_doc])
        self._telemetry.append_committed(kind="event", size=len(line))

    def _persist_events_batch(
        self, envelopes: Sequence[RuntimeEventEnvelope], *, org_id: str
    ) -> None:
        """Durably persist a whole event batch as one fsynced write per stream.

        Every event in a batch shares one run (hence one conversation); in
        practice they also share one target stream — coalesced ``MODEL_DELTA``
        batches are all main-stream (``task_id is None``) — so this collapses to
        a single ``open+write+fsync``. Grouping by stream only matters if a
        producer ever mixes per-subagent ``task_id`` events into one batch, and
        even then each stream is written all-or-nothing. The disposable catalog
        index is updated in one commit *after* the durable JSONL write (JSONL is
        canonical; a lost/torn index is rebuilt from it on reopen).
        """

        by_path: dict[Path, list[dict[str, Any]]] = {}
        index_docs: list[dict[str, Any]] = []
        sizes: list[int] = []
        for envelope in envelopes:
            doc = envelope.model_dump(mode="json")
            path = self._event_stream_path(envelope, org_id=org_id)
            by_path.setdefault(path, []).append(doc)
            index_docs.append({**doc, "org_id": org_id})
            sizes.append(len(JsonlIo.dumps(doc)))
        # Durable commit: one fsynced append per target stream. This is the
        # crash boundary — nothing in-memory is mutated until it returns.
        for path, docs in by_path.items():
            JsonlIo.append_lines(path, docs)
        self._index.insert_events(index_docs)
        for size in sizes:
            self._telemetry.append_committed(kind="event", size=size)

    # ==================================================================
    # Replay from disk (open)
    # ==================================================================

    def _load_sessions_from_disk(self) -> None:
        sessions_root = self._layout.workspaces_dir
        if not sessions_root.exists():
            return
        for workspace_dir in sessions_root.iterdir():
            sessions_dir = workspace_dir / "sessions"
            if not sessions_dir.is_dir():
                continue
            for conversation_dir in sessions_dir.iterdir():
                if conversation_dir.is_dir():
                    self._load_one_conversation(conversation_dir)
        self._rebuild_idempotency_maps()

    def _load_one_conversation(self, conversation_dir: Path) -> None:
        meta = JsonlIo.read_json(conversation_dir / self._layout.CONVERSATION_META)
        if meta is not None:
            conversation = ConversationRecord.model_validate(meta)
            self.conversations[conversation.conversation_id] = conversation

        for doc in JsonlIo.iter_lines(conversation_dir / self._layout.MESSAGES_FILE):
            message = MessageRecord.model_validate(doc)
            self.messages[message.message_id] = message  # last write wins

        for doc in JsonlIo.iter_lines(conversation_dir / self._layout.RUNS_FILE):
            run = RunRecord.model_validate(doc)
            self.runs[run.run_id] = run  # last write wins (status updates)

        envelopes: list[RuntimeEventEnvelope] = []
        for doc in JsonlIo.iter_lines(conversation_dir / self._layout.EVENTS_FILE):
            envelopes.append(RuntimeEventEnvelope.model_validate(doc))
        subagents_dir = conversation_dir / self._layout.SUBAGENTS_DIR
        if subagents_dir.is_dir():
            for sub_file in subagents_dir.iterdir():
                for doc in JsonlIo.iter_lines(sub_file):
                    envelopes.append(RuntimeEventEnvelope.model_validate(doc))
        for envelope in envelopes:
            bucket = self.events_by_run.setdefault(envelope.run_id, [])
            bucket.append(envelope)
        for bucket in self.events_by_run.values():
            bucket.sort(key=lambda event: event.sequence_no)

    def _rebuild_idempotency_maps(self) -> None:
        for conversation in self.conversations.values():
            if conversation.idempotency_key is not None:
                self._conversation_idempotency[
                    (
                        conversation.org_id,
                        conversation.user_id,
                        conversation.idempotency_key,
                    )
                ] = conversation.conversation_id
        for run in self.runs.values():
            if run.idempotency_key is None:
                continue
            key = (run.org_id, run.user_id, run.idempotency_key)
            self._run_idempotency[key] = run.run_id
            user_message = self.messages.get(run.user_message_id)
            fingerprint_input = (
                user_message.content_text if user_message is not None else ""
            )
            self._run_idempotency_fingerprint[key] = (
                run.conversation_id,
                fingerprint_input or "",
            )

    def _rebuild_index(self) -> None:
        events: list[dict] = []
        for run_id, bucket in self.events_by_run.items():
            run = self.runs.get(run_id)
            org_id = run.org_id if run is not None else ""
            for envelope in bucket:
                events.append({**envelope.model_dump(mode="json"), "org_id": org_id})
        self._index.rebuild(
            conversations=(
                c.model_dump(mode="json") for c in self.conversations.values()
            ),
            messages=(m.model_dump(mode="json") for m in self.messages.values()),
            runs=(r.model_dump(mode="json") for r in self.runs.values()),
            events=events,
        )

    def _load_state_from_disk(self) -> None:
        # Fold-by-key + append-only tables.
        for op, rec in self._ledger(_Tables.APPROVALS).load_ops():
            if op == "put":
                r = ApprovalRequestRecord.model_validate(rec)
                self.approval_requests[r.approval_id] = r
            else:
                self.approval_requests.pop(rec, None)
        for op, rec in self._ledger(_Tables.TOOL_INVOCATIONS).load_ops():
            if op == "put":
                ti = ToolInvocationRecord.model_validate(rec)
                self.tool_invocations[ti.invocation_id] = ti
        for op, rec in self._ledger(_Tables.APPROVAL_DECISIONS).load_ops():
            if op == "put":
                r = ApprovalDecisionRecord.model_validate(rec)
                self.approval_decisions[r.approval_id] = r
        for op, rec in self._ledger(_Tables.APPROVAL_BATCHES).load_ops():
            if op == "put":
                r = ApprovalBatchRecord.model_validate(rec)
                self.approval_batches[r.batch_id] = r
        for op, rec in self._ledger(_Tables.APPROVAL_BATCH_ITEMS).load_ops():
            if op == "put":
                r = ApprovalBatchItemRecord.model_validate(rec)
                self.approval_batch_items[r.item_id] = r
        for op, rec in self._ledger(_Tables.RUN_USAGE).load_ops():
            if op == "put":
                r = RuntimeRunUsageRecord.model_validate(rec)
                self.run_usage[r.run_id] = r
        # model_call_usage is an append-only list keyed by id (updates re-put).
        model_calls: dict[str, RuntimeModelCallUsageRecord] = {}
        order: list[str] = []
        for rec in self._ledger(_Tables.MODEL_CALL_USAGE).load_puts():
            r = RuntimeModelCallUsageRecord.model_validate(rec)
            if r.id not in model_calls:
                order.append(r.id)
            model_calls[r.id] = r
        self.model_call_usage = [model_calls[i] for i in order]
        pricing: dict[str, ModelPricingRecord] = {}
        pricing_order: list[str] = []
        for rec in self._ledger(_Tables.PRICING).load_puts():
            r = ModelPricingRecord.model_validate(rec)
            if r.id not in pricing:
                pricing_order.append(r.id)
            pricing[r.id] = r
        self.pricing_rows = [pricing[i] for i in pricing_order]
        for rec in self._ledger(_Tables.USER_DAILY).load_puts():
            r = UsageDailyUserRow.model_validate(rec)
            self.user_daily_usage[self._user_daily_key(r)] = r
        for rec in self._ledger(_Tables.ORG_DAILY).load_puts():
            r = UsageDailyOrgRow.model_validate(rec)
            self.org_daily_usage[self._org_daily_key(r)] = r
        for rec in self._ledger(_Tables.CONNECTOR_DAILY).load_puts():
            r = UsageDailyConnectorRow.model_validate(rec)
            self.connector_daily_usage[self._connector_daily_key(r)] = r
        for rec in self._ledger(_Tables.SUBAGENT_DAILY).load_puts():
            r = UsageDailySubagentRow.model_validate(rec)
            self.subagent_daily_usage[self._subagent_daily_key(r)] = r
        for rec in self._ledger(_Tables.PURPOSE_DAILY).load_puts():
            r = UsageDailyPurposeRow.model_validate(rec)
            self.purpose_daily_usage[self._purpose_daily_key(r)] = r
        for op, rec in self._ledger(_Tables.BUDGETS).load_ops():
            if op == "put":
                r = BudgetRecord.model_validate(rec)
                self.budgets[r.id] = r
            else:
                self.budgets.pop(rec, None)
        # Whole-collection rewrite tables.
        for rec in self._ledger(_Tables.BUDGET_STATES).load_puts():
            r = BudgetStateRecord.model_validate(rec)
            self.budget_states[(r.budget_id, r.period_start.isoformat())] = r
        for rec in self._ledger(_Tables.BUDGET_RESERVATIONS).load_puts():
            r = BudgetReservationRecord.model_validate(rec)
            self.budget_reservations[r.reservation_id] = r
        retention: dict[str, list[RetentionPolicyRecord]] = {}
        for rec in self._ledger(_Tables.RETENTION_POLICIES).load_puts():
            r = RetentionPolicyRecord.model_validate(rec)
            retention.setdefault(r.org_id, []).append(r)
        self.retention_policies = {k: tuple(v) for k, v in retention.items()}
        for rec in self._ledger(_Tables.WORKSPACE_DEFAULTS).load_puts():
            r = WorkspaceDefaultsRecord.model_validate(rec)
            self.workspace_defaults[r.org_id] = r
        self.deletion_evidence = list(
            self._ledger(_Tables.DELETION_EVIDENCE).load_puts()
        )
        self._load_audit_log_from_disk()

    def _load_audit_log_from_disk(self) -> None:
        for rec in self._ledger(_Tables.AUDIT_LOG).load_puts():
            event_type = str(rec.get("event_type", ""))
            record = rec.get("record")
            if not isinstance(record, dict):
                continue
            self.audit_log.append((event_type, record))
            org_id = str(record.get(_Fields.ORG_ID, "unknown"))
            seq = int(record.get("seq") or 0)
            self._audit_chain_counts_by_org[org_id] = max(
                self._audit_chain_counts_by_org.get(org_id, 0), seq
            )
            signature_hex = record.get("signature")
            if isinstance(signature_hex, str):
                self._audit_chain_heads_by_org[org_id] = bytes.fromhex(signature_hex)

    def _load_queue_from_disk(self) -> None:
        self._queue_line_count = 0
        for line in JsonlIo.iter_lines(self._layout.state_path(_Tables.QUEUE)):
            self._queue_line_count += 1
            op = line.get("op")
            command_id = line.get("command_id")
            if not isinstance(command_id, str):
                continue
            if op == "enqueue":
                payload = line.get("payload")
                if not isinstance(payload, dict):
                    continue
                if command_id not in self._queue_payloads:
                    self._queue_order.append(command_id)
                self._queue_payloads[command_id] = payload
                self._queue_statuses[command_id] = OutboxStatus.PENDING
                self._queue_attempts[command_id] = 0
                self._queue_available_at[command_id] = self._parse_dt(
                    line.get("available_at")
                )
            elif op == "status":
                self._queue_statuses[command_id] = OutboxStatus(str(line.get("status")))
                self._queue_available_at[command_id] = self._parse_dt(
                    line.get("available_at")
                )
            elif op == "attempts":
                self._queue_attempts[command_id] = int(line.get("attempts") or 0)
        # Claims (lock ownership) are ephemeral — a restarted worker re-claims.
        self._queue_claims = {}

    # ==================================================================
    # Boot-time bounded-growth compaction (append-with-fold state ledgers)
    # ==================================================================

    # Compact a fold table only when its on-disk log has grown to at least
    # COMPACT_MIN_LINES *and* at least COMPACT_RATIO× its live set. The floor
    # stops churning small files every boot; the ratio targets genuinely
    # bloated logs (superseded re-puts / tombstoned history). Conservative.
    _COMPACT_MIN_LINES = 256
    _COMPACT_RATIO = 2

    def _compactable_tables(self) -> list[tuple[str, list]]:
        """``(table, live records in canonical order)`` for every append-with-fold
        ledger whose live set the store holds in memory after load.

        Deliberately EXCLUDES: the whole-collection *rewrite* tables
        (``budget_states``/``budget_reservations``/``retention_policies`` —
        already self-compacting); the AUDIT_LOG (append-only immutable evidence
        — folding would break the per-org ``seq``/signature chain); and the
        QUEUE (a raw, non-``StateLedger`` op log — its own compactor is a
        follow-up). Session streams (events/messages/runs) are never a state
        ledger and are never touched.

        Order matters for the ``load_puts`` list tables (``model_call_usage``,
        ``pricing``): their live list is already in first-seen order, so
        re-emitting it preserves the ordinal semantics reload rebuilds.
        """

        return [
            (_Tables.APPROVALS, list(self.approval_requests.values())),
            (_Tables.TOOL_INVOCATIONS, list(self.tool_invocations.values())),
            (_Tables.APPROVAL_DECISIONS, list(self.approval_decisions.values())),
            (_Tables.APPROVAL_BATCHES, list(self.approval_batches.values())),
            (_Tables.APPROVAL_BATCH_ITEMS, list(self.approval_batch_items.values())),
            (_Tables.RUN_USAGE, list(self.run_usage.values())),
            (_Tables.MODEL_CALL_USAGE, list(self.model_call_usage)),
            (_Tables.PRICING, list(self.pricing_rows)),
            (_Tables.USER_DAILY, list(self.user_daily_usage.values())),
            (_Tables.ORG_DAILY, list(self.org_daily_usage.values())),
            (_Tables.CONNECTOR_DAILY, list(self.connector_daily_usage.values())),
            (_Tables.SUBAGENT_DAILY, list(self.subagent_daily_usage.values())),
            (_Tables.PURPOSE_DAILY, list(self.purpose_daily_usage.values())),
            (_Tables.BUDGETS, list(self.budgets.values())),
            (_Tables.WORKSPACE_DEFAULTS, list(self.workspace_defaults.values())),
        ]

    @classmethod
    def _should_compact(cls, line_count: int, live_count: int) -> bool:
        return line_count >= cls._COMPACT_MIN_LINES and line_count >= (
            cls._COMPACT_RATIO * max(live_count, 1)
        )

    def _compact_state_ledgers(self) -> None:
        """Fold each bloated fold-table ledger back to its live set (boot only).

        Reuses :meth:`StateLedger.rewrite` (atomic temp→fsync→``os.replace``):
        a crash mid-compaction leaves the prior committed log fully intact, and
        reload re-folds to the identical live state — compaction only discards
        superseded/tombstoned history, never live data. Correct precisely
        because the folded records come from already-durable state.
        """

        if not self._compaction_enabled:
            return
        for table, live in self._compactable_tables():
            ledger = self._ledger(table)
            before = ledger.line_count
            if not self._should_compact(before, len(live)):
                continue
            # Best-effort maintenance: a failed rewrite (disk full, transient IO)
            # must never brick open(). The rewrite is atomic, so a failure leaves
            # the prior committed log fully intact — the store simply opens with
            # the un-compacted (larger) ledger, and the next boot retries.
            try:
                ledger.rewrite(record.model_dump(mode="json") for record in live)
            except Exception as exc:  # maintenance is best-effort — never break open()
                self._telemetry.state_ledger_compaction_failed(
                    table=table, reason=type(exc).__name__
                )
                continue
            self._telemetry.state_ledger_compacted(
                table=table, lines_before=before, lines_after=ledger.line_count
            )

    _QUEUE_TERMINAL = frozenset({OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER})

    def _compact_queue_ledger(self) -> None:
        """Fold the raw queue op-log down to its LIVE commands at boot.

        The queue is not a ``StateLedger`` — it is a raw op-log (an ``enqueue``
        plus a ``status``/``attempts`` op per claim, then a terminal status) and
        completed / dead-lettered commands are NEVER pruned, so both replay and
        every ``claim_next`` scan grow O(history). At boot — single-writer,
        before serving and before any claim, so no ephemeral ``CLAIMED`` state is
        in flight — rewrite the log to only the non-terminal commands and drop
        the terminal ones from the in-memory queue too, so this session's scans
        are bounded immediately. Crash-safe (atomic rewrite) and best-effort — a
        failure never breaks ``open()``.
        """

        if not self._compaction_enabled:
            return
        live = [
            command_id
            for command_id in self._queue_order
            if self._queue_statuses.get(command_id) not in self._QUEUE_TERMINAL
        ]
        before = self._queue_line_count
        if not self._should_compact(before, len(live)):
            return
        try:
            self._rewrite_queue_ledger(live)
        except Exception as exc:  # maintenance is best-effort — never break open()
            self._telemetry.state_ledger_compaction_failed(
                table=_Tables.QUEUE, reason=type(exc).__name__
            )
            return
        # Drop terminal commands from the in-memory queue too (claim_next already
        # skipped them); the live subset is preserved intact.
        live_set = set(live)
        self._queue_order = list(live)
        for mapping in (
            self._queue_payloads,
            self._queue_statuses,
            self._queue_attempts,
            self._queue_available_at,
        ):
            for command_id in [k for k in mapping if k not in live_set]:
                del mapping[command_id]
        self._telemetry.state_ledger_compacted(
            table=_Tables.QUEUE,
            lines_before=before,
            lines_after=self._queue_line_count,
        )

    def _rewrite_queue_ledger(self, live_command_ids: list[str]) -> None:
        """Atomically replace ``queue.jsonl`` with the minimal op sequence that
        reconstructs each live command's state (order, payload, status, attempts,
        available_at) — mirroring ``_load_queue_from_disk``'s fold exactly."""

        lines: list[dict[str, object]] = []
        for command_id in live_command_ids:
            available_at = self._queue_available_at[command_id].isoformat()
            lines.append(
                {
                    "op": "enqueue",
                    "command_id": command_id,
                    "payload": self._queue_payloads[command_id],
                    "available_at": available_at,
                }
            )
            status = self._queue_statuses[command_id]
            if status is not OutboxStatus.PENDING:
                lines.append(
                    {
                        "op": "status",
                        "command_id": command_id,
                        "status": status.value,
                        "available_at": available_at,
                    }
                )
            attempts = self._queue_attempts[command_id]
            if attempts:
                lines.append(
                    {"op": "attempts", "command_id": command_id, "attempts": attempts}
                )
        JsonlIo.rewrite_lines(self._layout.state_path(_Tables.QUEUE), lines)
        self._queue_line_count = len(lines)

    @staticmethod
    def _parse_dt(value: object) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    # ----- daily-usage key functions ------------------------------------

    @staticmethod
    def _user_daily_key(row: UsageDailyUserRow) -> tuple[str, str, str, str, str]:
        return (
            row.org_id,
            row.user_id,
            row.day.isoformat(),
            row.model_provider,
            row.model_name,
        )

    @staticmethod
    def _org_daily_key(row: UsageDailyOrgRow) -> tuple[str, str, str, str]:
        return (row.org_id, row.day.isoformat(), row.model_provider, row.model_name)

    @staticmethod
    def _connector_daily_key(
        row: UsageDailyConnectorRow,
    ) -> tuple[str, str, str, str]:
        return (row.org_id, row.day.isoformat(), row.connector_slug, row.model_name)

    @staticmethod
    def _subagent_daily_key(
        row: UsageDailySubagentRow,
    ) -> tuple[str, str, str, str, str]:
        return (
            row.org_id,
            row.day.isoformat(),
            row.subagent_slug,
            row.model_provider,
            row.model_name,
        )

    @staticmethod
    def _purpose_daily_key(
        row: UsageDailyPurposeRow,
    ) -> tuple[str, str, str, str, str]:
        return (
            row.org_id,
            row.day.isoformat(),
            row.purpose,
            row.model_provider,
            row.model_name,
        )

    # ==================================================================
    # PersistencePort — conversations / messages
    # ==================================================================

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        if request.idempotency_key is not None:
            key = (request.org_id, request.user_id, request.idempotency_key)
            existing_id = self._conversation_idempotency.get(key)
            if existing_id is not None:
                return self.conversations[existing_id]
        conversation = ConversationRecord(
            org_id=request.org_id,
            user_id=request.user_id,
            assistant_id=request.assistant_id,
            title=request.title,
            metadata=request.metadata,
            idempotency_key=request.idempotency_key,
            project_id=request.project_id,
        )
        async with self._conversation_lock(conversation.conversation_id):
            self.conversations[conversation.conversation_id] = conversation
            if request.idempotency_key is not None:
                self._conversation_idempotency[
                    (request.org_id, request.user_id, request.idempotency_key)
                ] = conversation.conversation_id
            self._persist_conversation(conversation)
        return conversation

    async def get_conversation(
        self, *, org_id: str, user_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            return None
        if conversation.org_id != org_id or conversation.user_id != user_id:
            return None
        return conversation

    async def get_conversation_for_org(
        self, *, org_id: str, conversation_id: str
    ) -> ConversationRecord | None:
        conversation = self.conversations.get(conversation_id)
        if conversation is None or conversation.org_id != org_id:
            return None
        return conversation

    # PRD-09 D3 — the desktop catalog is single-user and bounded; the bucket +
    # keyset path fetches a generous slice from the index and scopes/pages in
    # Python (via the shared ``matches_conversation_bucket`` predicate) rather
    # than migrating the SQLite catalog to add ``pinned`` / ``archived_at``
    # columns and a composite keyset index. The record is the source of truth for
    # both — the catalog is a rebuildable projection.
    _BUCKET_SCAN_CEILING = 10_000

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
        include_deleted: bool = False,
        project_id: str | None = None,
        bucket: ConversationBucket | None = None,
        before_updated_at: datetime | None = None,
        before_conversation_id: str | None = None,
    ) -> Sequence[ConversationRecord]:
        keyset_mode = bucket is not None or (
            before_updated_at is not None and before_conversation_id is not None
        )
        if not keyset_mode:
            docs = self._index.list_conversations(
                org_id=org_id,
                user_id=user_id,
                limit=limit,
                include_archived=include_archived,
                include_deleted=include_deleted,
                project_id=project_id,
            )
            return tuple(ConversationRecord.model_validate_json(doc) for doc in docs)

        docs = self._index.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=self._BUCKET_SCAN_CEILING,
            include_archived=True,
            include_deleted=include_deleted if bucket is None else False,
            project_id=project_id,
        )
        records = [ConversationRecord.model_validate_json(doc) for doc in docs]
        if bucket is not None:
            records = [
                record
                for record in records
                if record.deleted_at is None
                and matches_conversation_bucket(record, bucket)
            ]
        ordered = sorted(
            records,
            key=lambda record: (record.updated_at, record.conversation_id),
            reverse=True,
        )
        if before_updated_at is not None and before_conversation_id is not None:
            boundary = (before_updated_at, before_conversation_id)
            ordered = [
                record
                for record in ordered
                if (record.updated_at, record.conversation_id) < boundary
            ]
        return tuple(ordered[:limit])

    async def count_conversations_by_project(
        self,
        *,
        org_id: str,
        user_id: str,
        project_ids: Sequence[str],
    ) -> Mapping[str, int]:
        """Group the caller's non-deleted conversations by project (PRD-07)."""

        return self._index.count_conversations_by_project(
            org_id=org_id,
            user_id=user_id,
            project_ids=project_ids,
        )

    async def search_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        query: str,
        limit: int,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> Sequence[ConversationSearchHit]:
        """Full-text search this user's conversations, ranked best-first.

        Matches the query against conversation titles and redacted
        user/assistant message text held in the disposable catalog's FTS5 index
        (tool payloads and system turns are never indexed). Scoping mirrors
        :meth:`list_conversations`. Search is a desktop-only capability: when the
        catalog's FTS5 module is unavailable this returns an empty result rather
        than failing, and direct conversation reads are unaffected.
        """

        hits = self._index.search_conversations(
            org_id=org_id,
            user_id=user_id,
            query=query,
            limit=limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )
        return tuple(
            ConversationSearchHit(
                conversation=ConversationRecord.model_validate_json(doc),
                score=score,
            )
            for doc, score in hits
        )

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        before_created_at: datetime | None = None,
        before_message_id: str | None = None,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return the most-recent ``limit`` messages older than the keyset, ASC.

        Reads the authoritative in-memory ``self.messages`` map (fully loaded
        from disk on open), filters on the composite ``(created_at, message_id)``
        keyset, takes the newest ``limit`` (DESC), then reverses to ascending.
        """

        records = [
            message
            for message in self.messages.values()
            if message.org_id == org_id and message.conversation_id == conversation_id
        ]
        if not include_deleted:
            records = [message for message in records if message.deleted_at is None]
        if before_created_at is not None and before_message_id is not None:
            keyset = (before_created_at, before_message_id)
            records = [
                message
                for message in records
                if (message.created_at, message.message_id) < keyset
            ]
        newest_first = sorted(
            records,
            key=lambda message: (message.created_at, message.message_id),
            reverse=True,
        )[:limit]
        return tuple(reversed(newest_first))

    async def get_message_by_id(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        message_id: str,
    ) -> MessageRecord | None:
        """Return one live message through the loaded primary-key map."""

        message = self.messages.get(message_id)
        if (
            message is None
            or message.org_id != org_id
            or message.conversation_id != conversation_id
            or message.run_id != run_id
            or message.deleted_at is not None
        ):
            return None
        return message

    async def append_message(self, message: MessageRecord) -> MessageRecord:
        async with self._conversation_lock(message.conversation_id):
            self.messages[message.message_id] = message
            self._persist_message(message)
            conversation = self.conversations.get(message.conversation_id)
            if conversation is not None:
                updated = conversation.model_copy(
                    update={"updated_at": message.created_at}
                )
                self.conversations[message.conversation_id] = updated
                self._persist_conversation(updated)
        return message

    async def insert_forked_conversation(
        self, conversation: ConversationRecord
    ) -> ConversationRecord:
        async with self._conversation_lock(conversation.conversation_id):
            self.conversations[conversation.conversation_id] = conversation
            self._persist_conversation(conversation)
        return conversation

    async def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        scopes_patch: dict[str, tuple[str, ...] | None],
        now: datetime,
    ) -> ConversationRecord | None:
        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        merged: dict[str, tuple[str, ...] | None] = dict(
            conversation.enabled_connectors
        )
        merged.update(scopes_patch)
        updated = conversation.model_copy(
            update={
                "enabled_connectors": merged,
                "connectors_updated_at": now,
                "updated_at": now,
            }
        )
        async with self._conversation_lock(conversation_id):
            self.conversations[conversation_id] = updated
            self._persist_conversation(updated)
        return updated

    async def get_workspace_defaults(
        self, *, org_id: str
    ) -> WorkspaceDefaultsRecord | None:
        return self.workspace_defaults.get(org_id)

    async def upsert_workspace_defaults(
        self, *, record: WorkspaceDefaultsRecord
    ) -> WorkspaceDefaultsRecord:
        persisted = record.model_copy(update={"retention_days": None})
        async with self._state_lock:
            self.workspace_defaults[record.org_id] = persisted
            self._ledger(_Tables.WORKSPACE_DEFAULTS).append_put(
                persisted.model_dump(mode="json")
            )
        return persisted

    async def update_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        title: str | None,
        title_changed: bool,
        folder: str | None,
        folder_changed: bool,
        archived: bool | None,
        archived_changed: bool,
        project_id: str | None,
        project_id_changed: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        update: dict[str, object] = {"updated_at": now}
        if title_changed:
            update["title"] = title
        if folder_changed:
            update["folder"] = folder
        if project_id_changed:
            update["project_id"] = project_id
        if archived_changed:
            if archived:
                update["status"] = ConversationStatus.ARCHIVED
                update["archived_at"] = now
            else:
                update["status"] = ConversationStatus.ACTIVE
                update["archived_at"] = None
        updated = conversation.model_copy(update=update)
        async with self._conversation_lock(conversation_id):
            self.conversations[conversation_id] = updated
            self._persist_conversation(updated)
        return updated

    async def soft_delete_conversation(
        self, *, org_id: str, user_id: str, conversation_id: str, now: datetime
    ) -> ConversationRecord | None:
        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        if conversation.deleted_at is not None:
            return conversation
        updated = conversation.model_copy(update={"deleted_at": now, "updated_at": now})
        async with self._conversation_lock(conversation_id):
            self.conversations[conversation_id] = updated
            self._persist_conversation(updated)
        return updated

    async def restore_conversation(
        self, *, org_id: str, user_id: str, conversation_id: str, now: datetime
    ) -> ConversationRecord | None:
        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        if conversation.deleted_at is None:
            return conversation
        updated = conversation.model_copy(
            update={"deleted_at": None, "updated_at": now}
        )
        async with self._conversation_lock(conversation_id):
            self.conversations[conversation_id] = updated
            self._persist_conversation(updated)
        return updated

    async def set_conversation_pinned(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        pinned: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        """Set the first-class ``pinned`` flag and persist it (PRD-H.4).

        Idempotent: setting the flag to its current value is a no-op that
        skips both the ``updated_at`` bump and the disk write, so a
        redundant pin never reshuffles the newest-first sidebar order.
        """

        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        if conversation.pinned == pinned:
            return conversation
        updated = conversation.model_copy(update={"pinned": pinned, "updated_at": now})
        async with self._conversation_lock(conversation_id):
            self.conversations[conversation_id] = updated
            self._persist_conversation(updated)
        return updated

    async def get_latest_message_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        prefer_roles: tuple[str, ...] = ("assistant",),
    ) -> MessageRecord | None:
        """Return the newest non-deleted message for the Chats-list preview (PRD-H.4).

        Prefers the newest message whose role is in ``prefer_roles`` (PRD-09 D6),
        falling back to the newest of any role when none matches.
        """

        candidates = [
            message
            for message in self.messages.values()
            if message.org_id == org_id
            and message.conversation_id == conversation_id
            and message.deleted_at is None
        ]
        if not candidates:
            return None
        preferred = [
            message for message in candidates if str(message.role) in prefer_roles
        ]
        pool = preferred if preferred else candidates
        return max(pool, key=lambda message: message.created_at)

    async def get_latest_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        """Return the newest run for a conversation regardless of status (PRD-H.4)."""

        candidates = [
            run
            for run in self.runs.values()
            if run.org_id == org_id and run.conversation_id == conversation_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda run: run.created_at)

    async def list_runs_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
    ) -> tuple[RunRecord, ...]:
        """Return the conversation's runs newest-first (any status), capped at ``limit``."""

        candidates = [
            run
            for run in self.runs.values()
            if run.org_id == org_id and run.conversation_id == conversation_id
        ]
        candidates.sort(key=lambda run: run.created_at, reverse=True)
        return tuple(candidates[: max(0, limit)])

    async def list_runs_for_org(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        before_created_at: datetime | None = None,
        before_run_id: str | None = None,
    ) -> tuple[RunHistoryEntry, ...]:
        """Return the caller's runs newest-first across conversations (PRD-05).

        Same in-memory-scan shape the other run queries use — both this store and
        the in-memory one hydrate every run into a process dict, so the scan is
        asymptotically identical. Joins ``self.conversations`` for the title and
        excludes runs whose conversation is soft-deleted or absent (the file
        store's ``delete_user_history`` physically purges, so purged runs never
        reach this scan; ``soft_delete_conversation`` keeps the row with a
        ``deleted_at`` stamp, which the join predicate below hides).
        """

        entries: list[RunHistoryEntry] = []
        for run in self.runs.values():
            if run.org_id != org_id or run.user_id != user_id:
                continue
            conversation = self.conversations.get(run.conversation_id)
            if conversation is None or conversation.deleted_at is not None:
                continue
            entries.append(
                RunHistoryEntry(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    conversation_title=conversation.title,
                    status=run.status,
                    model_name=run.model_name,
                    created_at=run.created_at,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    cancelled_at=run.cancelled_at,
                )
            )
        entries.sort(key=lambda e: (e.created_at, e.run_id), reverse=True)
        if before_created_at is not None and before_run_id is not None:
            keyset = (before_created_at, before_run_id)
            entries = [e for e in entries if (e.created_at, e.run_id) < keyset]
        return tuple(entries[: max(0, limit)])

    # ==================================================================
    # PersistencePort — tool-invocation ledger (PRD-08 D1b)
    # ==================================================================

    async def record_tool_invocation(self, record: ToolInvocationRecord) -> None:
        """Upsert a tool-invocation row keyed by ``invocation_id`` (persisted)."""

        async with self._state_lock:
            self.tool_invocations[record.invocation_id] = record
            self._ledger(_Tables.TOOL_INVOCATIONS).append_put(
                record.model_dump(mode="json")
            )

    async def count_tool_invocations_for_runs(
        self, *, org_id: str, run_ids: Sequence[str]
    ) -> Mapping[str, tuple[int, int]]:
        """Return ``run_id → (step_count, connector_count)`` (runs with rows only)."""

        wanted = set(run_ids)
        steps: dict[str, int] = {}
        connectors: dict[str, set[str]] = {}
        for record in self.tool_invocations.values():
            if record.org_id != org_id or record.run_id not in wanted:
                continue
            steps[record.run_id] = steps.get(record.run_id, 0) + 1
            if record.connector_slug is not None:
                connectors.setdefault(record.run_id, set()).add(record.connector_slug)
        return {
            run_id: (count, len(connectors.get(run_id, set())))
            for run_id, count in steps.items()
        }

    async def count_pending_approvals_for_runs(
        self, *, org_id: str, run_ids: Sequence[str]
    ) -> Mapping[str, int]:
        """Return ``run_id → pending-approval count`` (runs with pending only)."""

        from runtime_api.schemas.common import ApprovalStatus  # local: avoid cycle

        wanted = set(run_ids)
        pending: dict[str, int] = {}
        for request in self.approval_requests.values():
            if (
                request.org_id != org_id
                or request.run_id not in wanted
                or request.status is not ApprovalStatus.PENDING
            ):
                continue
            pending[request.run_id] = pending.get(request.run_id, 0) + 1
        return pending

    # ==================================================================
    # PersistencePort — runs
    # ==================================================================

    async def create_run_with_user_message(
        self, *, request: CreateRunRequest, conversation: ConversationRecord
    ) -> tuple[RunRecord, MessageRecord, bool]:
        context = request.runtime_context
        if context is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is required.",
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )
        if request.idempotency_key is not None:
            key = (context.org_id, context.user_id, request.idempotency_key)
            existing_run_id = self._run_idempotency.get(key)
            if existing_run_id is not None:
                self._ensure_run_idempotency_match(key=key, request=request)
                run = self.runs[existing_run_id]
                return run, self.messages[run.user_message_id], False

        user_message = RuntimeAdapterHelpers.message_for_run_request(
            request=request,
            conversation=conversation,
            get_message=lambda mid: self.messages.get(mid),
            get_latest_message_id=self._latest_message_id,
            find_latest_assistant_for_run=self._find_latest_assistant_for_run,
            run_id_for_message=context.run_id,
        )
        run = RunRecord(
            run_id=context.run_id,
            conversation_id=conversation.conversation_id,
            org_id=context.org_id,
            user_id=context.user_id,
            user_message_id=user_message.message_id,
            idempotency_key=request.idempotency_key,
            trace_id=context.trace_id,
            model_provider=context.model_profile.provider,
            model_name=context.model_profile.model_name,
            runtime_context=context,
            request_options=request.request_options,
        )
        async with self._conversation_lock(conversation.conversation_id):
            message_is_new = user_message.message_id not in self.messages
            if message_is_new:
                self.messages[user_message.message_id] = user_message
                self._persist_message(user_message)
            self.runs[run.run_id] = run
            self._persist_run(run)
            updated_conversation = conversation.model_copy(
                update={"updated_at": run.created_at}
            )
            self.conversations[conversation.conversation_id] = updated_conversation
            self._persist_conversation(updated_conversation)
            self.events_by_run.setdefault(run.run_id, [])
            if request.idempotency_key is not None:
                key = (context.org_id, context.user_id, request.idempotency_key)
                self._run_idempotency[key] = run.run_id
                self._run_idempotency_fingerprint[key] = (
                    request.conversation_id,
                    request.user_input,
                )
        return run, user_message, True

    def _latest_message_id(self, org_id: str, conversation_id: str) -> str | None:
        candidates = [
            message
            for message in self.messages.values()
            if message.org_id == org_id
            and message.conversation_id == conversation_id
            and message.deleted_at is None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda m: m.created_at).message_id

    def _find_latest_assistant_for_run(
        self, org_id: str, conversation_id: str, run_id: str
    ) -> str | None:
        matches = [
            message
            for message in self.messages.values()
            if message.org_id == org_id
            and message.conversation_id == conversation_id
            and message.run_id == run_id
            and message.role == MessageRole.ASSISTANT
            and message.deleted_at is None
        ]
        if not matches:
            return None
        return max(matches, key=lambda m: m.created_at).message_id

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        run = self.runs.get(run_id)
        if run is None or run.org_id != org_id:
            return None
        return run

    async def get_active_run_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> RunRecord | None:
        candidates = [
            run
            for run in self.runs.values()
            if run.org_id == org_id
            and run.conversation_id == conversation_id
            and run.status in ACTIVE_RUN_STATUSES
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda run: run.created_at)

    async def count_active_runs(self, *, org_id: str, user_id: str) -> int:
        """Count the caller's in-flight runs for the rail Run badge (PRD-12 D1).

        In-process scan over ``self.runs`` filtered on ``(org_id, user_id)`` and
        ``ACTIVE_RUN_STATUSES``, excluding runs whose conversation is soft-deleted
        or absent — the same deleted-conversation predicate ``list_runs_for_org``
        uses so a purged / soft-deleted conversation never keeps the badge lit.
        Counts EVERY in-flight run (two in one conversation count as 2).
        """

        count = 0
        for run in self.runs.values():
            if run.org_id != org_id or run.user_id != user_id:
                continue
            if run.status not in ACTIVE_RUN_STATUSES:
                continue
            conversation = self.conversations.get(run.conversation_id)
            if conversation is None or conversation.deleted_at is not None:
                continue
            count += 1
        return count

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        run = self.runs[run_id]
        timestamps = StatusTransition.timestamp_updates(
            status, already_started=run.started_at is not None
        )
        updated = run.model_copy(update={"status": status, **timestamps})
        async with self._conversation_lock(updated.conversation_id):
            self.runs[run_id] = updated
            self._persist_run(updated)
            # PRD-09 D4 — bump the parent conversation's ``updated_at`` in the
            # same locked section so a status-only transition moves the row the
            # Chats live tail watches.
            conversation = self.conversations.get(updated.conversation_id)
            if conversation is not None:
                bumped = conversation.model_copy(
                    update={"updated_at": datetime.now(timezone.utc)}
                )
                self.conversations[updated.conversation_id] = bumped
                self._persist_conversation(bumped)
        return updated

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        current = self.runs[run_id]
        existing = current.latest_sequence_no
        if existing is not None and existing >= latest_sequence_no:
            return current
        updated = current.model_copy(update={"latest_sequence_no": latest_sequence_no})
        self.runs[run_id] = updated
        self._persist_run(updated)
        return updated

    def _ensure_run_idempotency_match(
        self, *, key: tuple[str, str, str], request: CreateRunRequest
    ) -> None:
        fingerprint = self._run_idempotency_fingerprint.get(key)
        if fingerprint is not None and fingerprint != (
            request.conversation_id,
            request.user_input,
        ):
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.IDEMPOTENCY_CONFLICT,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
                correlation_id=request.runtime_context.trace_id,
            )

    # ==================================================================
    # EventStorePort
    # ==================================================================

    @staticmethod
    def _envelope_for(
        event: RuntimeEventDraft, *, sequence_no: int
    ) -> RuntimeEventEnvelope:
        """Seal a draft into an envelope at ``sequence_no``.

        The single source of the draft→envelope projection (activity-kind
        fallback included), shared by the single-event and batched-append paths
        so a batch's envelopes are byte-identical to N sequential
        :meth:`append_event` calls.
        """

        envelope_kwargs: dict[str, object] = {}
        if event.event_id is not None:
            envelope_kwargs["event_id"] = event.event_id
        if event.created_at is not None:
            envelope_kwargs["created_at"] = event.created_at
        return RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=sequence_no,
            source=event.source,
            event_type=event.event_type,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_task_id=event.parent_task_id,
            task_id=event.task_id,
            subagent_id=event.subagent_id,
            display_title=event.display_title,
            summary=event.summary,
            status=event.status,
            activity_kind=event.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=event.event_type,
                source=event.source,
            ),
            visibility=event.visibility,
            redaction_state=event.redaction_state,
            presentation=event.presentation,
            payload=event.payload,
            metadata=event.metadata,
            **envelope_kwargs,
        )

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        async with self._conversation_lock(event.conversation_id):
            events = self.events_by_run.setdefault(event.run_id, [])
            if event.event_id is not None:
                existing = next(
                    (item for item in events if item.event_id == event.event_id),
                    None,
                )
                if existing is not None:
                    if event.matches_envelope(existing):
                        return existing
                    raise RuntimeEventIdempotencyConflict(
                        run_id=event.run_id,
                        event_id=event.event_id,
                    )
            envelope = self._envelope_for(event, sequence_no=len(events) + 1)
            events.append(envelope)
            self._persist_event(envelope, org_id=event.org_id)
            if event.run_id in self.runs:
                await self.set_run_latest_sequence(
                    run_id=event.run_id, latest_sequence_no=envelope.sequence_no
                )
        return envelope

    async def append_events_batch(
        self, events: Sequence[RuntimeEventDraft]
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append a whole event batch crash-atomically (all-or-nothing).

        Parity with the Postgres adapter's single-transaction batch: the run's
        monotonic ``sequence_no``s are assigned for the entire batch up front,
        then every line is persisted through ONE fsynced append per target
        stream *before* the in-memory materialized view and catalog index are
        touched. A crash during the durable write therefore leaves either all of
        the batch's events on disk or none — never a fsynced proper prefix, the
        "partial coalesced flush" residue the previous per-event loop produced.
        On success the in-memory ``events_by_run`` bucket, the index, and the
        run's ``latest_sequence_no`` are advanced exactly as N sequential
        :meth:`append_event` calls would leave them.
        """

        if not events:
            return ()
        if any(event.event_id is not None for event in events):
            raise ValueError(
                "stable event ids require append_event; batch append is reserved "
                "for newly allocated stream events"
            )
        run_ids = {event.run_id for event in events}
        if len(run_ids) > 1:
            raise ValueError(
                "append_events_batch requires all events to share one run_id; "
                f"saw {len(run_ids)}."
            )
        first = events[0]
        async with self._conversation_lock(first.conversation_id):
            bucket = self.events_by_run.setdefault(first.run_id, [])
            base = len(bucket)
            envelopes = [
                self._envelope_for(event, sequence_no=base + offset)
                for offset, event in enumerate(events, start=1)
            ]
            # Durable commit first; only then advance the in-memory view so a
            # failed/interrupted write leaves no phantom events behind.
            self._persist_events_batch(envelopes, org_id=first.org_id)
            bucket.extend(envelopes)
            if first.run_id in self.runs:
                await self.set_run_latest_sequence(
                    run_id=first.run_id,
                    latest_sequence_no=envelopes[-1].sequence_no,
                )
        return tuple(envelopes)

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[RuntimeEventEnvelope]:
        run = await self.get_run(org_id=org_id, run_id=run_id)
        if run is None:
            return ()
        docs = self._index.list_events_after(
            run_id=run_id, after_sequence=after_sequence
        )
        return tuple(RuntimeEventEnvelope.model_validate_json(doc) for doc in docs)

    async def get_latest_sequence(self, *, run_id: str) -> int:
        return self._index.latest_sequence(run_id=run_id)

    # ==================================================================
    # PersistencePort — approvals
    # ==================================================================

    async def record_approval_decision(
        self, *, record: ApprovalDecisionRecord
    ) -> ApprovalDecisionRecord:
        async with self._state_lock:
            self.approval_decisions[record.approval_id] = record
            self._ledger(_Tables.APPROVAL_DECISIONS).append_put(
                record.model_dump(mode="json")
            )
            request = self.approval_requests[record.approval_id]
            merged_metadata = dict(request.metadata)
            merged_metadata["decided_at"] = record.decided_at.isoformat()
            updated = request.model_copy(
                update={"status": record.status, "metadata": merged_metadata}
            )
            self.approval_requests[record.approval_id] = updated
            self._ledger(_Tables.APPROVALS).append_put(updated.model_dump(mode="json"))
        return record

    async def create_approval_request(
        self, *, record: ApprovalRequestRecord
    ) -> ApprovalRequestRecord:
        async with self._state_lock:
            existing = self.approval_requests.get(record.approval_id)
            if existing is not None:
                return existing
            normalized_metadata = dict(record.metadata)
            normalized_metadata[_Fields.RISK_LEVEL] = (
                RuntimeAdapterHelpers.normalize_risk_class(record.metadata)
            )
            record = record.model_copy(update={"metadata": normalized_metadata})
            self.approval_requests[record.approval_id] = record
            self._ledger(_Tables.APPROVALS).append_put(record.model_dump(mode="json"))
        return record

    async def forward_approval_request(
        self,
        *,
        parent_approval_id: str,
        org_id: str,
        decided_by_user_id: str,
        forwarded_to_user_id: str,
        decision_reason: str | None,
        child: ApprovalRequestRecord,
        now: datetime,
    ) -> tuple[ApprovalRequestRecord, ApprovalRequestRecord]:
        from runtime_api.schemas.common import ApprovalStatus

        async with self._state_lock:
            parent = self.approval_requests.get(parent_approval_id)
            if parent is None or parent.org_id != org_id:
                raise KeyError(parent_approval_id)
            if parent.status is not ApprovalStatus.PENDING:
                existing_child = self.approval_requests.get(child.approval_id)
                if (
                    parent.status is ApprovalStatus.FORWARDED
                    and parent.forwarded_to_user_id == forwarded_to_user_id
                    and existing_child is not None
                    and existing_child.chain_parent_approval_id == parent_approval_id
                ):
                    return parent, existing_child
                raise RuntimeError("approval_forward_parent_no_longer_pending")
            existing_child = self.approval_requests.get(child.approval_id)
            if existing_child is not None:
                return parent, existing_child
            updated_parent = parent.model_copy(
                update={
                    "status": ApprovalStatus.FORWARDED,
                    "forwarded_to_user_id": forwarded_to_user_id,
                    "forwarded_at": now,
                }
            )
            self.approval_requests[parent_approval_id] = updated_parent
            normalized_metadata = dict(child.metadata)
            normalized_metadata[_Fields.RISK_LEVEL] = (
                RuntimeAdapterHelpers.normalize_risk_class(child.metadata)
            )
            normalized_child = child.model_copy(
                update={
                    "metadata": normalized_metadata,
                    "chain_parent_approval_id": parent_approval_id,
                    "chain_depth": child.chain_depth or (parent.chain_depth + 1),
                }
            )
            self.approval_requests[normalized_child.approval_id] = normalized_child
            parent_decision = ApprovalDecisionRecord(
                approval_id=parent_approval_id,
                run_id=updated_parent.run_id,
                conversation_id=updated_parent.conversation_id,
                org_id=updated_parent.org_id,
                user_id=updated_parent.user_id,
                status=ApprovalStatus.FORWARDED,
                decided_by_user_id=decided_by_user_id,
                reason=decision_reason,
                decided_at=now,
                forwarded_to_user_id=forwarded_to_user_id,
            )
            self.approval_decisions[parent_approval_id] = parent_decision
            approvals = self._ledger(_Tables.APPROVALS)
            approvals.append_put(updated_parent.model_dump(mode="json"))
            approvals.append_put(normalized_child.model_dump(mode="json"))
            self._ledger(_Tables.APPROVAL_DECISIONS).append_put(
                parent_decision.model_dump(mode="json")
            )
        return updated_parent, normalized_child

    async def get_approval_request(
        self, *, org_id: str, approval_id: str
    ) -> ApprovalRequestRecord | None:
        approval = self.approval_requests.get(approval_id)
        if approval is None or approval.org_id != org_id:
            return None
        return approval

    async def insert_approval_batch(
        self, *, spec: ApprovalBatchSpec
    ) -> ApprovalBatchRecord:
        async with self._state_lock:
            existing = self.approval_batches.get(spec.batch.batch_id)
            if existing is not None:
                return existing
            self.approval_batches[spec.batch.batch_id] = spec.batch
            self._ledger(_Tables.APPROVAL_BATCHES).append_put(
                spec.batch.model_dump(mode="json")
            )
            items_ledger = self._ledger(_Tables.APPROVAL_BATCH_ITEMS)
            for item in spec.items:
                self.approval_batch_items[item.item_id] = item
                items_ledger.append_put(item.model_dump(mode="json"))
        return spec.batch

    async def get_approval_batch(
        self, *, org_id: str, batch_id: str
    ) -> ApprovalBatchRecord | None:
        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return None
        return batch

    async def get_approval_batch_item(
        self, *, org_id: str, item_id: str
    ) -> ApprovalBatchItemRecord | None:
        item = self.approval_batch_items.get(item_id)
        if item is None:
            return None
        batch = self.approval_batches.get(item.batch_id)
        if batch is None or batch.org_id != org_id:
            return None
        return item

    async def list_items_for_batch(
        self, *, org_id: str, batch_id: str
    ) -> tuple[ApprovalBatchItemRecord, ...]:
        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return ()
        items = [
            item
            for item in self.approval_batch_items.values()
            if item.batch_id == batch_id
        ]
        items.sort(key=lambda record: record.index)
        return tuple(items)

    async def record_item_decision_and_maybe_lock_batch(
        self, *, org_id: str, item_id: str, decision: ApprovalDecision
    ) -> BatchTransitionOutcome:
        item = self.approval_batch_items.get(item_id)
        if item is None:
            return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
        batch_id = item.batch_id
        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)

        async with self._approval_batch_lock(batch_id):
            current_batch = self.approval_batches.get(batch_id)
            if current_batch is None:
                return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
            if current_batch.status is not ApprovalBatchStatus.PENDING:
                return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
            current_item = self.approval_batch_items[item_id]
            batch_decision = BatchItemDecision(decision.value)
            updated_item = current_item.model_copy(update={"decision": batch_decision})
            async with self._state_lock:
                self.approval_batch_items[item_id] = updated_item
                self._ledger(_Tables.APPROVAL_BATCH_ITEMS).append_put(
                    updated_item.model_dump(mode="json")
                )
                siblings = [
                    row
                    for row in self.approval_batch_items.values()
                    if row.batch_id == batch_id
                ]
                siblings.sort(key=lambda record: record.index)
                if any(sibling.decision is None for sibling in siblings):
                    return BatchTransitionOutcome(
                        status=BatchOutcomeStatus.BATCH_INCOMPLETE
                    )
                resuming = current_batch.model_copy(
                    update={"status": ApprovalBatchStatus.RESUMING}
                )
                self.approval_batches[batch_id] = resuming
                self._ledger(_Tables.APPROVAL_BATCHES).append_put(
                    resuming.model_dump(mode="json")
                )
            return BatchTransitionOutcome(
                status=BatchOutcomeStatus.READY_TO_RESUME,
                batch=resuming,
                items=tuple(siblings),
            )

    async def mark_approval_batch_resolved(self, *, org_id: str, batch_id: str) -> None:
        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return
        if batch.status in {ApprovalBatchStatus.RESOLVED, ApprovalBatchStatus.EXPIRED}:
            return
        updated = batch.model_copy(update={"status": ApprovalBatchStatus.RESOLVED})
        async with self._state_lock:
            self.approval_batches[batch_id] = updated
            self._ledger(_Tables.APPROVAL_BATCHES).append_put(
                updated.model_dump(mode="json")
            )

    async def list_assigned_approvals(
        self,
        *,
        org_id: str,
        requested_by_user_id: str,
        status: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> Sequence[ApprovalRequestRecord]:
        rows: list[ApprovalRequestRecord] = []
        for approval in self.approval_requests.values():
            if approval.org_id != org_id:
                continue
            if approval.user_id != requested_by_user_id:
                continue
            if approval.status.value != status:
                continue
            if cursor is not None:
                cursor_at, cursor_id = cursor
                if (approval.created_at, approval.approval_id) >= (
                    cursor_at,
                    cursor_id,
                ):
                    continue
            rows.append(approval)
        rows.sort(
            key=lambda record: (record.created_at, record.approval_id), reverse=True
        )
        return tuple(rows[:limit])

    async def list_pending_expired_approvals(
        self, *, now: datetime, limit: int
    ) -> Sequence[ApprovalRequestRecord]:
        from runtime_api.schemas.common import ApprovalStatus

        rows = [
            approval
            for approval in self.approval_requests.values()
            if approval.status is ApprovalStatus.PENDING
            and approval.expires_at is not None
            and approval.expires_at <= now
        ]
        rows.sort(key=lambda record: (record.expires_at or now, record.approval_id))
        return tuple(rows[:limit])

    async def list_pending_approvals_for_membership_audit(
        self, *, limit: int
    ) -> Sequence[ApprovalRequestRecord]:
        from runtime_api.schemas.common import ApprovalStatus

        rows = [
            approval
            for approval in self.approval_requests.values()
            if approval.status is ApprovalStatus.PENDING
        ]
        rows.sort(key=lambda record: (record.created_at, record.approval_id))
        return tuple(rows[:limit])

    async def seed_approval_request(
        self, record: ApprovalRequestRecord
    ) -> ApprovalRequestRecord:
        async with self._state_lock:
            self.approval_requests[record.approval_id] = record
            self._ledger(_Tables.APPROVALS).append_put(record.model_dump(mode="json"))
        return record

    # ==================================================================
    # PersistencePort — audit + history deletion
    # ==================================================================

    async def write_audit_log(
        self, *, event_type: str, record: dict[str, object]
    ) -> None:
        async with self._state_lock:
            signed = self._sign_audit_record(event_type=event_type, record=record)
            self.audit_log.append((event_type, signed))
            self._ledger(_Tables.AUDIT_LOG).append_put(
                {"event_type": event_type, "record": signed}
            )

    def _sign_audit_record(
        self, *, event_type: str, record: dict[str, object]
    ) -> dict[str, object]:
        org_id = str(record.get(_Fields.ORG_ID, "unknown"))
        prev_hash = self._audit_chain_heads_by_org.get(org_id)
        payload = self._audit_signing_payload(event_type=event_type, record=record)
        sig = self._audit_chain_signer.sign(prev_hash=prev_hash, payload=payload)
        seq = self._audit_chain_counts_by_org.get(org_id, 0) + 1
        self._audit_chain_counts_by_org[org_id] = seq
        self._audit_chain_heads_by_org[org_id] = sig.signature
        return {
            **record,
            "seq": seq,
            "prev_hash": prev_hash.hex() if prev_hash else None,
            "signature": sig.signature.hex(),
            "key_version": sig.key_version,
        }

    @staticmethod
    def _audit_signing_payload(
        *, event_type: str, record: dict[str, object]
    ) -> dict[str, Any]:
        # Single source of truth shared with the independent verifier, so the
        # bytes recomputed at verify time cannot drift from what was signed.
        return AuditManifest.signing_payload(event_type=event_type, record=record)

    def verify_audit_log(self, *, org_id: str | None = None) -> ChainVerificationResult:
        """Independently verify the signed manifest chain; detect any tampering.

        Reconstructs each row's signable payload and re-checks the HMAC chain via
        :class:`~runtime_adapters.file._audit_manifest.AuditManifestVerifier`. A
        flipped field, a reordered row, or a dropped row surfaces as ``ok=False``
        with the offending ``broken_at_seq``. Optionally scoped to one ``org_id``
        (the chain is per-org). Callable for a health check or a SIEM-side audit.
        """

        entries = [
            (event_type, dict(record))
            for event_type, record in self.audit_log
            if org_id is None or record.get(_Fields.ORG_ID) == org_id
        ]
        return AuditManifestVerifier(self._audit_chain_signer).verify(entries)

    async def list_audit_log_for_export(
        self, *, after_id: str | None, limit: int
    ) -> Sequence[dict]:
        # Carry ``event_type`` on each exported row so an external SIEM-side
        # verifier can recompute the HMAC independently (it is folded into the
        # signed payload as ``__event_type__`` but is not otherwise in the row).
        rows: list[dict] = [
            {"event_type": event_type, **record}
            for event_type, record in self.audit_log
        ]
        if after_id is not None:
            for index, row in enumerate(rows):
                if row.get("signature") == after_id:
                    rows = rows[index + 1 :]
                    break
            else:
                rows = []
        return tuple(rows[:limit])

    async def list_audit_log_events(
        self,
        *,
        org_id: str,
        after_seq: int = 0,
        limit: int = 50,
        action_prefix: str | None = None,
        actor_user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Sequence[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for _event_type, record in self.audit_log:
            if record.get("org_id") != org_id:
                continue
            seq = int(record.get("seq") or 0)
            if seq <= after_seq:
                continue
            action = str(record.get("action") or "")
            if action_prefix is not None and not action.startswith(action_prefix):
                continue
            actor = record.get("user_id")
            if actor_user_id is not None and actor != actor_user_id:
                continue
            created_at_value = record.get("created_at")
            if isinstance(created_at_value, str):
                try:
                    created_at_value = datetime.fromisoformat(created_at_value)
                except ValueError:
                    created_at_value = None
            if since is not None and (
                not isinstance(created_at_value, datetime) or created_at_value < since
            ):
                continue
            if until is not None and (
                not isinstance(created_at_value, datetime) or created_at_value >= until
            ):
                continue
            rows.append(dict(record))
        rows.sort(
            key=lambda r: (r.get("created_at") or "", int(r.get("seq") or 0)),
            reverse=True,
        )
        return tuple(rows[:limit])

    async def delete_user_history(
        self, *, org_id: str, user_id: str, reason: str | None = None
    ) -> HistoryDeletionResponse:
        """Physically erase a user's conversations from disk (desktop profile).

        Unlike the Postgres/in-memory adapters — which tombstone user-visible
        history while retaining audit-safe evidence — the desktop file store
        takes "delete my data" literally: each non-held conversation's session
        directory and JSONL streams are removed from disk and every object that
        becomes unreferenced is garbage-collected. Conversations under a legal
        hold (``metadata.legal_hold``) are skipped and left intact; their events
        are reported via ``events_retained``.

        The legacy ``HistoryDeletionResponse`` field names are mapped to the
        physical reality: ``conversations_archived`` / ``messages_tombstoned`` /
        ``runs_cancelled`` carry the *deleted* counts, and ``events_retained``
        carries the events kept because their conversation was on legal hold.
        The signed audit row records the byte-accurate tally.
        """

        now = datetime.now(timezone.utc)
        targets = [
            conversation
            for conversation in self.conversations.values()
            if conversation.org_id == org_id and conversation.user_id == user_id
        ]
        outcome = await self._purge_conversations(
            org_id=org_id,
            conversations=targets,
            trigger=_DeletionFields.TRIGGER_USER_REQUEST,
            reason=reason,
            now=now,
            user_id=user_id,
        )
        return HistoryDeletionResponse(
            org_id=org_id,
            user_id=user_id,
            conversations_archived=outcome.conversations,
            messages_tombstoned=outcome.messages,
            runs_cancelled=outcome.runs,
            events_retained=outcome.retained_events,
            audit_event_id=outcome.audit_event_id,
        )

    # ==================================================================
    # Export / import (portable single-conversation archive) — file store only
    # ==================================================================

    async def export_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        destination: Path,
    ) -> ExportManifest:
        """Write a portable ``.tar.gz`` backup of one conversation.

        Self-contained: the conversation's canonical session files, every
        object-store blob those files actually reference, and a manifest of
        SHA-256 hashes. The disposable catalog is not exported (it rebuilds).
        See :mod:`runtime_adapters.file.export_import`.
        """

        manifest = await ConversationArchiver(self).export(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            destination=Path(destination),
        )
        await self._record_export_audit(manifest)
        return manifest

    async def _record_export_audit(self, manifest: ExportManifest) -> None:
        """Write the tamper-evident conversation-export manifest row (``#9``).

        Binds the whole archive into the signed chain via a single content hash
        over every part's SHA-256 — no bytes, no destination host path, no
        secrets. The caller-supplied ``exported_at`` is the manifest timestamp.
        """

        parts_digest = hashlib.sha256(
            "\n".join(
                f"{name}:{digest}" for name, digest in sorted(manifest.parts.items())
            ).encode("utf-8")
        ).hexdigest()
        exported_at = manifest.exported_at.isoformat()
        audit_event_id = (
            f"conversation_export_{manifest.org_id}_{manifest.conversation_id}_"
            f"{int(manifest.exported_at.timestamp() * 1_000_000)}"
        )
        await self.write_audit_log(
            event_type=AuditManifest.EVENT_CONVERSATION_EXPORT,
            record=AuditManifest.export_record(
                audit_event_id=audit_event_id,
                org_id=manifest.org_id,
                user_id=manifest.user_id,
                conversation_id=manifest.conversation_id,
                exported_at=exported_at,
                parts_digest=parts_digest,
                part_count=len(manifest.parts),
                counts=manifest.counts.model_dump(),
            ),
        )

    async def import_conversation(
        self, *, org_id: str, user_id: str, source: Path
    ) -> ImportOutcome:
        """Import an archive under a fresh conversation id (fail-closed).

        Validates the manifest + every part's SHA-256 before writing anything,
        materialises the conversation with fresh conversation / run / message
        ids so it never clobbers an existing one, re-registers the referenced
        blobs, and refreshes the disposable catalog.
        """

        return await ConversationArchiver(self).import_(
            org_id=org_id, user_id=user_id, source=Path(source)
        )

    # ==================================================================
    # Physical deletion (bytes-gone) — used by delete + retention sweep
    # ==================================================================

    async def _purge_conversations(
        self,
        *,
        org_id: str,
        conversations: Sequence[ConversationRecord],
        trigger: str,
        reason: str | None,
        now: datetime,
        user_id: str | None = None,
        dry_run: bool = False,
    ) -> _PurgeOutcome:
        """Erase the given conversations' sessions + GC newly-orphaned objects.

        Serialised on the state lock so no concurrent writer can interleave a
        session write with a directory removal. Legal-held conversations are
        filtered out here (counted, never touched). When ``dry_run`` the tallies
        reflect what *would* be removed but nothing is deleted and no audit row
        is written (parity with the sweeper's dry-run contract).
        """

        outcome = _PurgeOutcome()
        async with self._state_lock:
            deletable: list[ConversationRecord] = []
            for conversation in conversations:
                if LegalHoldPolicy.is_on_hold(conversation):
                    outcome.skipped_legal_hold += 1
                    outcome.retained_events += self._conversation_event_count(
                        conversation.conversation_id
                    )
                    continue
                deletable.append(conversation)

            if not deletable:
                return outcome

            conv_ids = {c.conversation_id for c in deletable}
            # 1) Tally + snapshot victim object refs while the JSONL still exists.
            victim_refs = self._reachability.scan_all(deletable)
            outcome.conversations = len(deletable)
            outcome.messages = sum(
                1 for m in self.messages.values() if m.conversation_id in conv_ids
            )
            victim_runs = [
                run for run in self.runs.values() if run.conversation_id in conv_ids
            ]
            outcome.runs = len(victim_runs)
            outcome.events = sum(
                len(self.events_by_run.get(run.run_id, ())) for run in victim_runs
            )

            # 2) Fail-safe: verify the whole plan before removing a single byte.
            planned_dirs = self._session_eraser.plan(deletable)

            if dry_run:
                return outcome

            # 3) Erase session directories, then drop materialised + index state.
            self._session_eraser.erase(planned_dirs)
            for conversation in deletable:
                self._drop_conversation_state(conversation, victim_runs=victim_runs)

            # 4) GC objects that became unreferenced (survivors recomputed from
            #    the JSONL that remains on disk). Content-addressed sharing keeps
            #    any blob still referenced by another conversation alive.
            survivor_refs = self._reachability.scan_all(self.conversations.values())
            for digest in self._reachability.collectible(
                victim_refs=victim_refs,
                survivor_refs=survivor_refs,
                object_store=self.object_store,
            ):
                if self.object_store.delete(digest):
                    outcome.objects += 1

        outcome.audit_event_id = await self._record_deletion_audit(
            org_id=org_id,
            user_id=user_id,
            trigger=trigger,
            reason=reason,
            now=now,
            outcome=outcome,
        )
        self._telemetry.deletion_completed(
            conversations=outcome.conversations,
            objects_collected=outcome.objects,
            trigger=trigger,
        )
        return outcome

    def _conversation_event_count(self, conversation_id: str) -> int:
        """Count persisted events across every run of one conversation."""

        run_ids = {
            run.run_id
            for run in self.runs.values()
            if run.conversation_id == conversation_id
        }
        return sum(len(self.events_by_run.get(run_id, ())) for run_id in run_ids)

    def _drop_conversation_state(
        self,
        conversation: ConversationRecord,
        *,
        victim_runs: Sequence[RunRecord],
    ) -> None:
        """Remove one conversation from the materialised view + disposable index.

        The on-disk session directory is already gone; this keeps the in-memory
        dicts, idempotency maps, and SQLite index consistent so live reads never
        surface the purged conversation. The index also rebuilds from the (now
        absent) JSONL on the next :meth:`open`, so both paths agree.
        """

        conversation_id = conversation.conversation_id
        self.conversations.pop(conversation_id, None)
        if conversation.idempotency_key is not None:
            self._conversation_idempotency.pop(
                (
                    conversation.org_id,
                    conversation.user_id,
                    conversation.idempotency_key,
                ),
                None,
            )
        for message_id, message in tuple(self.messages.items()):
            if message.conversation_id == conversation_id:
                self.messages.pop(message_id, None)
        for run in victim_runs:
            if run.conversation_id != conversation_id:
                continue
            self.runs.pop(run.run_id, None)
            self.events_by_run.pop(run.run_id, None)
            if run.idempotency_key is not None:
                key = (run.org_id, run.user_id, run.idempotency_key)
                self._run_idempotency.pop(key, None)
                self._run_idempotency_fingerprint.pop(key, None)
        self._conversation_locks.pop(conversation_id, None)
        self._index.delete_conversation_cascade(conversation_id)

    async def _record_deletion_audit(
        self,
        *,
        org_id: str,
        user_id: str | None,
        trigger: str,
        reason: str | None,
        now: datetime,
        outcome: _PurgeOutcome,
    ) -> str:
        """Write the tamper-evident deletion-completed audit row; return its id."""

        audit_event_id = (
            f"data_purge_{org_id}_{trigger}_{int(now.timestamp() * 1_000_000)}"
        )
        await self.write_audit_log(
            event_type=_DeletionFields.EVENT_TYPE,
            record={
                _Fields.AUDIT_EVENT_ID: audit_event_id,
                _Fields.ORG_ID: org_id,
                _Fields.USER_ID: user_id,
                _Fields.REASON: reason,
                _Fields.DELETED_AT: now.isoformat(),
                _DeletionFields.TRIGGER: trigger,
                _DeletionFields.CONVERSATIONS_DELETED: outcome.conversations,
                _DeletionFields.MESSAGES_DELETED: outcome.messages,
                _DeletionFields.RUNS_DELETED: outcome.runs,
                _DeletionFields.EVENTS_DELETED: outcome.events,
                _DeletionFields.OBJECTS_GARBAGE_COLLECTED: outcome.objects,
                _DeletionFields.SKIPPED_LEGAL_HOLD: outcome.skipped_legal_hold,
            },
        )
        return audit_event_id

    async def sweep_expired_conversations(
        self, *, now: datetime | None = None, dry_run: bool = False
    ) -> FileStoreCleanupReport:
        """Reap conversations whose last activity predates the retention window.

        Gated on ``RUNTIME_FILE_STORE_RETENTION_DAYS`` (``retention_days``):
        ``0``/unset keeps everything forever and returns an empty report. When a
        positive window is configured, every conversation whose ``updated_at`` is
        older than ``now - retention_days`` is physically erased through the
        **existing** :meth:`_purge_conversations` path — same fail-safe plan,
        legal-hold skip, and object garbage collection as a user-initiated
        delete — so in-window conversations, legal-held conversations, and
        content-addressed objects still referenced by a survivor are untouched.
        Callable directly and also invoked from :meth:`open` at startup;
        ``dry_run`` reports the would-be tally without removing anything.
        """

        report = FileStoreCleanupReport(dry_run=dry_run)
        if self._retention_days <= 0:
            return report

        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self._retention_days)
        # Snapshot the victims (grouped by org) before any purge mutates the
        # materialised view — _purge_conversations audits + GCs per org.
        expired_by_org: dict[str, list[ConversationRecord]] = {}
        for conversation in self.conversations.values():
            if conversation.updated_at <= cutoff:
                expired_by_org.setdefault(conversation.org_id, []).append(conversation)

        for org_id, conversations in expired_by_org.items():
            outcome = await self._purge_conversations(
                org_id=org_id,
                conversations=conversations,
                trigger=_DeletionFields.TRIGGER_RETENTION_SWEEP,
                reason=f"file_store_retention:{self._retention_days}d",
                now=now,
                dry_run=dry_run,
            )
            report = report.adding(
                conversations=outcome.conversations,
                messages=outcome.messages,
                runs=outcome.runs,
                events=outcome.events,
                objects=outcome.objects,
                skipped_legal_hold=outcome.skipped_legal_hold,
            )
        self._telemetry.retention_sweep_completed(
            conversations=report.conversations_deleted,
            objects_collected=report.objects_collected,
            dry_run=dry_run,
        )
        return report

    # ==================================================================
    # PersistencePort — usage + pricing
    # ==================================================================

    async def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        async with self._state_lock:
            if record.run_id in self.run_usage:
                return
            self.run_usage[record.run_id] = record
            self._ledger(_Tables.RUN_USAGE).append_put(record.model_dump(mode="json"))

    async def record_model_call_usage(
        self, record: RuntimeModelCallUsageRecord
    ) -> None:
        async with self._state_lock:
            self.model_call_usage.append(record)
            self._ledger(_Tables.MODEL_CALL_USAGE).append_put(
                record.model_dump(mode="json")
            )

    async def update_run_usage_cost(
        self, *, run_id: str, cost_micro_usd: int, pricing_id: str, pricing_version: str
    ) -> None:
        async with self._state_lock:
            existing = self.run_usage.get(run_id)
            if existing is None:
                return
            updated = existing.model_copy(
                update={
                    "cost_micro_usd": cost_micro_usd,
                    "pricing_id": pricing_id,
                    "pricing_version": pricing_version,
                }
            )
            self.run_usage[run_id] = updated
            self._ledger(_Tables.RUN_USAGE).append_put(updated.model_dump(mode="json"))

    async def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        async with self._state_lock:
            for index, row in enumerate(self.model_call_usage):
                if row.id == usage_id:
                    updated = row.model_copy(
                        update={
                            "cost_micro_usd": cost_micro_usd,
                            "pricing_id": pricing_id,
                            "pricing_version": pricing_version,
                        }
                    )
                    self.model_call_usage[index] = updated
                    self._ledger(_Tables.MODEL_CALL_USAGE).append_put(
                        updated.model_dump(mode="json")
                    )
                    return

    async def upsert_pricing(self, record: ModelPricingRecord) -> ModelPricingRecord:
        async with self._state_lock:
            for index, existing in enumerate(self.pricing_rows):
                if (
                    existing.provider == record.provider
                    and existing.model_name == record.model_name
                    and existing.region == record.region
                    and existing.effective_until is None
                    and existing.effective_from < record.effective_from
                ):
                    closed = existing.model_copy(
                        update={"effective_until": record.effective_from}
                    )
                    self.pricing_rows[index] = closed
                    self._ledger(_Tables.PRICING).append_put(
                        closed.model_dump(mode="json")
                    )
            self.pricing_rows.append(record)
            self._ledger(_Tables.PRICING).append_put(record.model_dump(mode="json"))
        return record

    async def lookup_pricing(
        self, *, provider: str, model_name: str, region: str, at: datetime
    ) -> ModelPricingRecord | None:
        candidates = [
            row
            for row in self.pricing_rows
            if row.provider == provider
            and row.model_name == model_name
            and row.region == region
            and row.effective_from <= at
            and (row.effective_until is None or row.effective_until > at)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: row.effective_from)

    async def list_runs_missing_cost(
        self, *, limit: int, cursor: str | None = None
    ) -> Sequence[RuntimeRunUsageRecord]:
        rows = sorted(
            (row for row in self.run_usage.values() if row.cost_micro_usd is None),
            key=lambda row: row.id,
        )
        if cursor is not None:
            rows = [row for row in rows if row.id > cursor]
        return tuple(rows[:limit])

    async def upsert_user_daily_usage(self, row: UsageDailyUserRow) -> None:
        async with self._state_lock:
            self.user_daily_usage[self._user_daily_key(row)] = row
            self._ledger(_Tables.USER_DAILY).append_put(row.model_dump(mode="json"))

    async def upsert_org_daily_usage(self, row: UsageDailyOrgRow) -> None:
        async with self._state_lock:
            self.org_daily_usage[self._org_daily_key(row)] = row
            self._ledger(_Tables.ORG_DAILY).append_put(row.model_dump(mode="json"))

    async def upsert_connector_daily_usage(self, row: UsageDailyConnectorRow) -> None:
        async with self._state_lock:
            self.connector_daily_usage[self._connector_daily_key(row)] = row
            self._ledger(_Tables.CONNECTOR_DAILY).append_put(
                row.model_dump(mode="json")
            )

    async def upsert_subagent_daily_usage(self, row: UsageDailySubagentRow) -> None:
        async with self._state_lock:
            self.subagent_daily_usage[self._subagent_daily_key(row)] = row
            self._ledger(_Tables.SUBAGENT_DAILY).append_put(row.model_dump(mode="json"))

    async def upsert_purpose_daily_usage(self, row: UsageDailyPurposeRow) -> None:
        async with self._state_lock:
            self.purpose_daily_usage[self._purpose_daily_key(row)] = row
            self._ledger(_Tables.PURPOSE_DAILY).append_put(row.model_dump(mode="json"))

    async def query_user_daily_usage(
        self, *, org_id: str, user_id: str, start_day: datetime, end_day: datetime
    ) -> Sequence[UsageDailyUserRow]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.user_daily_usage.values()
                    if row.org_id == org_id
                    and row.user_id == user_id
                    and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_org_daily_usage(
        self, *, org_id: str, start_day: datetime, end_day: datetime
    ) -> Sequence[UsageDailyOrgRow]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.org_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_connector_daily_usage(
        self, *, org_id: str, start_day: datetime, end_day: datetime
    ) -> Sequence[UsageDailyConnectorRow]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.connector_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_subagent_daily_usage(
        self, *, org_id: str, start_day: datetime, end_day: datetime
    ) -> Sequence[UsageDailySubagentRow]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.subagent_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_purpose_daily_usage(
        self, *, org_id: str, start_day: datetime, end_day: datetime
    ) -> Sequence[UsageDailyPurposeRow]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.purpose_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_model_call_usage_for_range(
        self, *, org_id: str | None, start: datetime, end: datetime
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.model_call_usage
                    if (org_id is None or row.org_id == org_id)
                    and start <= row.created_at <= end
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )
        )

    async def list_run_ids_for_agent(
        self, *, org_id: str, agent_id: str, start: datetime, end: datetime
    ) -> Sequence[str]:
        if not agent_id:
            return ()
        matches: list[tuple[datetime, str]] = []
        for run in self.runs.values():
            if run.org_id != org_id:
                continue
            if not (start <= run.created_at <= end):
                continue
            trace_metadata = getattr(run.runtime_context, "trace_metadata", None)
            if not isinstance(trace_metadata, dict):
                continue
            if trace_metadata.get("agent_id") == agent_id:
                matches.append((run.created_at, run.run_id))
        matches.sort(key=lambda entry: entry[0], reverse=True)
        return tuple(run_id for _, run_id in matches)

    async def query_run_usage(
        self, *, org_id: str, run_id: str
    ) -> RuntimeRunUsageRecord | None:
        record = self.run_usage.get(run_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    async def query_run_usage_for_range(
        self,
        *,
        org_id: str | None,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeRunUsageRecord]:
        return tuple(
            sorted(
                (
                    row
                    for row in self.run_usage.values()
                    if (org_id is None or row.org_id == org_id)
                    and (user_id is None or row.user_id == user_id)
                    and start <= row.completed_at <= end
                    and (user_id is None or row.pii_purged_at is None)
                ),
                key=lambda r: r.completed_at,
                reverse=True,
            )
        )

    async def query_top_conversations(
        self, *, org_id: str, user_id: str, start: datetime, end: datetime, limit: int
    ) -> Sequence[UsageConversationAggregateRecord]:
        aggregates: dict[str, UsageConversationAggregateRecord] = {}
        for row in self.run_usage.values():
            if (
                row.org_id != org_id
                or row.user_id != user_id
                or not (start <= row.completed_at <= end)
                or row.pii_purged_at is not None
            ):
                continue
            current = aggregates.get(row.conversation_id)
            if current is None:
                conversation = self.conversations.get(row.conversation_id)
                aggregates[row.conversation_id] = UsageConversationAggregateRecord(
                    conversation_id=row.conversation_id,
                    title=conversation.title if conversation is not None else None,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    cached_input_tokens=row.cached_input_tokens,
                    total_tokens=row.total_tokens,
                    runs_count=1,
                    cost_micro_usd=row.cost_micro_usd,
                )
                continue
            aggregates[row.conversation_id] = current.model_copy(
                update={
                    "input_tokens": current.input_tokens + row.input_tokens,
                    "output_tokens": current.output_tokens + row.output_tokens,
                    "cached_input_tokens": current.cached_input_tokens
                    + row.cached_input_tokens,
                    "total_tokens": current.total_tokens + row.total_tokens,
                    "runs_count": current.runs_count + 1,
                    "cost_micro_usd": self._sum_optional_cost(
                        current.cost_micro_usd, row.cost_micro_usd
                    ),
                }
            )
        ranked = sorted(
            aggregates.values(), key=lambda item: item.total_tokens, reverse=True
        )
        return tuple(ranked[:limit])

    @staticmethod
    def _sum_optional_cost(left: int | None, right: int | None) -> int | None:
        if left is None:
            return right
        if right is None:
            return left
        return left + right

    async def query_model_call_usage_for_run(
        self, *, org_id: str, run_id: str
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        return tuple(
            row
            for row in self.model_call_usage
            if row.org_id == org_id and row.run_id == run_id
        )

    async def query_latest_run_usage_for_conversation(
        self, *, org_id: str, user_id: str, conversation_id: str
    ) -> RuntimeRunUsageRecord | None:
        candidates = [
            row
            for row in self.run_usage.values()
            if row.org_id == org_id
            and row.user_id == user_id
            and row.conversation_id == conversation_id
            and row.pii_purged_at is None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.completed_at)

    async def query_compression_events_for_run(
        self, *, org_id: str, run_id: str
    ) -> Sequence[CompressionEventRecord]:
        return tuple(
            sorted(
                (
                    event
                    for event in self.compression_events
                    if event.org_id == org_id and event.run_id == run_id
                ),
                key=lambda e: e.created_at,
            )
        )

    # ==================================================================
    # PersistencePort — budgets
    # ==================================================================

    async def lookup_budgets_for_run(
        self, *, org_id: str, user_id: str, now: datetime | None = None
    ) -> Sequence:
        from datetime import datetime as _datetime, timezone as _timezone

        from agent_runtime.budgets.period import BudgetPeriodCalculator
        from agent_runtime.persistence.records import BudgetWithState

        if now is None:
            now = _datetime.now(_timezone.utc)
        results: list = []
        for budget in self.budgets.values():
            if budget.org_id != org_id:
                continue
            if budget.scope.value == "user" and budget.user_id != user_id:
                continue
            window = BudgetPeriodCalculator.window(budget.period, now=now)
            state_key = (budget.id, window.period_start.isoformat())
            state = self.budget_states.get(state_key)
            reserved_micro = sum(
                r.reserved_micro_usd
                for r in self.budget_reservations.values()
                if r.budget_id == budget.id
                and r.period_start == window.period_start
                and r.consumed_at is None
            )
            reserved_tokens = sum(
                r.reserved_tokens
                for r in self.budget_reservations.values()
                if r.budget_id == budget.id
                and r.period_start == window.period_start
                and r.consumed_at is None
            )
            if state is not None:
                state = state.model_copy(
                    update={
                        "current_spend_micro_usd": state.current_spend_micro_usd
                        + reserved_micro,
                        "current_spend_tokens": state.current_spend_tokens
                        + reserved_tokens,
                    }
                )
            elif reserved_micro > 0 or reserved_tokens > 0:
                state = BudgetStateRecord(
                    budget_id=budget.id,
                    period_start=window.period_start,
                    period_end=window.period_end,
                    current_spend_micro_usd=reserved_micro,
                    current_spend_tokens=reserved_tokens,
                )
            results.append(BudgetWithState(budget=budget, state=state))
        results.sort(key=lambda e: e.budget.id)
        return tuple(results)

    async def charge_budget(
        self,
        *,
        budget_id: str,
        period_start,
        period_end,
        delta_micro_usd: int,
        delta_tokens: int,
        run_id: str,
        now,
    ) -> ChargeOutcome:
        async with self._state_lock:
            key = (budget_id, period_start.isoformat())
            state = self.budget_states.get(key)
            if state is None:
                state = BudgetStateRecord(
                    budget_id=budget_id,
                    period_start=period_start,
                    period_end=period_end,
                    current_spend_micro_usd=0,
                    current_spend_tokens=0,
                )
            if state.last_charged_run_id == run_id:
                return ChargeOutcome.IDEMPOTENT_NOOP
            updated = state.model_copy(
                update={
                    "current_spend_micro_usd": state.current_spend_micro_usd
                    + delta_micro_usd,
                    "current_spend_tokens": state.current_spend_tokens + delta_tokens,
                    "row_version": state.row_version + 1,
                    "last_charged_run_id": run_id,
                    "updated_at": now,
                }
            )
            self.budget_states[key] = updated
            self._rewrite_budget_states()
            return ChargeOutcome.APPLIED

    async def reserve_budget(
        self,
        *,
        budget_id: str,
        period_start,
        run_id: str,
        reserved_micro_usd: int,
        reserved_tokens: int,
        now,
    ) -> BudgetReservationRecord | None:
        from agent_runtime.budgets.reservations import BudgetReservationManager

        async with self._state_lock:
            existing = next(
                (
                    r
                    for r in self.budget_reservations.values()
                    if r.budget_id == budget_id
                    and r.run_id == run_id
                    and r.consumed_at is None
                ),
                None,
            )
            if existing is not None:
                return None
            record = BudgetReservationRecord(
                budget_id=budget_id,
                period_start=period_start,
                run_id=run_id,
                reserved_micro_usd=reserved_micro_usd,
                reserved_tokens=reserved_tokens,
                expires_at=BudgetReservationManager.expires_at(now=now, ttl_seconds=60),
            )
            self.budget_reservations[record.reservation_id] = record
            self._rewrite_budget_reservations()
            return record

    async def consume_budget_reservation(self, *, reservation_id: str, now) -> None:
        async with self._state_lock:
            record = self.budget_reservations.get(reservation_id)
            if record is None or record.consumed_at is not None:
                return
            self.budget_reservations[reservation_id] = record.model_copy(
                update={"consumed_at": now}
            )
            self._rewrite_budget_reservations()

    async def reap_expired_budget_reservations(self, *, now) -> int:
        async with self._state_lock:
            purged = 0
            for reservation_id, record in list(self.budget_reservations.items()):
                if record.consumed_at is None and record.expires_at < now:
                    del self.budget_reservations[reservation_id]
                    purged += 1
            if purged:
                self._rewrite_budget_reservations()
            return purged

    async def list_budgets(self, *, org_id: str) -> Sequence[BudgetRecord]:
        return tuple(
            sorted(
                (b for b in self.budgets.values() if b.org_id == org_id),
                key=lambda b: b.created_at,
                reverse=True,
            )
        )

    async def list_tool_budgets_for_org(
        self, *, org_id: str
    ) -> Sequence[ToolBudgetRecord]:
        return tuple(
            b
            for b in self.tool_budgets.values()
            if b.org_id == org_id or b.org_id is None
        )

    async def get_budget(self, *, org_id: str, budget_id: str) -> BudgetRecord | None:
        record = self.budgets.get(budget_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    async def create_budget(self, record: BudgetRecord) -> BudgetRecord:
        async with self._state_lock:
            for existing in self.budgets.values():
                if (
                    existing.org_id == record.org_id
                    and (existing.user_id or "<org>") == (record.user_id or "<org>")
                    and existing.scope == record.scope
                    and existing.period == record.period
                ):
                    raise ValueError("budget already exists for that scope/period")
            self.budgets[record.id] = record
            self._ledger(_Tables.BUDGETS).append_put(record.model_dump(mode="json"))
        return record

    async def update_budget(self, record: BudgetRecord) -> BudgetRecord:
        async with self._state_lock:
            if record.id not in self.budgets:
                raise KeyError(record.id)
            self.budgets[record.id] = record
            self._ledger(_Tables.BUDGETS).append_put(record.model_dump(mode="json"))
        return record

    async def delete_budget(self, *, org_id: str, budget_id: str) -> None:
        async with self._state_lock:
            record = self.budgets.get(budget_id)
            if record is None or record.org_id != org_id:
                return
            del self.budgets[budget_id]
            self._ledger(_Tables.BUDGETS).append_delete(budget_id)
            self.budget_states = {
                key: state
                for key, state in self.budget_states.items()
                if state.budget_id != budget_id
            }
            self.budget_reservations = {
                rid: r
                for rid, r in self.budget_reservations.items()
                if r.budget_id != budget_id
            }
            self._rewrite_budget_states()
            self._rewrite_budget_reservations()

    def _rewrite_budget_states(self) -> None:
        self._ledger(_Tables.BUDGET_STATES).rewrite(
            state.model_dump(mode="json") for state in self.budget_states.values()
        )

    def _rewrite_budget_reservations(self) -> None:
        self._ledger(_Tables.BUDGET_RESERVATIONS).rewrite(
            r.model_dump(mode="json") for r in self.budget_reservations.values()
        )

    # ==================================================================
    # PersistencePort — retention
    # ==================================================================

    async def list_retention_orgs(self) -> Sequence[str]:
        seen: set[str] = set()
        seen.update(c.org_id for c in self.conversations.values())
        seen.update(m.org_id for m in self.messages.values())
        seen.update(r.org_id for r in self.runs.values())
        return tuple(sorted(seen))

    async def sweep_retention_kind(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        dry_run: bool = False,
        chunk_size: int = 0,
    ) -> RetentionSweepOutcome:
        """Physically reap expired conversation sessions for one tenant.

        The file store is session-folder-based rather than row-based, so the
        conversation session is the unit of retention. Session-scoped reaping is
        driven by :data:`RetentionKind.MESSAGES` (the conversation-lifecycle
        kind — see ``docs/features/retention.md``); a session is expired when its
        last activity (``updated_at``) is older than ``ttl_seconds``. Expired,
        non-legal-hold sessions are erased from disk with the same GC + fail-safe
        plan as a user-initiated delete. Every other kind is subsumed by session
        deletion and returns an empty tally. ``dry_run`` reports what would be
        removed without deleting.
        """

        if kind is not RetentionKind.MESSAGES:
            return RetentionSweepOutcome(org_id=org_id, kind=kind)

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=max(ttl_seconds, 0))
        expired = [
            conversation
            for conversation in self.conversations.values()
            if conversation.org_id == org_id and conversation.updated_at <= cutoff
        ]
        outcome = await self._purge_conversations(
            org_id=org_id,
            conversations=expired,
            trigger=_DeletionFields.TRIGGER_RETENTION_SWEEP,
            reason=f"retention:{kind.value}",
            now=now,
            dry_run=dry_run,
        )
        return RetentionSweepOutcome(
            org_id=org_id,
            kind=kind,
            tombstoned=0,
            deleted=outcome.conversations,
            skipped_legal_hold=outcome.skipped_legal_hold,
        )

    async def insert_retention_deletion_evidence(self, record) -> None:
        async with self._state_lock:
            self.deletion_evidence.append(record)
            payload = (
                record.model_dump(mode="json")
                if hasattr(record, "model_dump")
                else dict(record)
            )
            self._ledger(_Tables.DELETION_EVIDENCE).append_put(payload)

    async def backfill_retention_until(
        self, *, org_id: str, kind, ttl_seconds: int, chunk_size: int
    ) -> int:
        return 0

    async def recompute_retention_until_for_policy(
        self, *, org_id: str, kind, scope, resource_id, ttl_seconds
    ) -> int:
        return 0

    async def list_retention_policies(
        self, *, org_id: str
    ) -> Sequence[RetentionPolicyRecord]:
        return tuple(self.retention_policies.get(org_id, ()))

    async def upsert_retention_policy(
        self, record: RetentionPolicyRecord
    ) -> RetentionPolicyRecord:
        async with self._state_lock:
            bucket = list(self.retention_policies.get(record.org_id, ()))
            bucket = [
                row
                for row in bucket
                if (row.scope, row.resource_id, row.kind)
                != (record.scope, record.resource_id, record.kind)
            ]
            bucket.append(record)
            self.retention_policies[record.org_id] = tuple(bucket)
            self._rewrite_retention_policies()
        return record

    async def delete_retention_policy(self, *, org_id: str, policy_id: str) -> None:
        async with self._state_lock:
            bucket = self.retention_policies.get(org_id, ())
            self.retention_policies[org_id] = tuple(
                row for row in bucket if row.id != policy_id
            )
            self._rewrite_retention_policies()

    def _rewrite_retention_policies(self) -> None:
        rows = [
            policy.model_dump(mode="json")
            for policies in self.retention_policies.values()
            for policy in policies
        ]
        self._ledger(_Tables.RETENTION_POLICIES).rewrite(rows)

    # ==================================================================
    # RuntimeQueuePort
    # ==================================================================

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        self.run_commands.append(command)
        await self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_REQUESTED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=None,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        self.cancel_commands.append(command)
        await self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_CANCEL_REQUESTED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=None,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        self.approval_commands.append(command)
        await self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.APPROVAL_RESOLVED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=command.approval_id,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_stage_commit(self, command: RuntimeStageCommitCommand) -> None:
        """Enqueue a staged-write commit command (PRD-D2)."""

        self.stage_commit_commands.append(command)
        await self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.STAGE_COMMIT_REQUESTED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=None,
            payload=command.model_dump(mode="json"),
        )

    async def claim_next(
        self, *, worker_id: str, lock_expires_at: datetime
    ) -> RuntimeWorkerClaim | None:
        async with self._state_lock:
            now = datetime.now(timezone.utc)
            for command_id in self._queue_order:
                status_value = self._queue_statuses[command_id]
                if status_value in {OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER}:
                    continue
                if self._queue_available_at[command_id] > now:
                    continue
                active_claim = self._queue_claims.get(command_id)
                if active_claim is not None and active_claim.lock_expires_at > now:
                    continue
                claim = self._claim_command(
                    command_id=command_id,
                    worker_id=worker_id,
                    lock_expires_at=lock_expires_at,
                )
                self._queue_claims[command_id] = claim
                self._queue_statuses[command_id] = OutboxStatus.CLAIMED
                self._append_queue_op(
                    {
                        "op": "attempts",
                        "command_id": command_id,
                        "attempts": self._queue_attempts[command_id],
                    }
                )
                return claim
            return None

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        async with self._state_lock:
            self._queue_statuses[result.command_id] = OutboxStatus.COMPLETED
            self._queue_claims.pop(result.command_id, None)
            self._append_queue_status(result.command_id, OutboxStatus.COMPLETED, None)

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        async with self._state_lock:
            available_at = result.retry_available_at or datetime.now(timezone.utc)
            self._queue_statuses[result.command_id] = OutboxStatus.RETRY
            self._queue_available_at[result.command_id] = available_at
            self._queue_claims.pop(result.command_id, None)
            self._append_queue_status(
                result.command_id, OutboxStatus.RETRY, available_at
            )

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        async with self._state_lock:
            self._queue_statuses[result.command_id] = OutboxStatus.DEAD_LETTER
            self._queue_claims.pop(result.command_id, None)
            self._append_queue_status(result.command_id, OutboxStatus.DEAD_LETTER, None)

    async def _register_command(
        self,
        *,
        command_id: str,
        command_type: str,
        org_id: str,
        run_id: str,
        approval_id: str | None,
        payload: dict[str, object],
    ) -> None:
        async with self._state_lock:
            available_at = datetime.now(timezone.utc)
            self._queue_order.append(command_id)
            full_payload = {
                **payload,
                _Fields.COMMAND_ID: command_id,
                _Fields.COMMAND_TYPE: command_type,
                _Fields.ORG_ID: org_id,
                _Fields.RUN_ID: run_id,
                _Fields.APPROVAL_ID: approval_id,
            }
            self._queue_payloads[command_id] = full_payload
            self._queue_statuses[command_id] = OutboxStatus.PENDING
            self._queue_attempts[command_id] = 0
            self._queue_available_at[command_id] = available_at
            self._append_queue_op(
                {
                    "op": "enqueue",
                    "command_id": command_id,
                    "payload": full_payload,
                    "available_at": available_at.isoformat(),
                }
            )

    def _claim_command(
        self, *, command_id: str, worker_id: str, lock_expires_at: datetime
    ) -> RuntimeWorkerClaim:
        payload = self._queue_payloads[command_id]
        self._queue_attempts[command_id] += 1
        return RuntimeWorkerClaim(
            command_id=command_id,
            command_type=str(payload[_Fields.COMMAND_TYPE]),
            org_id=str(payload[_Fields.ORG_ID]),
            run_id=str(payload[_Fields.RUN_ID]),
            approval_id=payload[_Fields.APPROVAL_ID]
            if isinstance(payload[_Fields.APPROVAL_ID], str)
            else None,
            locked_by=worker_id,
            lock_expires_at=lock_expires_at,
            attempts=self._queue_attempts[command_id],
            payload=payload,
        )

    def _append_queue_status(
        self, command_id: str, status_value: OutboxStatus, available_at: datetime | None
    ) -> None:
        self._append_queue_op(
            {
                "op": "status",
                "command_id": command_id,
                "status": status_value.value,
                "available_at": (
                    available_at or datetime.now(timezone.utc)
                ).isoformat(),
            }
        )

    def _append_queue_op(self, op: dict[str, object]) -> None:
        JsonlIo.append_line(self._layout.state_path(_Tables.QUEUE), op)
        self._queue_line_count += 1


__all__ = ("FileRuntimeApiStore",)
