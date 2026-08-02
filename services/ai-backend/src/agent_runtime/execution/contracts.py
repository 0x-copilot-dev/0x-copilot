"""Pydantic contracts for the runtime foundation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, runtime_checkable
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassDecision,
)
from agent_runtime.execution.ports import (
    McpRegistry,
    MemoryBackendFactory,
    SubagentCatalog,
    ToolRegistry,
)
from agent_runtime.observability.constants import Keys as ObservabilityKeys
from agent_runtime.observability.redactor import JsonObjectCoercer
from agent_runtime.observability.tracing import TraceContext
from agent_runtime.capabilities.skills.sources import SkillSourceConfig

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime.
    from agent_runtime.capabilities.discovery.activation import (
        CapabilityExpansionLimits,
    )
    from agent_runtime.capabilities.discovery.contracts import (
        CapabilityReferenceMinter,
    )
    from agent_runtime.capabilities.discovery.revision_authority import (
        CapabilityRefRevalidation,
    )
    from agent_runtime.capabilities.discovery.schema_artifacts import (
        RunScopedSchemaArtifactPublisher,
    )
    from agent_runtime.capabilities.discovery.telemetry import (
        CapabilityDiscoveryObserver,
        CapabilityExpansionObserver,
    )

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | dict[str, object] | list[object]
JsonObject: TypeAlias = dict[str, JsonValue]


class RuntimeContract(BaseModel):
    """Immutable, strictly-validated Pydantic base for all runtime domain models."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class FeatureFlag(StrEnum):
    """Optional capability gates that can be enabled per request context."""

    DYNAMIC_TOOL_LOADING = "dynamic_tool_loading"
    SKILLS_MIDDLEWARE = "skills_middleware"
    DYNAMIC_MCP_LOADING = "dynamic_mcp_loading"
    CONTEXT_MEMORY = "context_memory"
    SUBAGENTS = "subagents"
    STREAMING_OBSERVABILITY = "streaming_observability"


class RuntimeErrorCode(StrEnum):
    """Typed error classes safe for API and stream surfaces."""

    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    CAPABILITY_NOT_FOUND = "capability_not_found"
    CAPABILITY_LOAD_ERROR = "capability_load_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    CONFIGURATION_ERROR = "configuration_error"
    DEPENDENCY_ERROR = "dependency_error"
    RUNTIME_FACTORY_ERROR = "runtime_factory_error"
    RUN_WORKER_LOST = "run_worker_lost"


class RuntimeRunStatus(StrEnum):
    """Product-visible status for request-scoped runtime runs."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StreamEventSource(StrEnum):
    """Runtime subsystem that produced a stream event."""

    MAIN_AGENT = "main_agent"
    SUBAGENT = "subagent"
    TOOL = "tool"
    MCP = "mcp"
    SUMMARIZATION = "summarization"
    SYSTEM = "system"
    RUNTIME = "runtime"
    MODEL = "model"


class StreamEventType(StrEnum):
    """User-safe event types emitted by the runtime."""

    PROGRESS = "progress"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CUSTOM = "custom"
    LIFECYCLE = "lifecycle"
    SUBAGENT_UPDATE = "subagent_update"
    OBSERVATION = "observation"
    ERROR = "error"
    FINAL = "final"
    FINAL_RESPONSE = "final_response"


StreamSource = StreamEventSource


class ModelReasoningEffort(StrEnum):
    """Provider-neutral reasoning effort controls.

    Ordered cheapest-first. No model advertises every rung — ``minimal`` is an
    older GPT-5 spelling and ``max`` arrived with GPT-5.6 — so the valid set is
    a per-model property carried on ``CatalogModelRecord.reasoning_efforts``,
    never a constant. This enum is the union of every rung we can *express*;
    the catalog says which ones a given model will *accept*.
    """

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ModelReasoningSummary(StrEnum):
    """OpenAI reasoning summary output controls."""

    AUTO = "auto"
    CONCISE = "concise"
    DETAILED = "detailed"


class ModelReasoningDisplay(StrEnum):
    """Anthropic thinking display controls."""

    OMITTED = "omitted"
    SUMMARIZED = "summarized"


class ModelThinkingMode(StrEnum):
    """Anthropic thinking mode controls."""

    ENABLED = "enabled"
    ADAPTIVE = "adaptive"


class ModelReasoningConfig(RuntimeContract):
    """Provider-neutral reasoning and thinking controls."""

    enabled: bool = True
    effort: ModelReasoningEffort | None = None
    summary: ModelReasoningSummary | None = None
    display: ModelReasoningDisplay | None = None
    budget_tokens: PositiveInt | None = Field(default=None, le=2_000_000)
    include_encrypted_content: bool = False
    thinking_mode: ModelThinkingMode | None = None

    @model_validator(mode="after")
    def _validate_thinking_budget(self) -> "ModelReasoningConfig":
        if (
            self.thinking_mode is ModelThinkingMode.ADAPTIVE
            and self.budget_tokens is not None
        ):
            msg = "budget_tokens cannot be set when thinking_mode is adaptive"
            raise ValueError(msg)
        return self


class ModelConfig(RuntimeContract):
    """Model settings selected before the runtime is constructed.

    ``max_output_tokens`` and ``tool_call_budget`` are post-mapping fields:
    they may be scaled by :class:`~agent_runtime.execution.depth.DepthBudgetTable`
    when the request carries a ``reasoning_depth``. Defaults preserve the
    behaviour from before depth wiring landed.
    """

    provider: str
    model_name: str = Field(min_length=1, max_length=200)
    max_input_tokens: PositiveInt = Field(le=2_000_000)
    # Optional baseline cap on completion tokens. ``None`` means "let the
    # provider apply its default"; depth multipliers only scale a numeric
    # baseline — they never invent a value. The budgets estimator already
    # tolerated this field via ``getattr``; declaring it here makes the
    # contract explicit and the wire shape stable.
    max_output_tokens: PositiveInt | None = Field(default=None, le=2_000_000)
    timeout_seconds: float = Field(gt=0, le=600)
    temperature: float = Field(ge=0, le=2)
    supports_streaming: bool = True
    reasoning: ModelReasoningConfig | None = None
    # Per-run cap on repeat invocations of any single tool, scaled by
    # reasoning depth. The default MUST equal the enforced seed cap
    # ``DefaultToolBudget.MAX_CALLS_PER_RUN`` (10) — the number
    # ``ToolBudgetMiddleware`` actually admits against — so the budget the
    # model is told never undershoots what is enforced. It was ``5`` while the
    # middleware enforced ``10``; T0.3 reconciled them. Held as a literal, not
    # an import, because ``tool_budgets`` imports this module for
    # ``RuntimeContract`` and importing it back would cycle; the SSOT gate in
    # ``tests/unit/architecture/test_named_default_ssot.py`` fails if this
    # literal drifts from the enforced value.
    tool_call_budget: PositiveInt = Field(default=10, le=100)
    # The depth selection that produced this config, when one was supplied.
    # Stored as a string so it round-trips cleanly through ``model_dump``
    # / persistence without importing the enum here (which would create
    # a cycle with the depth module). Validated downstream where used.
    reasoning_depth: str | None = Field(default=None, max_length=32)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _normalize_slug(value, "provider")

    @field_validator("model_name")
    @classmethod
    def _normalize_model_name(cls, value: str) -> str:
        normalized = _normalize_nonempty_string(value, "model_name")
        if len(normalized) > 200:
            msg = "model_name must be at most 200 characters"
            raise ValueError(msg)
        return normalized


class RuntimeRunContext(RuntimeContract):
    """Product-owned IDs propagated through LangGraph, traces, and logs."""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_trace_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonObject = Field(default_factory=dict)

    @field_validator("request_id", "run_id", "trace_id", mode="before")
    @classmethod
    def _normalize_required_id(cls, value: object, info: ValidationInfo) -> str:
        return _normalize_runtime_id(value, info.field_name)

    @field_validator("parent_trace_id", mode="before")
    @classmethod
    def _normalize_optional_parent_trace_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_id(value, "parent_trace_id")

    @field_validator("metadata", mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)  # type: ignore[return-value]


class RuntimeRunHandle(RuntimeContract):
    """Small response returned once a product-owned run has been created."""

    request_id: str
    run_id: str
    trace_id: str
    status: RuntimeRunStatus = RuntimeRunStatus.ACCEPTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("request_id", "run_id", "trace_id")
    @classmethod
    def _normalize_handle_id(cls, value: object, info: ValidationInfo) -> str:
        return _normalize_runtime_id(value, info.field_name)

    @classmethod
    def from_context(
        cls,
        context: "AgentRuntimeContext",
        *,
        status: RuntimeRunStatus = RuntimeRunStatus.ACCEPTED,
    ) -> "RuntimeRunHandle":
        return cls(
            request_id=context.request_id,
            run_id=context.run_id,
            trace_id=context.trace_id,
            status=status,
            created_at=context.started_at,
        )


class CatalogSuggestionCard(RuntimeContract):
    """One uninstalled catalog connector that the agent may suggest to the user.

    Materialised at run-create from ``backend.McpRegistryService.list_suggestible_connectors``
    so the system prompt and discovery tool see the same fields without re-parsing
    the wire payload.
    """

    slug: str
    display_name: str
    description: str = ""
    scopes_summary: str | None = None
    brand_color: str | None = None
    # When True, install requires the user to paste a pre-registered OAuth client
    # (vendor doesn't expose RFC 8414 / RFC 7591). The FE routes Connect to the
    # credentials form instead of the 1-click install + redirect flow.
    requires_pre_registered_client: bool = False

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str) -> str:
        normalized = _normalize_nonempty_string(value, "slug").lower()
        if not normalized:
            msg = "slug must be a non-empty string"
            raise ValueError(msg)
        return normalized

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: str) -> str:
        return _normalize_nonempty_string(value, "display_name")


class ConnectorAccessMode(StrEnum):
    """Per-connector agent access mode (PRD-06).

    Byte-identical value set to the backend's ``ConnectorAccessMode`` and the
    api-types union — the ai-backend can't import the backend's enum across
    the deployable boundary, so it mirrors the three values. ``off`` is the
    only value the runtime re-check denies; ``read`` / ``read_act`` are both
    authorized at the card-visibility gate (the ``read`` write restriction is
    enforced authoritatively backend-side at ``proxy_internal_rpc``).
    """

    READ = "read"
    READ_ACT = "read_act"
    OFF = "off"


class AgentRuntimeContext(RuntimeContract):
    """Immutable, fully-validated identity, authorization, model, and trace context for one request."""

    user_id: str
    org_id: str
    roles: frozenset[str]
    permission_scopes: frozenset[str] = Field(default_factory=frozenset)
    connector_scopes: dict[str, frozenset[str]] = Field(default_factory=dict)
    # Connectors paused by the user via the per-chat toggle. Kept separate from
    # ``connector_scopes`` because an empty scopes mapping is ambiguous between
    # "no override" and "everything paused". A paused server is invisible,
    # unloadable, and uncallable for the duration of this run.
    paused_connectors: frozenset[str] = Field(default_factory=frozenset)
    # Per-connector durable access mode, keyed by the MCP ``server_id`` (PRD-06
    # D3b). Populated server-side at run-create from the ``/internal/v1/mcp/
    # cards`` response (each card now carries ``access_mode``). An entry equal
    # to ``off`` makes the server unauthorized for the whole run — the
    # defense-in-depth mirror of ``paused_connectors``, catching a stale tool
    # reference from an earlier turn. A missing key means "no per-connector
    # gate" (authorized). The authoritative, immediately-effective boundary is
    # the backend ``proxy_internal_rpc`` gate; this is the frozen snapshot.
    connector_access_modes: dict[str, ConnectorAccessMode] = Field(default_factory=dict)
    # Uninstalled catalog connectors the agent may suggest this run. Filtered
    # server-side to exclude paused, user-muted, and non-discoverable entries.
    # An empty tuple means the system prompt omits the suggestions section.
    suggested_connectors: tuple["CatalogSuggestionCard", ...] = Field(
        default_factory=tuple
    )
    model_profile: ModelConfig
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    parent_trace_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    max_parallel_tasks: PositiveInt = Field(default=4, le=100)
    trace_metadata: JsonObject = Field(default_factory=dict)
    feature_flags: frozenset[FeatureFlag] = Field(default_factory=frozenset)
    # Per-run web-search toggle (composer Tools popover). Default True preserves
    # the historic always-on behavior — when the user turns web search off for a
    # turn the ``WebSearchToolRegistry`` omits the ``web_search`` tool for that
    # run only. Purely a capability filter; nothing else keys off it.
    web_search_enabled: bool = True
    # Sealed filesystem-bypass decision for this run (PRD-FS-10 §4.3). Folded
    # ONCE at run-create from the workspace master switch plus the composer's
    # run/message selection, then persisted verbatim in ``agent_runs`` so a
    # worker re-claim or an approval resume reads the same answer rather than
    # re-deriving it from state that may have moved. The default is the
    # fail-closed one: master off, manual, nothing offered — which is what every
    # non-desktop run and every run created before this field existed resolves
    # to. It only ever removes the approval PAUSE on the staged C3 -> ledger ->
    # C2 lane; it can neither widen a grant nor authorize a direct host write.
    filesystem_bypass: FilesystemBypassDecision = Field(
        default_factory=lambda: MANUAL_FILESYSTEM_BYPASS
    )
    # Workspace-level policy knobs (e.g. training opt-out, behavior overrides)
    # resolved at run-create and persisted verbatim in ``agent_runs``. Stored as
    # a generic ``JsonObject`` rather than a typed model to avoid importing
    # ``runtime_api`` schemas, which would invert the layering. Consumers
    # downcast via ``WorkspaceBehaviorOverrides.model_validate``.
    workspace_behavior_overrides: JsonObject = Field(default_factory=dict)
    # Per-(org, user) policies resolved at run-create from the backend aggregate
    # route. Empty dict means "use deployment defaults." Consumers downcast to
    # ``ToolUsePolicySnapshot`` / ``PrivacySettingsSnapshot`` as needed.
    # SECURITY INVARIANT: this dict is persisted verbatim (run records,
    # outbox payloads) — it must NEVER contain provider API keys. Keys are
    # split out into ``provider_keys`` below before the context is sealed.
    user_policies_json: JsonObject = Field(default_factory=dict)
    # Per-user BYOK provider API keys (normalized provider slug -> plaintext
    # key), resolved at run-create and re-hydrated by the worker at claim
    # time. In-memory only: ``exclude=True`` keeps the mapping out of every
    # ``model_dump`` / ``model_dump_json`` surface (``agent_runs.runtime_context_json``,
    # queue/outbox payloads, events), and ``repr=False`` keeps it out of
    # tracebacks and logs — mirroring ``ProviderSettings.api_key``.
    provider_keys: dict[str, str] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    # Per-user custom OpenAI-compatible endpoint base URLs (normalized provider
    # slug -> base_url), resolved at run-create from the backend aggregate
    # (BYOK decision D-2). NON-secret — a base_url is not key material — so
    # (unlike ``provider_keys``) it is persisted verbatim in the run record and
    # survives the queue round-trip WITHOUT re-hydration. Consumed in
    # ``user_policy_model_kwargs`` to inject ``base_url`` for the custom slug.
    provider_endpoints: dict[str, str] = Field(default_factory=dict)

    @field_validator("user_id", "org_id")
    @classmethod
    def _normalize_identity(cls, value: str, info: ValidationInfo) -> str:
        normalized = _normalize_nonempty_string(value, info.field_name)
        if not _ID_PATTERN.fullmatch(normalized):
            msg = f"{info.field_name} contains unsupported characters"
            raise ValueError(msg)
        return normalized

    @field_validator("roles", mode="before")
    @classmethod
    def _normalize_roles(cls, value: object) -> frozenset[str]:
        return _normalize_slug_set(value, "roles", require_non_empty=True)

    @field_validator("permission_scopes", mode="before")
    @classmethod
    def _normalize_permission_scopes(cls, value: object) -> frozenset[str]:
        return _normalize_scope_set(value, "permission_scopes")

    @field_validator("connector_scopes", mode="before")
    @classmethod
    def _normalize_connector_scopes(cls, value: object) -> dict[str, frozenset[str]]:
        # Connector ids (e.g. ``"seed:linear"``) accept any non-empty trimmed string;
        # the strict slug pattern would reject the colon. Lowercase + trim to preserve
        # historic case-insensitive semantics without blocking catalog-seeded servers.
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = "connector_scopes must be a mapping of connector id to scopes"
            raise ValueError(msg)

        normalized: dict[str, frozenset[str]] = {}
        for connector, scopes in value.items():
            if not isinstance(connector, str) or not connector.strip():
                msg = "connector_scopes keys must be non-empty strings"
                raise ValueError(msg)
            connector_id = connector.strip().lower()
            normalized[connector_id] = _normalize_scope_set(
                scopes,
                f"connector_scopes.{connector_id}",
            )
        return normalized

    @field_validator("suggested_connectors", mode="before")
    @classmethod
    def _normalize_suggested_connectors(
        cls, value: object
    ) -> tuple["CatalogSuggestionCard", ...]:
        # Accept either a sequence of ``CatalogSuggestionCard`` (Python
        # caller path) or a sequence of dicts (HTTP-deserialised path).
        # ``None`` collapses to empty so older request shapes still
        # validate.
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            msg = "suggested_connectors must be an iterable of cards"
            raise ValueError(msg)
        try:
            iterable = list(value)  # type: ignore[arg-type]
        except TypeError as exc:
            msg = "suggested_connectors must be an iterable of cards"
            raise ValueError(msg) from exc
        cards: list[CatalogSuggestionCard] = []
        for item in iterable:
            if isinstance(item, CatalogSuggestionCard):
                cards.append(item)
            elif isinstance(item, Mapping):
                cards.append(CatalogSuggestionCard.model_validate(dict(item)))
            else:
                msg = "suggested_connectors items must be cards or dicts"
                raise ValueError(msg)
        return tuple(cards)

    @field_validator("paused_connectors", mode="before")
    @classmethod
    def _normalize_paused_connectors(cls, value: object) -> frozenset[str]:
        # Accept the same connector-id shape as ``connector_scopes`` (any
        # non-empty trimmed string, including the ``seed:`` prefix). Existence
        # validation belongs to the runtime registry, not this contract.
        if value is None:
            return frozenset()
        if isinstance(value, (str, bytes)):
            msg = "paused_connectors must be an iterable of connector ids"
            raise ValueError(msg)
        try:
            iterable = list(value)  # type: ignore[arg-type]
        except TypeError as exc:
            msg = "paused_connectors must be an iterable of connector ids"
            raise ValueError(msg) from exc
        normalized: list[str] = []
        for item in iterable:
            if not isinstance(item, str) or not item.strip():
                msg = "paused_connectors items must be non-empty strings"
                raise ValueError(msg)
            normalized.append(item.strip())
        return frozenset(normalized)

    @field_validator("connector_access_modes", mode="before")
    @classmethod
    def _normalize_connector_access_modes(
        cls, value: object
    ) -> dict[str, ConnectorAccessMode]:
        # Accept a mapping of connector/server id -> access-mode string (the
        # ``/internal/v1/mcp/cards`` shape). Keys mirror ``paused_connectors``
        # (non-empty trimmed strings); values coerce into the enum so an
        # unknown mode fails loudly rather than silently un-gating.
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = "connector_access_modes must be a mapping of connector id to mode"
            raise ValueError(msg)
        normalized: dict[str, ConnectorAccessMode] = {}
        for connector_id, mode in value.items():
            if not isinstance(connector_id, str) or not connector_id.strip():
                msg = "connector_access_modes keys must be non-empty strings"
                raise ValueError(msg)
            normalized[connector_id.strip()] = ConnectorAccessMode(mode)
        return normalized

    @field_validator("provider_keys", mode="before")
    @classmethod
    def _normalize_provider_keys(cls, value: object) -> dict[str, str]:
        # Error messages here must never include the mapping's values —
        # they are plaintext credentials.
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            msg = "provider_keys must be a mapping of provider slug to API key"
            raise ValueError(msg)
        normalized: dict[str, str] = {}
        for provider, key in value.items():
            if not isinstance(provider, str) or not provider.strip():
                msg = "provider_keys keys must be non-empty provider slugs"
                raise ValueError(msg)
            if not isinstance(key, str) or not key.strip():
                msg = "provider_keys values must be non-empty strings"
                raise ValueError(msg)
            normalized[provider.strip().lower()] = key.strip()
        return normalized

    @field_validator("request_id", "run_id", "trace_id", mode="before")
    @classmethod
    def _normalize_runtime_identifier(cls, value: object, info: ValidationInfo) -> str:
        return _normalize_runtime_id(value, info.field_name)

    @field_validator("parent_trace_id", mode="before")
    @classmethod
    def _normalize_parent_trace_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return _normalize_runtime_id(value, "parent_trace_id")

    @field_validator("trace_metadata", mode="before")
    @classmethod
    def _redact_trace_metadata(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)  # type: ignore[return-value]

    @property
    def run_context(self) -> RuntimeRunContext:
        """Return the request/run ID bundle used by graph config and observability."""

        return RuntimeRunContext(
            request_id=self.request_id,
            run_id=self.run_id,
            trace_id=self.trace_id,
            parent_trace_id=self.parent_trace_id,
            started_at=self.started_at,
            metadata=self.trace_metadata,
        )


@dataclass(frozen=True, slots=True)
class CapabilityBridgeComposition:
    """The F3 invocation-seam inputs only a composition root can supply.

    The bridge is built from two halves that live on opposite sides of this
    module.  The runtime factory owns the *model-framework* half — it is the one
    place a run's :class:`~agent_runtime.capabilities.mcp.loader.McpLoader` and
    its single :class:`~agent_runtime.capabilities.mcp.tools.CallMcpTool` exist,
    and reusing those instances is what keeps F3 a reuse of the one MCP dispatch
    route rather than a second one.  The worker owns the *run-scoped* half
    collected here: none of it is derivable inside the factory, and none of it
    is derivable inside the discovery package either.

    Every field is independently optional and every one of them fails dark:

    * no ``minter`` means no second-tier expansion, so the bridge registers the
      catalog-only search it registered before;
    * no ``revalidation`` means ``invoke_capability`` is not registered at all,
      because a reference that cannot be revalidated at use time must never be
      offered; and
    * no ``observer`` or ``schema_artifacts`` changes nothing the bridge *does* —
      the run is merely unmeasured, and an over-bound schema is reported
      ``unavailable`` rather than deferred to an artifact.

    ``minter`` carries the load-bearing invariant.  It must be keyed exactly as
    the :class:`~agent_runtime.capabilities.discovery.builder.AuthorizedCatalogBuilder`
    that projected the catalog beside it was, because expansion mints references
    for that catalog's own id — a different key would mint references the run's
    catalog identity cannot explain.  Supplying the builder's *own* minter object
    rather than a second one built from the same bytes is how the composition
    root makes that unrepresentable rather than merely true.

    The annotations resolve only under ``TYPE_CHECKING``: the discovery contracts
    import this module, so a runtime import here would close a cycle and would
    also drag the discovery package onto the dark path's import graph.
    """

    minter: CapabilityReferenceMinter | None = None
    revalidation: CapabilityRefRevalidation | None = None
    observer: CapabilityDiscoveryObserver | None = None
    expansion_observer: CapabilityExpansionObserver | None = None
    schema_artifacts: RunScopedSchemaArtifactPublisher | None = None
    expansion_limits: CapabilityExpansionLimits | None = None


@runtime_checkable
class RuntimeBatchAdmissionPort(Protocol):
    """The graph tool seam's one route into an F6 batch, expressed without F6.

    :class:`~agent_runtime.control_plane.parallel_admission.ParallelAdmissionPort`
    already carries the half of this seam that decides a call's *width*. It is
    deliberately not enough on its own: knowing that three calls may overlap
    does not make anything plan them, journal them, permit them, or stop them,
    and F6 owns all four. This port carries the other half — the two moments the
    graph is the only thing that can tell F6 about.

    Its signatures name no F6 type at all, which is the whole reason it lives
    here rather than in ``capabilities.concurrency``. The middleware that
    consumes it must not import the concurrency package: a dark deployment's
    import graph has to stay byte-identical to the pre-F6 one, and an import for
    a type annotation would break that as surely as an import for a call. So the
    protocol speaks only in provider tool-call ids and an awaitable body, and the
    capabilities side implements it.

    Both methods are total. Neither may raise into a healthy run, and neither may
    change what a call *is* — only whether, and alongside what, it runs.
    """

    async def aplan_model_batch(
        self,
        *,
        execution_scope: str,
        model_turn: int,
        tool_calls: Sequence[Mapping[str, Any]],
    ) -> None:
        """Durably record the ordering for one model turn's tool calls.

        Awaited from ``aafter_model``, which the framework runs to completion
        before it routes to the tool node — so a plan is durable before any child
        it names can dispatch, by graph topology rather than by a lock.
        """

    async def arun_tool_body(
        self,
        *,
        tool_call_id: str,
        body: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run one graph tool body, through F6 when it is a planned child.

        ``tool_call_id`` that names no planned child must await ``body`` exactly
        as the caller would have — the unknown case is the unchanged case, not a
        refusal — which is what keeps "unknown means serial" a property of the
        composition rather than a branch each caller remembers.
        """


_CURRENT_BATCH_ADMISSION: ContextVar[RuntimeBatchAdmissionPort | None] = ContextVar(
    "agent_runtime_batch_admission",
    default=None,
)


class RuntimeBatchAdmissionContext:
    """The run-scoped slot the graph tool seam reads its F6 binding from.

    A context variable rather than a middleware constructor argument for the
    same reason the run control binding is one: Deep Agents materializes a fresh
    middleware stack for every locally compiled subagent graph, so a value
    threaded through ``__init__`` would reach the supervisor and none of its
    children. A delegated tool call would then be the one call that never
    crossed F6 — precisely the bypass this seam exists to make impossible.

    Installed once per run by the composition root and never rebound, so the
    object every scope sees is the same object; the plan a turn records is
    therefore visible to the tool calls that turn emitted, in every scope.
    """

    @staticmethod
    def install(
        port: RuntimeBatchAdmissionPort,
    ) -> Token[RuntimeBatchAdmissionPort | None]:
        """Bind one run's batch admission and return its reset token."""

        return _CURRENT_BATCH_ADMISSION.set(port)

    @staticmethod
    def current() -> RuntimeBatchAdmissionPort | None:
        """Return the active batch admission, or ``None`` when F6 is dark."""

        return _CURRENT_BATCH_ADMISSION.get()

    @staticmethod
    def reset(token: Token[RuntimeBatchAdmissionPort | None]) -> None:
        """Release one run's binding."""

        _CURRENT_BATCH_ADMISSION.reset(token)


class RuntimeDependencies(RuntimeContract):
    """Dependency-injected runtime ports.

    Concrete connectors, stores, MCP clients, and runners live behind these
    protocols so unit tests can use fakes and the runtime avoids vendor SDKs.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )

    tool_registry: ToolRegistry
    mcp_registry: McpRegistry
    skill_source_config: SkillSourceConfig
    skill_registry: object | None = None
    prior_tool_result_loader: object | None = None
    memory_backend_factory: MemoryBackendFactory
    subagent_catalog: SubagentCatalog
    # Optional read-only Deep Agents backend that exposes per-subagent execution
    # traces under `/subagents/<task_id>/`. Constructed by the run handler from
    # the event store + persistence ports; the factory wraps it with deepagents'
    # `CompositeBackend` so the supervisor's `ls` and `read_file` tools can read
    # it without affecting `/memories/`, `/skills/`, etc.
    subagent_artifacts_backend: object | None = None
    # Optional Deep Agents backend that captures `/drafts/<uuid>.md` writes
    # and persists them as versioned `runtime_drafts` rows. Constructed by the
    # run handler from the draft store + event producer; the factory routes
    # the `/drafts/` prefix to it so the agent's existing `write_file` /
    # `edit_file` tools produce Workspace-pane drafts without a new tool.
    drafts_backend: object | None = None
    # Optional read-only Deep Agents backend that resolves offloaded oversized
    # tool results under `/large_tool_results/<sha256>` from the desktop file
    # store's content-addressed object store. Constructed by the run handler on
    # the `file` backend only; the factory routes the `/large_tool_results/`
    # prefix to it. `None` everywhere else, so those paths stay on the default
    # `StateBackend` exactly as before.
    large_tool_results_backend: object | None = None
    # Optional durable store for the browsable MCP catalog served at `/mcp/`.
    # Implements the publisher + reader halves of the catalog seam
    # (`McpCatalogPublisher` / `McpCatalogReader`); on the desktop the worker
    # supplies the file-backed one rooted at
    # `$COPILOT_HOME/.tmp/<conversation_id>/mcp/`, which is what makes a catalog
    # survive the next turn AND the second harness an approval resume builds.
    # `None` everywhere else, and the factory then composes the in-process
    # `McpCatalogStore` exactly as before — one catalog per run either way,
    # never a file tree shadowed by an in-memory copy.
    mcp_catalog_store: object | None = None
    # Optional read-only Deep Agents backend exposing user-granted host folders
    # under `/workspace/<mount>/<path>`. Constructed by the run handler per run
    # from the desktop capability broker (env config + the run's ACTIVE grant
    # snapshot); the factory routes the `/workspace/` prefix to it. `None`
    # everywhere the broker is absent (web / postgres / in-memory) or when the
    # user has granted no folders, so the route is absent and those images are
    # byte-identical.
    workspace_backend: object | None = None
    # The host folders the user has ATTACHED, resolved once per run by the worker
    # off the desktop capability broker's active-grant snapshot (as
    # ``GrantedRoot``s). The factory turns them into the deepagents ``allow``
    # rules — and the matching ``HostFilesystemFloor`` roots — that stop an
    # attached folder prompting on every read.
    #
    # Carried here rather than read off ``workspace_backend`` because that object
    # is chosen by ``workspace_effect_mode``: in ENFORCE it is C3's
    # gateway/tombstone backend, which cannot name a host root (its host-session
    # projection is path-free by design), so the capability read found nothing and
    # attaching a folder bought the user nothing in the mode the desktop ships.
    # Which folders the user granted is a broker fact, not a property of the
    # write-staging lane.
    #
    # ``None`` means "not resolved" and falls back to the backend capability, so
    # every existing composition is unchanged; ``()`` means "resolved: nothing is
    # attached". ``None`` everywhere off the desktop path.
    granted_host_roots: tuple[object, ...] | None = None
    # Optional gated ``run_code_mode`` tool (AC6 Monty code mode). Built per run
    # by the worker only when ``RUNTIME_ENABLE_MONTY`` + ``single_user_desktop``
    # hold and the file object store is present; the factory appends it to the
    # model-visible tool set and adds its prompt guidance. ``None`` everywhere
    # else, so non-desktop / disabled runs are byte-identical.
    code_mode_tool: object | None = None
    # Optional gated ``run_in_sandbox`` tool (AC7 remote sandbox, execute-only).
    # Built per run by the worker only when ``RUNTIME_ENABLE_REMOTE_SANDBOX`` + a
    # configured provider + ``single_user_desktop`` hold; the factory appends it
    # to the model-visible tool set and adds its prompt guidance. ``None``
    # everywhere else, so non-desktop / disabled runs are byte-identical.
    sandbox_execute_tool: object | None = None
    # Optional gated ``stage_rowset_write`` tool (PRD-D3 bulk row-set staging).
    # Built per run by the worker only when ``SURFACES_V2`` is on; the factory
    # appends it to the model-visible tool set. ``None`` (and absent from the
    # model's tool surface) with the flag off, so those runs are byte-identical.
    stage_rowset_write_tool: object | None = None
    # Optional B1 explicit artifact-publication tool. Built only when the A2
    # repository is fully composed behind ``ARTIFACT_EFFECTS_V2``; absent on
    # the dark path so ordinary model tool surfaces remain unchanged.
    publish_artifact_tool: object | None = None
    revise_artifact_tool: object | None = None
    # Optional process-wide TTL cache for MCP discovery (the
    # ``connect + list_tools + list_resources`` round-trips on
    # ``McpLoader.load_server``). When ``None`` the loader behaves
    # exactly as before. Constructed at FastAPI lifespan startup and
    # at worker dependency wiring; one instance per process (API and
    # worker run in separate processes and each gets its own cache).
    mcp_discovery_cache: object | None = None
    # Optional run-bound PromptAssemblyObserver. The worker constructs it from
    # the canonical run event journal plus the already-verified control
    # snapshot, then the factory binds it to every root/child model invocation.
    # Kept as an object here to preserve the execution-contract module's
    # dependency direction; PromptRuntimeBinding validates the concrete use.
    prompt_assembly_observer: object | None = None
    # Optional F3 capability-discovery inputs: the resolved
    # ``CapabilityActivationDecision`` and the authorization-projected
    # ``CapabilityCatalog``. Both are built per run *after* verified identity,
    # connector scope, the F4 policy selection, and the F8 descriptor revision
    # are known; the factory hands the pair to the single
    # ``CapabilityBridgeRegistrar`` entry point, which registers the bounded
    # discovery bridge tools only in the ``deferred`` posture and only for a
    # catalog that can mint a revalidatable ref. ``None`` — the production
    # default while F3 is dark — registers nothing, so the model-visible tool
    # surface is byte-identical to the pre-F3 disclosure path. Kept as
    # ``object`` here (like ``prompt_assembly_observer``) to preserve this
    # module's dependency direction: the discovery contracts import from it, so
    # a typed field would close an import cycle. The factory validates the
    # concrete types and narrows to no bridge tools when either is unresolved.
    capability_activation: object | None = None
    capability_catalog: object | None = None
    # Optional P2-8 per-tool MCP credential plane: an
    # ``McpPerToolCollaborators`` (connection directory + credential provider).
    # Supplied per deployment by the composition root; ``None`` — the production
    # default while the desktop keychain writer is missing — makes the factory
    # keep the legacy ``call_mcp_tool`` gateway even with
    # ``MCP_PER_TOOL_ENABLED`` on, so a half-wired credential plane can never
    # cost a run its connectors. Kept as ``object`` (like
    # ``prompt_assembly_observer``) to preserve this module's dependency
    # direction; the registrar validates the concrete type.
    mcp_per_tool_collaborators: object | None = None
    # Optional sink for the run's ``tool_name -> server_slug`` snapshot. The
    # worker passes its ``StreamMessageProcessor`` (which exposes
    # ``publish_connector_resolver`` since P2-6); the factory calls it once per
    # run when per-tool registration succeeds, so a per-tool call still resolves
    # ``connector_slug`` / ``provenance`` / ``access_mode`` in the stream.
    # ``None`` everywhere the legacy gateway is active — the seam then resolves
    # exactly as it did, through ``McpDispatcherUnwrap``.
    mcp_connector_resolver_sink: object | None = None
    # Optional F3 invocation seam: the run-scoped inputs only a composition root
    # can know (the keyed reference minter, the live revalidation, the telemetry
    # observers, and the protected-schema publisher). See
    # :class:`CapabilityBridgeComposition`. ``None`` — the production default —
    # leaves the registrar with the catalog-only search and describe pair it
    # registered before, so nothing about the dark path changes.
    capability_bridge: object | None = None

    @field_validator(
        "tool_registry",
        "mcp_registry",
        "memory_backend_factory",
        "subagent_catalog",
    )
    @classmethod
    def _validate_protocol(cls, value: object, info: ValidationInfo) -> object:
        method_by_field = {
            "tool_registry": "list_available_tools",
            "mcp_registry": "list_available_servers",
            "memory_backend_factory": "create",
            "subagent_catalog": "list_available_subagents",
        }
        method_name = method_by_field[info.field_name]
        if not callable(getattr(value, method_name, None)):
            msg = f"{info.field_name} must provide callable {method_name}()"
            raise ValueError(msg)
        return value


class RuntimeErrorEnvelope(RuntimeContract):
    """User-safe serialized runtime error."""

    code: RuntimeErrorCode
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool
    correlation_id: str

    @field_validator("safe_message")
    @classmethod
    def _normalize_safe_message(cls, value: str) -> str:
        return _normalize_nonempty_string(value, "safe_message")

    @field_validator("correlation_id")
    @classmethod
    def _normalize_correlation_id(cls, value: str) -> str:
        normalized = _normalize_nonempty_string(value, "correlation_id")
        if not _ID_PATTERN.fullmatch(normalized):
            msg = "correlation_id contains unsupported characters"
            raise ValueError(msg)
        return normalized

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        *,
        correlation_id: str | None = None,
        default_code: RuntimeErrorCode = RuntimeErrorCode.RUNTIME_FACTORY_ERROR,
        default_message: str = "The runtime could not complete the request safely.",
        retryable: bool = False,
    ) -> "RuntimeErrorEnvelope":
        from agent_runtime.execution.errors import AgentRuntimeError

        if isinstance(exc, AgentRuntimeError):
            return exc.to_envelope(correlation_id=correlation_id)

        return cls(
            code=default_code,
            safe_message=default_message,
            retryable=retryable,
            correlation_id=correlation_id or uuid4().hex,
        )


class StreamEvent(RuntimeContract):
    """Normalized, redacted event emitted by the runtime."""

    event_id: str = Field(default_factory=TraceContext.event_id)
    source: StreamEventSource
    event_type: StreamEventType
    trace_id: str
    parent_task_id: str | None = None
    payload: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(ObservabilityKeys.Field.EVENT_ID, ObservabilityKeys.Field.TRACE_ID)
    @classmethod
    def _normalize_event_id(cls, value: object, info: ValidationInfo) -> str:
        return StreamValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(ObservabilityKeys.Field.PARENT_TASK_ID)
    @classmethod
    def _normalize_parent_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return StreamValueNormalizer.normalize_id(
            value, ObservabilityKeys.Field.PARENT_TASK_ID
        )

    @field_validator(
        ObservabilityKeys.Field.PAYLOAD,
        ObservabilityKeys.Field.METADATA,
        mode="before",
    )
    @classmethod
    def _redact_json_fields(cls, value: object) -> JsonObject:
        return JsonObjectCoercer.coerce(value)  # type: ignore[return-value]


class StreamValueNormalizer:
    """Normalization helpers for streaming-contract Pydantic validators."""

    from agent_runtime.validation import ValueNormalizer as _V

    normalize_nonempty_string = _V.normalize_nonempty_string
    normalize_id = _V.normalize_id
    normalize_slug = _V.normalize_slug

    del _V


def _normalize_nonempty_string(value: object, field_name: str) -> str:
    """Delegate to the shared ``ValueNormalizer`` for non-empty string validation."""

    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_nonempty_string(value, field_name)


def _normalize_runtime_id(value: object, field_name: str) -> str:
    """Generate a UUID when ``value`` is ``None``; otherwise validate the ID pattern."""

    if value is None:
        return uuid4().hex
    normalized = _normalize_nonempty_string(value, field_name)
    if not _ID_PATTERN.fullmatch(normalized):
        msg = f"{field_name} contains unsupported characters"
        raise ValueError(msg)
    return normalized


def _normalize_slug(value: object, field_name: str) -> str:
    """Delegate to the shared ``ValueNormalizer`` for slug normalization."""

    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_slug(value, field_name)


def _normalize_slug_set(
    value: object,
    field_name: str,
    *,
    require_non_empty: bool = False,
) -> frozenset[str]:
    """Normalize a collection of slugs and optionally enforce non-emptiness."""

    from agent_runtime.validation import ValueNormalizer

    normalized = ValueNormalizer.normalize_slug_set(value, field_name)
    if require_non_empty and not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    return normalized


def _normalize_scope_set(value: object, field_name: str) -> frozenset[str]:
    """Delegate to the shared ``ValueNormalizer`` for OAuth scope-set normalization."""

    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_scope_set(value, field_name)


def _normalize_scope(value: object, field_name: str) -> str:
    """Delegate to the shared ``ValueNormalizer`` for a single OAuth scope string."""

    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.normalize_scope(value, field_name)


def _coerce_iterable(value: object, field_name: str) -> tuple[object, ...]:
    """Delegate to the shared ``ValueNormalizer`` for iterable coercion."""

    from agent_runtime.validation import ValueNormalizer

    return ValueNormalizer.coerce_iterable(value, field_name)
