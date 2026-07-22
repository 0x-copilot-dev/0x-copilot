"""Conversation and message API schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, NonNegativeInt, PositiveInt, ValidationInfo, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.api.constants import Keys, Values
from agent_runtime.observability.redactor import JsonObjectCoercer
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.common import (
    ConversationStatus,
    MessageRole,
    MessageStatus,
)


# PR 1.2: per-chat connector scope override. Map of connector_id ->
# tuple of scope strings (active for this chat) or None (paused for this
# chat). Default empty dict means "no override; defer to inbound header
# or workspace defaults". See docs/new-design/pr-1-2-per-chat-connector-scope.md.
ConversationConnectorScopes = dict[str, tuple[str, ...] | None]


class _Fields:
    CONTENT_TEXT = "content_text"
    CONTENT_FORMAT = "content_format"
    PARENT_MESSAGE_ID = "parent_message_id"
    SOURCE_MESSAGE_ID = "source_message_id"
    BRANCH_ID = "branch_id"
    ENABLED_CONNECTORS = "enabled_connectors"
    SCOPES = "scopes"
    # PR 1.6
    FOLDER = "folder"
    TITLE = "title"
    ARCHIVED = "archived"


# PR 1.6 — UI cap. Strings longer than this in PATCH bodies bounce 422.
# 64 chars matches the design's sidebar truncation budget; longer
# folders ruin the visual rhythm of grouped chats.
FOLDER_MAX_LENGTH = 64
TITLE_MAX_LENGTH = 240


class CreateConversationRequest(RuntimeContract):
    """Request to create or idempotently resume a conversation shell."""

    org_id: str
    user_id: str
    assistant_id: str = Values.DEFAULT_ASSISTANT_ID
    title: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    idempotency_key: str | None = None
    # P6.5-A2 — when the caller files this conversation under a project
    # and does NOT pass an explicit ``enabled_connectors`` map, the
    # coordinator inherits the project's ``default_connector_allowlist``
    # (per projects-extensions-prd §5.4). Optional; absent on every
    # non-project chat. NOT validated against an existing project here
    # — the resolver tolerates missing / cross-tenant projects by
    # returning ``None`` so create never fails on a bad id.
    project_id: str | None = None
    # P6.5-A2 — explicit connector scopes passed by the caller. When
    # non-``None`` (including ``{}`` for "explicit empty"), the project
    # allowlist inheritance is skipped — caller wins (PRD §5.4 rule:
    # "Only when the caller did not pass an explicit connectors list").
    # The seed-from-workspace-defaults path also short-circuits when
    # this map is set, matching the existing
    # ``conversation.enabled_connectors`` semantics.
    enabled_connectors: ConversationConnectorScopes | None = None

    @field_validator(Keys.Field.ORG_ID, Keys.Field.USER_ID, Keys.Field.ASSISTANT_ID)
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.TITLE, mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_text(value, Keys.Field.TITLE)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator("project_id", mode="before")
    @classmethod
    def _normalize_project_id(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, "project_id")

    @field_validator(_Fields.ENABLED_CONNECTORS, mode="before")
    @classmethod
    def _coerce_enabled_connectors(
        cls, value: object
    ) -> ConversationConnectorScopes | None:
        # ``None`` (the default) means "caller did not pass an explicit
        # map"; the coordinator interprets that as eligible for project
        # / workspace inheritance. A passed-in ``{}`` is treated as
        # "explicit empty" — caller wins.
        if value is None:
            return None
        return ConnectorScopeValidator.coerce(value)

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)


class ConversationRecord(RuntimeContract):
    """Persisted conversation metadata."""

    conversation_id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    user_id: str
    assistant_id: str
    title: str | None = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    archived_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)
    schema_version: PositiveInt = Values.SCHEMA_VERSION
    idempotency_key: str | None = None
    enabled_connectors: ConversationConnectorScopes = Field(default_factory=dict)
    connectors_updated_at: datetime | None = None
    # PR 1.6 — conversation lifecycle (migration 0020).
    # ``deleted_at`` is the soft-delete tombstone consulted by
    # ``list_conversations`` (excluded by default) and reaped by the C8
    # retention sweeper once the resolved messages TTL elapses.
    # ``folder`` is a flat string label the sidebar groups by (no folder
    # table — folders are personal organisational labels in v1).
    # ``parent_conversation_id`` is forward-declared for Wave 6 fork
    # lineage; nullable + unset today.
    deleted_at: datetime | None = None
    folder: str | None = None
    parent_conversation_id: str | None = None
    # PRD-H.4 — first-class pin flag driving the Chats "Pinned" section.
    # Defaults False so rows created before migration 0034 (and every
    # non-pinned chat) fall to the Recent bucket. Toggled via the
    # dedicated ``POST /v1/agent/conversations/{id}/pin`` route.
    pinned: bool = False
    # PR 6.2 — fork lineage. Audit pointer to the share row that
    # authorised this conversation's creation. Non-FK so revoking the
    # share doesn't break the conversation. NULL on every non-forked row.
    forked_from_share_id: str | None = None
    # PR A3 — self-fork lineage. The message id this conversation was
    # forked from when the user picked "retry from here" / "fork to
    # new chat" on their own conversation. Mutually exclusive with
    # ``forked_from_share_id``. NULL on every non-self-fork row.
    forked_from_message_id: str | None = None

    @field_validator(
        Keys.Field.CONVERSATION_ID,
        Keys.Field.ORG_ID,
        Keys.Field.USER_ID,
        Keys.Field.ASSISTANT_ID,
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.IDEMPOTENCY_KEY, mode="before")
    @classmethod
    def _normalize_idempotency_key(cls, value: object) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, Keys.Field.IDEMPOTENCY_KEY)

    @field_validator(_Fields.ENABLED_CONNECTORS, mode="before")
    @classmethod
    def _normalize_enabled_connectors(
        cls, value: object
    ) -> ConversationConnectorScopes:
        return ConnectorScopeValidator.coerce(value)

    def runtime_connector_scopes(self) -> dict[str, tuple[str, ...]]:
        """Materialise the column into the shape ``AgentRuntimeContext`` expects.

        Drops paused connectors (``null`` value) so they're invisible to
        ``ToolPermissionChecker`` at run-start. Active connectors flow
        through verbatim.
        """

        return {
            connector: scopes
            for connector, scopes in self.enabled_connectors.items()
            if scopes is not None
        }

    def paused_connectors(self) -> frozenset[str]:
        """Server_ids the user explicitly paused for this conversation.

        Distinct from "absent" entries: a connector with ``null`` in
        ``enabled_connectors`` was set to that value via the per-chat
        popover and must be invisible to MCP loaders for the next run.
        Connectors not present at all defer to workspace defaults.
        """

        return frozenset(
            connector
            for connector, scopes in self.enabled_connectors.items()
            if scopes is None
        )

    def to_response(self) -> "ConversationResponse":
        """Return the stable public conversation shape."""

        return ConversationResponse(
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            user_id=self.user_id,
            assistant_id=self.assistant_id,
            title=self.title,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            archived_at=self.archived_at,
            metadata=self.metadata,
            schema_version=self.schema_version,
            enabled_connectors=self.enabled_connectors,
            connectors_updated_at=self.connectors_updated_at,
            deleted_at=self.deleted_at,
            folder=self.folder,
            parent_conversation_id=self.parent_conversation_id,
            pinned=self.pinned,
            forked_from_share_id=self.forked_from_share_id,
            forked_from_message_id=self.forked_from_message_id,
        )


class ConversationResponse(RuntimeContract):
    """Conversation metadata returned by the API."""

    conversation_id: str
    org_id: str
    user_id: str
    assistant_id: str
    title: str | None = None
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)
    schema_version: PositiveInt
    enabled_connectors: ConversationConnectorScopes = Field(default_factory=dict)
    connectors_updated_at: datetime | None = None
    # PR 1.6 — lifecycle additions; absent on rows pre-migration 0020 stay None.
    deleted_at: datetime | None = None
    folder: str | None = None
    parent_conversation_id: str | None = None
    # PRD-H.4 — first-class pin flag surfaced to the Chats list. Defaults
    # False so old clients + non-pinned rows compile/behave unchanged.
    pinned: bool = False
    # PRD-H.4 — Chats-list projections, populated once per page by
    # ``ConversationResponse.with_list_fields`` after the read (never
    # persisted on the record). ``preview`` is the last user/assistant
    # message snippet; ``model`` is the latest run's model name. Both
    # nullable so older clients + never-run conversations are unaffected.
    preview: str | None = None
    model: str | None = None
    # PR 6.2 — fork lineage; NULL on every non-forked row.
    forked_from_share_id: str | None = None
    # PR A3 — self-fork lineage; NULL on every non-self-fork row.
    forked_from_message_id: str | None = None
    # PR 2.2.1 — most-recent-run projection. Optional so older clients
    # that never call the projection helper compile against this schema
    # unchanged. The list endpoint populates them via
    # ``ConversationResponse.with_latest_run`` after the read.
    latest_run_status: str | None = None
    latest_run_id: str | None = None
    # desktop-run-identity §D2 — the most-recent run of ANY status. Unlike
    # ``latest_run_id`` (a non-terminal active run only; ``None`` once the run
    # completes), this carries the latest run regardless of status, so a client
    # reopening a FINISHED conversation can still resolve and bind its last run
    # (the durable O(1) "which run is this conversation's head" signal). The
    # list-fields projection populates it from the run already fetched for
    # ``model`` — no extra query. Optional so older clients are unaffected.
    latest_run_id_any_status: str | None = None

    def with_latest_run(
        self,
        *,
        status: str | None,
        run_id: str | None,
    ) -> "ConversationResponse":
        """Return a copy with the most-recent-run projection populated.

        Kept as a typed copy method (not field mutation) so consumers that
        receive the response from elsewhere always observe immutable
        Pydantic instances. The list endpoint resolves the latest run
        once per page and overlays it via this method.
        """

        return self.model_copy(
            update={
                "latest_run_status": status,
                "latest_run_id": run_id,
            }
        )

    def with_list_fields(
        self,
        *,
        preview: str | None,
        model: str | None,
        latest_run_id_any_status: str | None = None,
    ) -> "ConversationResponse":
        """Return a copy carrying the Chats-list ``preview`` + ``model`` + head-run projections.

        Kept as a typed copy (not field mutation) for the same immutability
        reason as :meth:`with_latest_run`. The list endpoint resolves the
        last-message snippet + latest-run (any status) once per row and overlays
        them via this method; ``pinned`` already rides along on the record's
        ``to_response`` so it needs no overlay. ``latest_run_id_any_status``
        (desktop-run-identity §D2) is the latest run's id regardless of status —
        the same run row whose ``model_name`` feeds ``model`` — so a finished
        conversation still hands the client a run id to bind on reopen.
        """

        return self.model_copy(
            update={
                "preview": preview,
                "model": model,
                "latest_run_id_any_status": latest_run_id_any_status,
            }
        )


class PinConversationRequest(RuntimeContract):
    """Body for ``POST /v1/agent/conversations/{id}/pin`` (PRD-H.4).

    A single route handles both pin and unpin: ``pinned`` defaults to
    ``True`` so a bare POST pins, and clients send ``{"pinned": false}``
    to unpin. Idempotent — re-pinning an already-pinned chat is a no-op
    that still returns the current row.
    """

    pinned: bool = True


class ConversationListResponse(RuntimeContract):
    """Paginated conversation metadata for a caller scope."""

    conversations: tuple[ConversationResponse, ...]
    next_cursor: str | None = None
    has_more: bool = False


class ConnectorScopeValidator:
    """Shape-only validation + normalization for connector scope payloads.

    We deliberately do NOT validate connector ids or scope strings against
    the live tool registry: registries are loaded per-run on the worker,
    not on the API service, and a connector that was valid at PATCH time
    may be removed before the next run executes. ``ToolPermissionChecker``
    enforces semantics at run-start; this layer enforces shape only.
    """

    @classmethod
    def coerce(cls, value: object) -> ConversationConnectorScopes:
        """Coerce an untyped connector-scopes dict into the canonical shape, normalising ids and scope lists."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("scopes must be an object")
        normalized: ConversationConnectorScopes = {}
        for raw_key, raw_value in value.items():
            connector_id = cls._coerce_connector_id(raw_key)
            normalized[connector_id] = cls._coerce_scopes(connector_id, raw_value)
        return normalized

    @staticmethod
    def _coerce_connector_id(value: object) -> str:
        """Validate and strip a connector id string."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("connector id must be a non-empty string")
        return value.strip()

    @staticmethod
    def _coerce_scopes(connector_id: str, value: object) -> tuple[str, ...] | None:
        """Coerce a per-connector scope list or null; raises on unexpected shape."""
        if value is None:
            return None
        if isinstance(value, list | tuple):
            scopes: list[str] = []
            for scope in value:
                if not isinstance(scope, str) or not scope.strip():
                    raise ValueError(
                        f"scopes for {connector_id} must be non-empty strings"
                    )
                scopes.append(scope.strip())
            return tuple(scopes)
        raise ValueError(f"scopes for {connector_id} must be a list or null")


class UpdateConversationConnectorsRequest(RuntimeContract):
    """RFC 7396 merge-patch body for per-chat connector scope toggles.

    Send only the connectors you are changing:
      - ``[scope, ...]`` activates the connector for this chat with these scopes,
      - ``null`` pauses the connector for this chat (still installed/connected,
        just inert here),
      - omitting a key leaves it untouched.
    """

    scopes: ConversationConnectorScopes = Field(default_factory=dict)

    @field_validator(_Fields.SCOPES, mode="before")
    @classmethod
    def _coerce_scopes(cls, value: object) -> ConversationConnectorScopes:
        return ConnectorScopeValidator.coerce(value)


class ConversationConnectorScopesResponse(RuntimeContract):
    """Effective per-chat connector scope map after an update or read."""

    conversation_id: str
    scopes: ConversationConnectorScopes = Field(default_factory=dict)
    updated_at: datetime | None = None


class UpdateConversationRequest(RuntimeContract):
    """RFC 7396 merge-patch body for ``PATCH /v1/agent/conversations/{id}``
    (PR 1.6).

    All fields optional; omit a field to leave it untouched. Sending
    ``null`` clears (folder/title) or un-archives (``archived: false``).
    The service writes one ``conversation.update`` audit row per call
    with the before/after diff.
    """

    title: str | None = None
    folder: str | None = None
    archived: bool | None = None
    # Pydantic distinguishes "omitted" from "explicit null" via
    # ``model_dump(exclude_unset=True)`` — the service uses that to
    # decide which columns to UPDATE. We carry an internal sentinel set
    # so adapters know the patch's intent without reflecting on the
    # Pydantic model from the persistence layer.

    @field_validator(_Fields.TITLE, mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = ValueNormalizer.normalize_optional_text(value, _Fields.TITLE)
        if normalized is not None and len(normalized) > TITLE_MAX_LENGTH:
            raise ValueError(f"title must be at most {TITLE_MAX_LENGTH} characters")
        return normalized

    @field_validator(_Fields.FOLDER, mode="before")
    @classmethod
    def _normalize_folder(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("folder must be a string or null")
        stripped = value.strip()
        if not stripped:
            # Empty string treated as null (clear folder).
            return None
        if len(stripped) > FOLDER_MAX_LENGTH:
            raise ValueError(f"folder must be at most {FOLDER_MAX_LENGTH} characters")
        return stripped


class MessageRecord(RuntimeContract):
    """Persisted conversation message."""

    message_id: str = Field(default_factory=lambda: uuid4().hex)
    conversation_id: str
    org_id: str
    run_id: str | None = None
    role: MessageRole
    content_text: str
    content_format: str = Values.DEFAULT_CONTENT_FORMAT
    content: tuple[JsonObject, ...] = ()
    attachments: tuple[JsonObject, ...] = ()
    quote: JsonObject | None = None
    metadata: JsonObject = Field(default_factory=dict)
    parent_message_id: str | None = None
    source_message_id: str | None = None
    branch_id: str | None = None
    token_count: NonNegativeInt | None = None
    trace_id: str | None = None
    status: MessageStatus = MessageStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    edited_at: datetime | None = None
    deleted_at: datetime | None = None

    @field_validator(
        Keys.Field.MESSAGE_ID,
        Keys.Field.CONVERSATION_ID,
        Keys.Field.ORG_ID,
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(
        Keys.Field.RUN_ID,
        _Fields.PARENT_MESSAGE_ID,
        _Fields.SOURCE_MESSAGE_ID,
        _Fields.BRANCH_ID,
        Keys.Field.TRACE_ID,
        mode="before",
    )
    @classmethod
    def _normalize_optional_ids(cls, value: object, info: ValidationInfo) -> str | None:
        return ValueNormalizer.normalize_optional_id(value, info.field_name)

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)

    @field_validator(_Fields.CONTENT_TEXT, _Fields.CONTENT_FORMAT)
    @classmethod
    def _normalize_text(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_nonempty_string(value, info.field_name)

    def to_response(self) -> "MessageResponse":
        """Return the stable public message shape."""

        return MessageResponse(
            message_id=self.message_id,
            conversation_id=self.conversation_id,
            org_id=self.org_id,
            run_id=self.run_id,
            role=self.role,
            content_text=self.content_text,
            content_format=self.content_format,
            content=self.content,
            attachments=self.attachments,
            quote=self.quote,
            metadata=self.metadata,
            parent_message_id=self.parent_message_id,
            source_message_id=self.source_message_id,
            branch_id=self.branch_id,
            token_count=self.token_count,
            trace_id=self.trace_id,
            status=self.status,
            created_at=self.created_at,
            edited_at=self.edited_at,
            deleted_at=self.deleted_at,
        )


class MessageResponse(RuntimeContract):
    """Conversation message returned to clients."""

    message_id: str
    conversation_id: str
    org_id: str
    run_id: str | None = None
    role: MessageRole
    content_text: str
    content_format: str
    content: tuple[JsonObject, ...] = ()
    attachments: tuple[JsonObject, ...] = ()
    quote: JsonObject | None = None
    metadata: JsonObject = Field(default_factory=dict)
    parent_message_id: str | None = None
    source_message_id: str | None = None
    branch_id: str | None = None
    token_count: NonNegativeInt | None = None
    trace_id: str | None = None
    status: MessageStatus
    created_at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None


class MessageListResponse(RuntimeContract):
    """Paginated conversation messages."""

    conversation_id: str
    messages: tuple[MessageResponse, ...]
    next_cursor: str | None = None
    has_more: bool = False


class HistoryDeletionResponse(RuntimeContract):
    """Audit-safe result for deleting a user's visible runtime history."""

    org_id: str
    user_id: str
    conversations_archived: NonNegativeInt = 0
    messages_tombstoned: NonNegativeInt = 0
    runs_cancelled: NonNegativeInt = 0
    events_retained: NonNegativeInt = 0
    audit_event_id: str | None = None


# ---------------------------------------------------------------------------
# Conversation context (B5 — `/context` slash command).
#
# Joins the latest run-level usage row (B1) with the per-call rows (B2),
# the compression event log, and the model's pricing context window (B3).
# Server returns integer ``headroom_pct`` so the UI never re-derives it.
# ---------------------------------------------------------------------------


class ContextWindowSummary(RuntimeContract):
    """Model + context-window descriptor for the latest run."""

    provider: str
    name: str
    context_window_tokens: NonNegativeInt | None = None  # None = model not in pricing


class ContextCurrentSlice(RuntimeContract):
    """Token state for the latest completed run in the conversation."""

    last_run_id: str | None = None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    available_tokens: NonNegativeInt | None = None
    headroom_pct: int | None = Field(default=None, ge=0, le=100)


class ContextCallRow(RuntimeContract):
    """One LLM call inside ``ContextBreakdown.by_call``."""

    event_id: str
    model_name: str
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    task_id: str | None = None


class ContextSubagentRow(RuntimeContract):
    """One subagent inside ``ContextBreakdown.by_subagent``."""

    subagent_id: str
    name: str
    total: NonNegativeInt = 0
    call_count: NonNegativeInt = 0


class ContextCompressionRow(RuntimeContract):
    """One context compression event for the run."""

    before: NonNegativeInt
    after: NonNegativeInt
    strategy: str
    at: datetime


class ContextBreakdown(RuntimeContract):
    """Per-call, per-subagent, and compression-event breakdown."""

    by_call: tuple[ContextCallRow, ...] = ()
    by_subagent: tuple[ContextSubagentRow, ...] = ()
    compression_events: tuple[ContextCompressionRow, ...] = ()


class ConversationContextResponse(RuntimeContract):
    """Response shape for ``GET /v1/agent/conversations/{id}/context``."""

    model: ContextWindowSummary
    current: ContextCurrentSlice
    breakdown: ContextBreakdown
