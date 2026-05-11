"""Pydantic contracts for scoped memory and context compression."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TypeAlias
from uuid import uuid4

from pydantic import (
    Field,
    PositiveInt,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    JsonScalar,
    RuntimeContract,
)
from agent_runtime.observability.redactor import DENY_KEYS

from agent_runtime.context.memory.constants import (
    Defaults,
    Keys,
    Limits,
    Messages,
    Patterns,
    Values,
)

MemoryMetadata: TypeAlias = Mapping[str, JsonScalar]


class MemoryScopeType(StrEnum):
    """Supported memory isolation scopes."""

    USER = Values.ScopeType.USER
    AGENT = Values.ScopeType.AGENT
    ORGANIZATION = Values.ScopeType.ORGANIZATION


class MemoryActorRole(StrEnum):
    """Actors that may read or write memory through policy checks."""

    USER = Values.ActorRole.USER
    ASSISTANT = Values.ActorRole.ASSISTANT
    APPLICATION = Values.ActorRole.APPLICATION


class MemoryAccessOperation(StrEnum):
    """Memory access operations governed by path policy."""

    READ = Values.Operation.READ
    WRITE = Values.Operation.WRITE


class ContextCompressionStrategy(StrEnum):
    """How a payload was kept under the active context budget."""

    INLINE = Values.CompressionStrategy.INLINE
    OFFLOAD = Values.CompressionStrategy.OFFLOAD
    SUMMARIZE = Values.CompressionStrategy.SUMMARIZE
    FALLBACK_SUMMARY = Values.CompressionStrategy.FALLBACK_SUMMARY


class ContextFallbackTrigger(StrEnum):
    """Conditions that activate summary fallback behavior."""

    CONTEXT_OVERFLOW = Values.FallbackTrigger.CONTEXT_OVERFLOW
    SUMMARIZATION_FAILURE = Values.FallbackTrigger.SUMMARIZATION_FAILURE


class MemoryScope(RuntimeContract):
    """Tenant-safe namespace for user, agent, or organization memory."""

    scope_type: MemoryScopeType
    org_id: str
    namespace: tuple[str, ...] = Field(min_length=1)
    user_id: str | None = None
    assistant_id: str | None = None

    @field_validator(Keys.Field.ORG_ID, Keys.Field.USER_ID, Keys.Field.ASSISTANT_ID)
    @classmethod
    def _normalize_optional_id(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is None:
            return None
        return MemoryValueNormalizer.normalize_id(value, info.field_name)

    @field_validator(Keys.Field.NAMESPACE, mode="before")
    @classmethod
    def _normalize_namespace(cls, value: object) -> tuple[str, ...]:
        return MemoryValueNormalizer.normalize_namespace(value)

    @model_validator(mode="after")
    def _validate_scope_identifiers(self) -> "MemoryScope":
        if self.scope_type is MemoryScopeType.USER and self.user_id is None:
            raise ValueError(Messages.Validation.USER_SCOPE_REQUIRES_USER_ID)
        if self.scope_type is MemoryScopeType.AGENT and self.assistant_id is None:
            raise ValueError(Messages.Validation.AGENT_SCOPE_REQUIRES_ASSISTANT_ID)
        if not self.namespace:
            raise ValueError(Messages.Validation.NAMESPACE_REQUIRED)
        return self

    @classmethod
    def for_user(cls, context: AgentRuntimeContext) -> "MemoryScope":
        """Create an isolated user memory namespace for the runtime context."""

        return cls(
            scope_type=MemoryScopeType.USER,
            org_id=context.org_id,
            user_id=context.user_id,
            namespace=("org", context.org_id, "user", context.user_id),
        )

    @classmethod
    def for_agent(
        cls,
        context: AgentRuntimeContext,
        *,
        assistant_id: str = Defaults.ASSISTANT_ID,
    ) -> "MemoryScope":
        """Create an agent memory namespace scoped inside the organization."""

        normalized_assistant_id = MemoryValueNormalizer.normalize_id(
            assistant_id,
            Keys.Field.ASSISTANT_ID,
        )
        return cls(
            scope_type=MemoryScopeType.AGENT,
            org_id=context.org_id,
            assistant_id=normalized_assistant_id,
            namespace=("org", context.org_id, "agent", normalized_assistant_id),
        )

    @classmethod
    def for_organization(cls, context: AgentRuntimeContext) -> "MemoryScope":
        """Create a shared organization policy memory namespace."""

        return cls(
            scope_type=MemoryScopeType.ORGANIZATION,
            org_id=context.org_id,
            namespace=("org", context.org_id, "policies"),
        )


class MemoryPathPolicy(RuntimeContract):
    """Read/write policy for a virtual memory path prefix."""

    path_prefix: str
    read_roles: frozenset[MemoryActorRole]
    write_roles: frozenset[MemoryActorRole] = Field(default_factory=frozenset)
    shared: bool = False
    approval_required: bool = False

    @field_validator(Keys.Field.PATH_PREFIX)
    @classmethod
    def _normalize_path_prefix(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_path_prefix(
            value, Keys.Field.PATH_PREFIX
        )

    @field_validator(Keys.Field.READ_ROLES, Keys.Field.WRITE_ROLES, mode="before")
    @classmethod
    def _normalize_roles(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> frozenset[MemoryActorRole]:
        return MemoryValueNormalizer.normalize_actor_roles(value, info.field_name)

    def matches(self, path: str) -> bool:
        """Return whether this policy governs the supplied memory path."""

        normalized_path = MemoryValueNormalizer.normalize_memory_path(
            path, Keys.Field.PATH
        )
        return normalized_path.startswith(self.path_prefix)


class TokenBudgetPolicy(RuntimeContract):
    """Threshold policy for Deep Agents context compression decisions."""

    max_input_tokens: PositiveInt = Field(default=Defaults.MAX_INPUT_TOKENS)
    summary_threshold_ratio: float = Field(
        default=Defaults.SUMMARY_THRESHOLD_RATIO,
        gt=0,
        le=1,
    )
    recent_context_ratio: float = Field(
        default=Defaults.RECENT_CONTEXT_RATIO,
        gt=0,
        lt=1,
    )
    fallback_trigger: ContextFallbackTrigger = ContextFallbackTrigger.CONTEXT_OVERFLOW

    @model_validator(mode="after")
    def _validate_ratios(self) -> "TokenBudgetPolicy":
        if self.recent_context_ratio >= self.summary_threshold_ratio:
            raise ValueError(
                "recent_context_ratio must be lower than summary_threshold_ratio"
            )
        return self


class ContextCompressionEvent(RuntimeContract):
    """Redacted metrics emitted when context is compressed or offloaded."""

    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    strategy: ContextCompressionStrategy
    files_written: tuple[str, ...] = Field(default_factory=tuple)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    metadata: MemoryMetadata = Field(default_factory=dict)

    @field_validator(Keys.Field.FILES_WRITTEN, mode="before")
    @classmethod
    def _normalize_files_written(cls, value: object) -> tuple[str, ...]:
        return tuple(
            MemoryValueNormalizer.normalize_memory_path(item, Keys.Field.FILES_WRITTEN)
            for item in MemoryValueNormalizer.coerce_iterable(
                value, Keys.Field.FILES_WRITTEN
            )
        )

    @field_validator(Keys.Field.TRACE_ID)
    @classmethod
    def _normalize_trace_id(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_id(value, Keys.Field.TRACE_ID)

    @field_validator(Keys.Field.METADATA, mode="before")
    @classmethod
    def _redact_metadata(cls, value: object) -> MemoryMetadata:
        return MemoryRedactor.redact_metadata(value)

    @model_validator(mode="after")
    def _validate_compression_result(self) -> "ContextCompressionEvent":
        if self.after_tokens > self.before_tokens:
            raise ValueError("after_tokens must be less than or equal to before_tokens")
        return self


class ContextSummary(RuntimeContract):
    """Structured summary that preserves task continuity after compression."""

    objective: str = Field(min_length=1, max_length=Limits.SUMMARY_FIELD_MAX_LENGTH)
    decisions: tuple[str, ...] = Field(default_factory=tuple)
    artifacts: tuple[str, ...] = Field(default_factory=tuple)
    next_steps: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        Keys.Field.OBJECTIVE,
        Keys.Field.DECISIONS,
        Keys.Field.ARTIFACTS,
        Keys.Field.NEXT_STEPS,
        mode="before",
    )
    @classmethod
    def _normalize_summary_field(cls, value: object, info: ValidationInfo) -> object:
        if info.field_name == Keys.Field.OBJECTIVE:
            return MemoryValueNormalizer.normalize_nonempty_string(
                value, info.field_name
            )
        return tuple(
            MemoryValueNormalizer.normalize_nonempty_string(item, info.field_name)
            for item in MemoryValueNormalizer.coerce_iterable(value, info.field_name)
        )


class ManagedContextPayload(RuntimeContract):
    """Tool or connector output after inline, offload, or summary handling."""

    strategy: ContextCompressionStrategy
    content: str | None = None
    reference: str | None = None
    preview: str | None = None
    event: ContextCompressionEvent

    @field_validator(Keys.Field.CONTENT, Keys.Field.REFERENCE, mode="before")
    @classmethod
    def _normalize_optional_string(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        if info.field_name == Keys.Field.REFERENCE:
            return MemoryValueNormalizer.normalize_memory_path(value, info.field_name)
        return MemoryValueNormalizer.normalize_nonempty_string(value, info.field_name)

    @model_validator(mode="after")
    def _validate_strategy_payload(self) -> "ManagedContextPayload":
        if (
            self.strategy is ContextCompressionStrategy.OFFLOAD
            and self.reference is None
        ):
            raise ValueError("offloaded payloads require a reference")
        if (
            self.strategy
            in {
                ContextCompressionStrategy.INLINE,
                ContextCompressionStrategy.SUMMARIZE,
                ContextCompressionStrategy.FALLBACK_SUMMARY,
            }
            and self.content is None
        ):
            raise ValueError("inline and summarized payloads require content")
        return self


class MemoryValueNormalizer:
    """Normalization helpers used by memory Pydantic validators.

    Common methods delegate to the shared ``ValueNormalizer``;
    memory-specific helpers (paths, namespaces, actor roles) and the
    length-bounded ``normalize_id`` remain here.
    """

    from agent_runtime.validation import ValueNormalizer as _V

    normalize_nonempty_string = _V.normalize_nonempty_string
    coerce_iterable = _V.coerce_iterable

    del _V

    @classmethod
    def normalize_id(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if len(normalized) > Limits.TRACE_ID_MAX_LENGTH:
            raise ValueError(
                Messages.Validation.id_contains_unsupported_characters(field_name)
            )
        if not Patterns.ID.fullmatch(normalized):
            raise ValueError(
                Messages.Validation.id_contains_unsupported_characters(field_name)
            )
        return normalized

    @classmethod
    def normalize_namespace(cls, value: object) -> tuple[str, ...]:
        values = cls.coerce_iterable(value, Keys.Field.NAMESPACE)
        namespace = tuple(
            cls.normalize_id(item, Keys.Field.NAMESPACE) for item in values
        )
        if not namespace:
            raise ValueError(Messages.Validation.NAMESPACE_REQUIRED)
        return namespace

    @classmethod
    def normalize_memory_path(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_nonempty_string(value, field_name)
        if ".." in normalized.split("/"):
            raise ValueError(Messages.Validation.PATH_TRAVERSAL_UNSUPPORTED)
        if len(
            normalized
        ) > Limits.MEMORY_PATH_MAX_LENGTH or not Patterns.MEMORY_PATH.fullmatch(
            normalized
        ):
            raise ValueError(Messages.Validation.memory_path(field_name))
        return normalized

    @classmethod
    def normalize_path_prefix(cls, value: object, field_name: str) -> str:
        normalized = cls.normalize_memory_path(value, field_name)
        if not Patterns.PATH_PREFIX.fullmatch(normalized):
            raise ValueError(Messages.Validation.path_prefix(field_name))
        return normalized

    @classmethod
    def normalize_actor_roles(
        cls,
        value: object,
        field_name: str,
    ) -> frozenset[MemoryActorRole]:
        values = cls.coerce_iterable(value, field_name)
        return frozenset(MemoryActorRole(item) for item in values)


class MemoryRedactor:
    """Redaction helpers for safe compression observability events."""

    REDACTED = "[redacted]"

    @classmethod
    def redact_metadata(cls, value: object) -> MemoryMetadata:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")

        redacted: dict[str, JsonScalar] = {}
        for key, item in value.items():
            normalized_key = MemoryValueNormalizer.normalize_nonempty_string(
                key,
                Keys.Field.METADATA,
            )
            if normalized_key in DENY_KEYS:
                redacted[normalized_key] = cls.REDACTED
                continue
            # P11.2: no more value-pattern scrubbing. Memory metadata
            # values pass through unchanged; sensitivity is decided at
            # the key boundary (above) or — once P11.3 lands — at the
            # field-annotation level on the enclosing Pydantic model.
            if isinstance(item, str | int | float | bool) or item is None:
                redacted[normalized_key] = item
                continue
            redacted[normalized_key] = str(item)
        return redacted
