"""Read-only projection over conversations, messages, runs, and events.

Provides the query side of the CQRS-lite split: ``list_models``,
``get_conversation``, ``list_conversations``, ``list_messages``,
``get_conversation_context``, ``get_run``, and ``replay_events``. Never mutates
state; returns typed Pydantic responses for HTTP routes and the SSE adapter.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import datetime

from agent_runtime.api.constants import Messages, Values
from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.api.model_enablement import ModelEnablementResolver
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.usage_service import ConversationContextBuilder
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.pricing import ModelPricingCatalog
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationResponse,
    DefaultModelSelection,
    MessageListResponse,
    RunHistoryEntry,
    RunHistoryResponse,
    RunListResponse,
    RunSummaryResponse,
    ModelCatalogResponse,
    RunStatusResponse,
    RuntimeEventReplayResponse,
)
from runtime_api.schemas.surfaces_v2 import RunSurfacesResponse
from starlette import status


class KeysetCursor:
    """Opaque ``(created_at, id)`` keyset cursor codec for backward pagination.

    Generic over the trailing id — message pagination pages on
    ``(created_at, message_id)``; run-history pagination (PRD-05) pages on
    ``(created_at, run_id)`` through the exact same codec. Encodes the keyset of
    the boundary row of a returned page so a follow-up request can fetch
    strictly-older rows. The query service owns encode/decode; the persistence
    port receives the DECODED keyset so the adapters never re-parse the token.

    ``decode`` is deliberately tolerant: a malformed or empty token is treated
    as "no cursor" (returns ``None``) so a bad client value degrades to the
    most-recent window rather than raising a 500.
    """

    _SEPARATOR = "|"

    @staticmethod
    def encode(created_at: datetime, row_id: str) -> str:
        """Return a base64url token over ``f"{created_at.isoformat()}|{row_id}"``."""

        raw = f"{created_at.isoformat()}{KeysetCursor._SEPARATOR}{row_id}"
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode(token: str | None) -> tuple[datetime, str] | None:
        """Decode a cursor to ``(created_at, row_id)``, or ``None`` if malformed.

        ``UnicodeError`` and ``binascii.Error`` are both ``ValueError``
        subclasses, so a single ``except ValueError`` covers a non-ascii token,
        bad base64 padding, and an unparseable timestamp.
        """

        if not token or not token.strip():
            return None
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            created_at_iso, separator, row_id = raw.partition(KeysetCursor._SEPARATOR)
            if not separator or not row_id:
                return None
            return datetime.fromisoformat(created_at_iso), row_id
        except ValueError:
            return None


class ConversationQueryService:
    """Read-only projection that assembles typed responses from persistence and event stores.

    Scope enforcement is enforced on every public method: records outside the
    caller's (org_id, user_id) scope raise a 404 rather than leaking data.
    """

    TERMINAL_RUN_STATUSES = frozenset(
        {
            AgentRunStatus.CANCELLED,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMED_OUT,
        }
    )

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        settings: RuntimeSettings,
        model_resolver: ModelConfigResolver,
    ) -> None:
        self._persistence = persistence
        self._event_store = event_store
        self._settings = settings
        self._model_resolver = model_resolver
        self._pricing_catalog = ModelPricingCatalog.from_litellm()

    async def list_models(
        self,
        *,
        org_id: str | None = None,
        user_key_providers: frozenset[str] = frozenset(),
    ) -> ModelCatalogResponse:
        """Return the model catalog with per-provider credential + enablement flags.

        The catalog is assembled in-process from ``RuntimeSettings``. Each item's
        ``configured`` flag reflects a usable credential from either source the run
        path accepts — a deployment env key **or** one of ``user_key_providers``
        (the caller's stored BYOK provider slugs, resolved by the route from the
        same per-(org, user) policies resolver the run-create gate uses). Each
        item's ``enabled`` flag is then resolved from the org's workspace
        ``enabled_models`` curation (PR-2C) — an explicit selection, or the
        newest-per-provider default when the workspace hasn't curated.
        ``ModelCatalog.build`` is the single source of truth and already
        returns an id-unique tuple with the runtime default present exactly
        once, so no further deduplication happens here.
        """

        unique_models = ModelCatalog.build(
            self._settings, user_key_providers=user_key_providers
        )
        defaults = (
            await self._persistence.get_workspace_defaults(org_id=org_id)
            if org_id is not None
            else None
        )
        # The always-enabled default is the workspace default when set, else the
        # runtime settings default — the model every run falls back to, so it
        # must always be selectable regardless of curation.
        effective_default = (
            defaults.default_model
            if defaults is not None and defaults.default_model is not None
            else DefaultModelSelection(
                provider=self._settings.default_model.provider,
                model_name=self._settings.default_model.model_name,
            )
        )
        enabled = ModelEnablementResolver.apply(
            unique_models,
            enabled_models=defaults.enabled_models if defaults is not None else None,
            default_model=effective_default,
        )
        return ModelCatalogResponse(
            default_model_id=self._settings.default_model.model_name,
            models=enabled,
        )

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationResponse:
        """Return conversation metadata for the caller scope."""

        conversation = await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        # desktop-run-identity §D2 — GET /conversations/{id} carries the SAME
        # projection as the list path (active-run overlay + preview/model +
        # ``latest_run_id_any_status``), so a client reopening a conversation
        # resolves its head run from either endpoint with an identical shape.
        projected = await self._with_latest_run(
            conversation.to_response(), org_id=org_id
        )
        return await self._with_list_fields(projected, org_id=org_id)

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = Values.DEFAULT_CONVERSATION_LIMIT,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> ConversationListResponse:
        """Return scoped conversations newest-first, enriched with each one's active run.

        ``has_more`` is derived from whether the store returned a full page, so
        callers must re-request with a cursor (not implemented yet) when it is True.
        """

        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self._persistence.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=bounded_limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )
        responses: list[ConversationResponse] = []
        for record in records:
            projected = await self._with_latest_run(record.to_response(), org_id=org_id)
            projected = await self._with_list_fields(projected, org_id=org_id)
            responses.append(projected)
        return ConversationListResponse(
            conversations=tuple(responses),
            has_more=len(records) == bounded_limit,
        )

    async def list_messages(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        limit: int = Values.DEFAULT_MESSAGE_LIMIT,
        before: str | None = None,
        include_deleted: bool = False,
    ) -> MessageListResponse:
        """Return the most-recent window of message history, ASC, with a keyset cursor.

        Gated on a successful conversation scope check. The returned
        ``messages`` array stays oldest-first (ASC) so transcript consumers can
        read it in array order. ``before`` is an opaque :class:`KeysetCursor`;
        when present it selects the page strictly older than its keyset, and a
        malformed/empty token is tolerated as "no cursor" (most-recent window).
        ``next_cursor`` encodes the OLDEST returned message's keyset when older
        messages remain (``has_more``), else ``None``.
        """

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        keyset = KeysetCursor.decode(before)
        records = await self._persistence.list_messages(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=bounded_limit,
            before_created_at=keyset[0] if keyset is not None else None,
            before_message_id=keyset[1] if keyset is not None else None,
            include_deleted=include_deleted,
        )
        has_more = len(records) == bounded_limit
        # ``records`` is ASC, so ``records[0]`` is the oldest in this window; its
        # keyset is what a follow-up ``before`` request pages backwards from.
        next_cursor = (
            KeysetCursor.encode(records[0].created_at, records[0].message_id)
            if has_more and records
            else None
        )
        return MessageListResponse(
            conversation_id=conversation_id,
            messages=tuple(record.to_response() for record in records),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def list_runs_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        limit: int = Values.DEFAULT_CONVERSATION_LIMIT,
    ) -> RunListResponse:
        """Return the conversation's runs newest-first for the multi-run selector.

        Gated on a successful conversation scope check (a run outside the caller's
        scope 404s rather than leaking). Backs the Run cockpit's ``RunMultiSelect``
        (desktop-run-identity §D2, Phase 6) — the durable replacement for the dead
        ``GET /v1/agent/runs`` auto-resolve the client used to attempt.
        """

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self._persistence.list_runs_for_conversation(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=bounded_limit,
        )
        return RunListResponse(
            runs=tuple(
                RunSummaryResponse(
                    run_id=record.run_id,
                    status=record.status,
                    model_name=record.model_name,
                    created_at=record.created_at,
                    started_at=record.started_at,
                    completed_at=record.completed_at,
                )
                for record in records
            ),
            has_more=len(records) == bounded_limit,
        )

    async def list_run_history(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = Values.DEFAULT_RUN_HISTORY_LIMIT,
        cursor: str | None = None,
    ) -> RunHistoryResponse:
        """Return the caller's org-scoped run history, newest-first, paginated (PRD-05).

        The run-keyed spine Activity reads to show FINISHED runs (all eight
        statuses), not just the in-flight ones the conversation list carries.
        Keyset-paginated on ``(created_at, run_id)`` via an opaque
        :class:`KeysetCursor`; a malformed/empty ``cursor`` degrades to the
        most-recent window rather than raising.

        ``has_more`` is derived by fetching ``limit + 1`` rows and truncating —
        NOT ``len == limit`` — so an exact-multiple boundary never reports a
        spurious extra page. ``next_cursor`` encodes the OLDEST row of the
        returned page and is ``None`` exactly when ``has_more`` is ``False``.
        """

        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        keyset = KeysetCursor.decode(cursor)
        # Fetch one extra row to disambiguate has_more on an exact-multiple page.
        records = await self._persistence.list_runs_for_org(
            org_id=org_id,
            user_id=user_id,
            limit=bounded_limit + 1,
            before_created_at=keyset[0] if keyset is not None else None,
            before_run_id=keyset[1] if keyset is not None else None,
        )
        has_more = len(records) > bounded_limit
        page = records[:bounded_limit]
        # ``page`` is DESC, so ``page[-1]`` is the OLDEST row in this window; its
        # keyset is what a follow-up ``cursor`` request pages backwards from.
        next_cursor = (
            KeysetCursor.encode(page[-1].created_at, page[-1].run_id)
            if has_more and page
            else None
        )
        return RunHistoryResponse(
            runs=await self._attach_meta_counters(org_id=org_id, page=page),
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def _attach_meta_counters(
        self,
        *,
        org_id: str,
        page: Sequence[RunHistoryEntry],
    ) -> tuple[RunHistoryEntry, ...]:
        """Stamp the Activity meta counters onto a run-history page (PRD-08 D1).

        Two grouped aggregates over indexes that already exist — one query for
        the tool-invocation counts, one for pending approvals — keyed by the
        page's run ids (bounded by ``limit``, never N+1). A run ABSENT from the
        tool-invocation map reports ``connector_count``/``step_count`` as ``None``
        (unknown — recorded before the writer existed, D1b), NOT ``0``; a run
        absent from the approval map reports ``0`` (approvals persist since
        ``0001``). The wire model defaults already carry these, so the stamp only
        overrides when a real count exists.
        """

        if not page:
            return ()
        run_ids = [entry.run_id for entry in page]
        tool_counts = await self._persistence.count_tool_invocations_for_runs(
            org_id=org_id, run_ids=run_ids
        )
        pending = await self._persistence.count_pending_approvals_for_runs(
            org_id=org_id, run_ids=run_ids
        )
        stamped: list[RunHistoryEntry] = []
        for entry in page:
            counts = tool_counts.get(entry.run_id)
            step_count = counts[0] if counts is not None else None
            connector_count = counts[1] if counts is not None else None
            stamped.append(
                entry.model_copy(
                    update={
                        "step_count": step_count,
                        "connector_count": connector_count,
                        "pending_approval_count": pending.get(entry.run_id, 0),
                    }
                )
            )
        return tuple(stamped)

    async def get_conversation_context(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationContextResponse:
        """Return a context-window summary for the conversation's most recent run.

        When no run exists yet, returns a default-model placeholder so the UI
        can render context-budget progress even before the first message.
        """

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        latest_run = await self._persistence.query_latest_run_usage_for_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if latest_run is None:
            default_model = self._settings.default_model
            return ConversationContextBuilder.build(
                provider=default_model.provider,
                model_name=default_model.model_name,
                latest_run=None,
                per_call_rows=(),
                compression_events=(),
                pricing=None,
            )

        per_call_rows = await self._persistence.query_model_call_usage_for_run(
            org_id=org_id, run_id=latest_run.run_id
        )
        compression_events = await self._persistence.query_compression_events_for_run(
            org_id=org_id, run_id=latest_run.run_id
        )
        pricing = await self._pricing_catalog.lookup(
            provider=latest_run.model_provider,
            model_name=latest_run.model_name,
            region="global",
            at=latest_run.completed_at,
        )
        return ConversationContextBuilder.build(
            provider=latest_run.model_provider,
            model_name=latest_run.model_name,
            latest_run=latest_run,
            per_call_rows=per_call_rows,
            compression_events=compression_events,
            pricing=pricing,
        )

    async def get_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> RunStatusResponse:
        """Return current run state."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        return run.to_response()

    async def replay_events(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        after_sequence: int,
    ) -> RuntimeEventReplayResponse:
        """Return events persisted after ``after_sequence`` for SSE reconnect replay.

        ``latest_sequence_no`` is derived from the fetched batch when possible,
        otherwise from a dedicated store query — keeping the field accurate even
        when the batch is empty.
        """

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events = tuple(
            await self._event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=after_sequence,
            )
        )
        # Prefer the max from the fetched slice; fall back to the store query
        # only when the batch is empty so we avoid a second round-trip on the hot path.
        latest_sequence_no = max(
            (event.sequence_no for event in events),
            default=await self._event_store.get_latest_sequence(run_id=run_id),
        )
        return RuntimeEventReplayResponse(
            run_id=run_id,
            events=events,
            latest_sequence_no=latest_sequence_no,
            run_status=run.status,
            has_more=False,
        )

    async def list_run_surfaces(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> RunSurfacesResponse:
        """Return the SurfaceStore projection for a run (Generative Surfaces v2).

        Replays the run's full ledger (``list_events_after(after_sequence=0)``)
        and folds it into the surfaces the canvas hydrates from. Not flag-gated:
        it is additive and, with no v2 events, returns an empty list — harmless
        and honest. Scope check mirrors ``replay_events`` (404 on wrong-tenant or
        unknown run).
        """

        await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events = await self._event_store.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        state = SurfaceStoreProjection.fold(run_id, events)
        return RunSurfacesResponse(
            run_id=state.run_id,
            surfaces=state.surfaces,
            latest_sequence_no=state.latest_sequence_no,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _conversation_for_scope(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ):
        """Return the conversation or raise 404 if it falls outside the caller's scope."""
        conv = await self._persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conv is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return conv

    async def _with_latest_run(
        self,
        response: ConversationResponse,
        *,
        org_id: str,
    ) -> ConversationResponse:
        """Attach the active run status to a conversation response, if one exists."""
        active = await self._persistence.get_active_run_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        if active is None:
            return response
        return response.with_latest_run(
            # Guard against enum vs. string representation in older store adapters.
            status=active.status.value
            if hasattr(active.status, "value")
            else str(active.status),
            run_id=active.run_id,
        )

    async def _with_list_fields(
        self,
        response: ConversationResponse,
        *,
        org_id: str,
    ) -> ConversationResponse:
        """Attach the Chats-list ``preview`` + ``model`` projections (PRD-H.4).

        ``preview`` is the last visible message's text, trimmed to a short
        snippet; ``model`` is the latest run's model name (any status), so
        even a fully-completed conversation shows the model it last used.
        Both stay ``None`` when the conversation has no messages / runs.
        ``pinned`` needs no overlay — it rides along on the record.
        """

        latest_message = await self._persistence.get_latest_message_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        latest_run = await self._persistence.get_latest_run_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        preview = (
            self._snippet(latest_message.content_text)
            if latest_message is not None
            else None
        )
        model = latest_run.model_name if latest_run is not None else None
        # desktop-run-identity §D2 — surface the head run's id (any status) from
        # the SAME run row we already fetched for ``model``; previously discarded.
        latest_run_id_any_status = latest_run.run_id if latest_run is not None else None
        return response.with_list_fields(
            preview=preview,
            model=model,
            latest_run_id_any_status=latest_run_id_any_status,
        )

    @staticmethod
    def _snippet(text: str) -> str | None:
        """Collapse whitespace and trim a message body to a one-line preview.

        Returns ``None`` for empty/whitespace-only content so the row
        hides the preview rather than rendering a blank line.
        """

        collapsed = " ".join(text.split())
        if not collapsed:
            return None
        limit = Values.CONVERSATION_PREVIEW_MAX_LENGTH
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[: limit - 1].rstrip() + "…"

    async def _run_for_scope(self, *, org_id: str, user_id: str, run_id: str):
        """Return the run or raise 404 when it is absent or belongs to another user."""
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return run

    def _workspace_defaults(self):
        """Return a ``WorkspaceDefaultsService`` bound to this service's deps.

        Lazily imported to avoid a circular dependency at module load time.
        """
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self._persistence,
            settings=self._settings,
        )
