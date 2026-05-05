"""Public request/response schemas for /v1/agent/workspace/defaults (PR 1.6 + PR 4.3).

Workspace-level runtime defaults consulted at conversation-create
(``default_connectors``) and at run-create (``default_model`` +
``behavior_overrides``) when the inbound request omits the
corresponding field.

Retention is *not* a column on this table — see migration 0019 header.
The Settings retention slider composes ``scope='org'`` rows in
``retention_policies`` (migration 0012) per kind. This schema carries
``retention_days`` as a derived/composed view: GET resolves the
org-scope policy for ``messages``; PUT writes the same value back as
three policies (messages, events, checkpoints) inside one transaction.

PR 4.3 adds ``behavior_overrides`` — a small, opinion-shaped JSONB
blob with five workspace-policy knobs (``system_prompt_override``,
``temperature``, ``citation_density``, ``refusal_behavior``,
``default_reasoning_effort``, ``training_data_opt_out``). Pydantic
v2 strict-mode here is the single validation point; the persistence
layer never queries by substructure (the runtime resolver reads the
keys it knows, future keys are ignored cleanly).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field, ValidationInfo, field_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.api.constants import Keys
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.conversations import (
    ConnectorScopeValidator,
    ConversationConnectorScopes,
)


class _Fields:
    DEFAULT_MODEL = "default_model"
    DEFAULT_CONNECTORS = "default_connectors"
    RETENTION_DAYS = "retention_days"
    BEHAVIOR_OVERRIDES = "behavior_overrides"
    PROVIDER = "provider"
    MODEL_NAME = "model_name"
    SYSTEM_PROMPT_OVERRIDE = "system_prompt_override"
    TEMPERATURE = "temperature"
    CITATION_DENSITY = "citation_density"
    REFUSAL_BEHAVIOR = "refusal_behavior"
    DEFAULT_REASONING_EFFORT = "default_reasoning_effort"
    TRAINING_DATA_OPT_OUT = "training_data_opt_out"


# PR 4.3 — three small, well-known enums that gate the workspace-policy
# knobs. Each is a closed set; the FE renders a 3-way pill / select.
class CitationDensity(StrEnum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    THOROUGH = "thorough"


class RefusalBehavior(StrEnum):
    STANDARD = "standard"
    STRICT = "strict"
    PERMISSIVE = "permissive"


class ReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# 8 KB cap on the system_prompt_override (see pr-4.3 §2.6). The cap
# protects token budgets at run-start; longer overrides should ride
# the assistant config or per-run request, not the workspace default.
_SYSTEM_PROMPT_MAX_CHARS = 8 * 1024
_TEMPERATURE_MIN = 0.0
_TEMPERATURE_MAX = 1.0


class WorkspaceBehaviorOverrides(RuntimeContract):
    """Workspace-policy knobs read at run/conversation create.

    Every field is optional. ``training_data_opt_out`` defaults to
    ``False`` so an absent row matches "training is allowed (current
    behaviour)". Five-of-six fields fall through to deployment
    defaults when None; the sixth (``training_data_opt_out``) is a
    plain boolean with a deterministic default.

    ``model_config = forbid`` rejects unknown keys at write — keeps
    the JSONB blob from accumulating drift over time.
    """

    model_config = ConfigDict(extra="forbid")

    system_prompt_override: str | None = Field(
        default=None,
        max_length=_SYSTEM_PROMPT_MAX_CHARS,
    )
    temperature: float | None = Field(
        default=None,
        ge=_TEMPERATURE_MIN,
        le=_TEMPERATURE_MAX,
    )
    citation_density: CitationDensity | None = None
    refusal_behavior: RefusalBehavior | None = None
    default_reasoning_effort: ReasoningEffort | None = None
    training_data_opt_out: bool = False

    @field_validator(_Fields.SYSTEM_PROMPT_OVERRIDE, mode="before")
    @classmethod
    def _normalize_system_prompt(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            msg = "system_prompt_override must be a string"
            raise ValueError(msg)
        stripped = value.strip()
        return stripped or None


# Inclusive [1, 3650] day window. Retention rows below 1 day are useless;
# above 10 years (3650) is almost always a typo. The C8 sweeper accepts
# any positive ttl_seconds; this UI-facing cap is informational and
# bounces obvious mistakes early.
_MIN_RETENTION_DAYS = 1
_MAX_RETENTION_DAYS = 3650


# Default-model JSONB shape: {provider, model_name, reasoning?}.
# We avoid pulling in ``ModelSelectionRequest`` to keep the wire surface
# narrow and avoid cyclical imports between the workspace defaults
# schema and the runs schema (which has its own optional/auth fields).
class DefaultModelSelection(RuntimeContract):
    """Workspace-default model selection.

    Mirrors ``ModelSelectionRequest`` minimally — provider + model_name
    + reasoning. Other fields (temperature, timeouts) belong per-run,
    not per-workspace, so they are intentionally absent.
    """

    provider: str
    model_name: str
    reasoning: JsonObject | None = None

    @field_validator(_Fields.PROVIDER, _Fields.MODEL_NAME, mode="before")
    @classmethod
    def _normalize(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)


class WorkspaceDefaultsRecord(RuntimeContract):
    """Persisted workspace defaults row.

    ``retention_days`` lives in this record but is not persisted in the
    ``workspace_defaults`` table — it is resolved from / written to
    ``retention_policies`` inside the service (one orchestrated TX).

    ``behavior_overrides`` (PR 4.3) is persisted in this row as a
    typed JSONB blob — see ``WorkspaceBehaviorOverrides`` for the
    closed shape.
    """

    org_id: str
    default_model: DefaultModelSelection | None = None
    default_connectors: ConversationConnectorScopes = Field(default_factory=dict)
    retention_days: int | None = None
    behavior_overrides: WorkspaceBehaviorOverrides = Field(
        default_factory=WorkspaceBehaviorOverrides
    )
    updated_at: datetime | None = None
    updated_by_user_id: str | None = None

    @field_validator(Keys.Field.ORG_ID, mode="before")
    @classmethod
    def _normalize_org(cls, value: object) -> str:
        return ValueNormalizer.normalize_id(value, Keys.Field.ORG_ID)

    @field_validator(_Fields.DEFAULT_CONNECTORS, mode="before")
    @classmethod
    def _coerce_connectors(cls, value: object) -> ConversationConnectorScopes:
        return ConnectorScopeValidator.coerce(value)

    @field_validator(_Fields.BEHAVIOR_OVERRIDES, mode="before")
    @classmethod
    def _coerce_overrides(cls, value: object) -> WorkspaceBehaviorOverrides:
        if value is None:
            return WorkspaceBehaviorOverrides()
        if isinstance(value, WorkspaceBehaviorOverrides):
            return value
        if isinstance(value, dict):
            return WorkspaceBehaviorOverrides.model_validate(value)
        msg = "behavior_overrides must be an object or null"
        raise ValueError(msg)


class WorkspaceDefaultsResponse(RuntimeContract):
    """Public read shape returned by GET / PUT.

    The FE always sees a complete object: when no row exists for the
    org, the service materialises deployment fallbacks
    (``RuntimeSettings.default_model`` + the deployment's retention
    floor) so the panel renders a populated baseline.
    """

    default_model: DefaultModelSelection
    default_connectors: ConversationConnectorScopes = Field(default_factory=dict)
    retention_days: int
    behavior_overrides: WorkspaceBehaviorOverrides = Field(
        default_factory=WorkspaceBehaviorOverrides
    )
    updated_at: datetime | None = None
    updated_by_user_id: str | None = None


class UpdateWorkspaceDefaultsRequest(RuntimeContract):
    """Body for ``PUT /v1/agent/workspace/defaults``.

    Full-document replace (not RFC 7396 merge-patch) — defaults are
    short, the admin is editing a Settings panel where partial intent
    isn't a thing. The service reuses ``ConnectorScopeValidator`` for
    the connector map and the C8 retention pipeline for the policy
    rows.

    PR 4.3 adds an optional ``behavior_overrides`` block. Existing
    callers that omit it land on the default
    ``WorkspaceBehaviorOverrides()`` (all-None / opt-out=False), so
    the change is backwards compatible.
    """

    default_model: DefaultModelSelection
    default_connectors: ConversationConnectorScopes = Field(default_factory=dict)
    retention_days: int = Field(ge=_MIN_RETENTION_DAYS, le=_MAX_RETENTION_DAYS)
    behavior_overrides: WorkspaceBehaviorOverrides = Field(
        default_factory=WorkspaceBehaviorOverrides
    )

    @field_validator(_Fields.DEFAULT_CONNECTORS, mode="before")
    @classmethod
    def _coerce_connectors(cls, value: object) -> ConversationConnectorScopes:
        return ConnectorScopeValidator.coerce(value)

    @field_validator(_Fields.BEHAVIOR_OVERRIDES, mode="before")
    @classmethod
    def _coerce_overrides(cls, value: object) -> WorkspaceBehaviorOverrides:
        if value is None:
            return WorkspaceBehaviorOverrides()
        if isinstance(value, WorkspaceBehaviorOverrides):
            return value
        if isinstance(value, dict):
            return WorkspaceBehaviorOverrides.model_validate(value)
        msg = "behavior_overrides must be an object or null"
        raise ValueError(msg)


# Bounds re-exported for the service so the only place that knows the
# UI-facing retention window is this file.
RETENTION_DAYS_BOUNDS = (_MIN_RETENTION_DAYS, _MAX_RETENTION_DAYS)


__all__: tuple[str, ...] = (
    "CitationDensity",
    "DefaultModelSelection",
    "RETENTION_DAYS_BOUNDS",
    "ReasoningEffort",
    "RefusalBehavior",
    "UpdateWorkspaceDefaultsRequest",
    "WorkspaceBehaviorOverrides",
    "WorkspaceDefaultsRecord",
    "WorkspaceDefaultsResponse",
)


def update_workspace_defaults_request_to_record(
    *,
    org_id: str,
    request: UpdateWorkspaceDefaultsRequest,
    actor_user_id: str,
    now: datetime,
) -> WorkspaceDefaultsRecord:
    """Translate a wire request into a persistence record.

    Centralised so the route + service + tests don't each re-build the
    record (a tiny but important DRY win — the record is constructed
    in exactly one place).
    """

    return WorkspaceDefaultsRecord(
        org_id=org_id,
        default_model=request.default_model,
        default_connectors=request.default_connectors,
        retention_days=request.retention_days,
        behavior_overrides=request.behavior_overrides,
        updated_at=now,
        updated_by_user_id=actor_user_id,
    )


# Type alias used by the service layer when it needs to thread an
# authoritative "what this conversation should default to" through the
# create_conversation path without forcing every caller to load the
# whole record.
WorkspaceDefaultsLite = dict[str, Any]
