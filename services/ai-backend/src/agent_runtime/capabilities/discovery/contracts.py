"""Strict contracts for a run-scoped, authorization-projected capability catalog."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract


class CapabilitySource(StrEnum):
    """Trusted compact-record source represented by a catalog entry."""

    TOOL_CARD = "tool_card"
    MCP_SERVER = "mcp_server"


class CatalogEffectClass(StrEnum):
    """Effect metadata known without loading a full capability descriptor."""

    NONE = "none"
    INTERNAL_REVERSIBLE = "internal_reversible"
    EXTERNAL_REVERSIBLE = "external_reversible"
    EXTERNAL_DESTRUCTIVE = "external_destructive"
    UNKNOWN = "unknown"


class ApprovalCue(StrEnum):
    """Compact approval disclosure derived only from trusted policy metadata."""

    NOT_REQUIRED = "not_required"
    POLICY_DEPENDENT = "policy_dependent"
    REQUIRED = "required"
    UNKNOWN = "unknown"


class CapabilityCatalogScope(RuntimeContract):
    """Identity and policy snapshot that bounds one ephemeral catalog."""

    run_id: str = Field(min_length=1, max_length=128)
    org_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(min_length=1, max_length=128)
    policy_revision: str = Field(min_length=1, max_length=256)
    connector_scope_revision: str = Field(min_length=1, max_length=256)

    @field_validator(
        "run_id",
        "org_id",
        "user_id",
        "profile_id",
        "policy_revision",
        "connector_scope_revision",
    )
    @classmethod
    def _strip_nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "catalog scope values must be non-empty"
            raise ValueError(msg)
        return normalized

    @classmethod
    def from_context(
        cls,
        context: AgentRuntimeContext,
        *,
        profile_id: str,
        policy_revision: str,
        connector_scope_revision: str,
    ) -> Self:
        """Create a scope from the verified, frozen run context."""

        return cls(
            run_id=context.run_id,
            org_id=context.org_id,
            user_id=context.user_id,
            profile_id=profile_id,
            policy_revision=policy_revision,
            connector_scope_revision=connector_scope_revision,
        )

    def matches(self, context: AgentRuntimeContext) -> bool:
        """Return whether ``context`` owns this exact run subject."""

        return (
            self.run_id == context.run_id
            and self.org_id == context.org_id
            and self.user_id == context.user_id
        )


class CapabilityIndexEntry(RuntimeContract):
    """Schema-free, model-searchable metadata for one authorized source record."""

    capability_ref: str = Field(
        pattern=r"^cap_[0-9a-f]{32}$",
        min_length=36,
        max_length=36,
    )
    source: CapabilitySource
    stable_name: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    concise_description: str = Field(min_length=1, max_length=512)
    intent_tags: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    parameter_names: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    parameter_types: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    effect_class: CatalogEffectClass = CatalogEffectClass.UNKNOWN
    approval_cue: ApprovalCue = ApprovalCue.UNKNOWN
    connector_label: str = Field(min_length=1, max_length=256)
    descriptor_revision: str | None = Field(default=None, max_length=256)

    @field_validator(
        "stable_name",
        "display_name",
        "concise_description",
        "connector_label",
    )
    @classmethod
    def _strip_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "catalog entry labels must be non-empty"
            raise ValueError(msg)
        return normalized

    @field_validator("intent_tags", mode="before")
    @classmethod
    def _canonical_tags(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            msg = "catalog term fields must be iterables of strings"
            raise ValueError(msg)
        normalized: set[str] = set()
        for item in value:  # type: ignore[union-attr]
            if not isinstance(item, str) or not item.strip():
                msg = "catalog terms must be non-empty strings"
                raise ValueError(msg)
            normalized.add(item.strip().casefold())
        return tuple(sorted(normalized))

    @field_validator("parameter_names", "parameter_types", mode="before")
    @classmethod
    def _canonical_parameter_metadata(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            msg = "parameter metadata must be an iterable of strings"
            raise ValueError(msg)
        normalized: list[str] = []
        for item in value:  # type: ignore[union-attr]
            if not isinstance(item, str) or not item.strip():
                msg = "parameter metadata must contain non-empty strings"
                raise ValueError(msg)
            normalized.append(item.strip().casefold())
        return tuple(normalized)

    @model_validator(mode="after")
    def _parameter_metadata_is_aligned(self) -> Self:
        if self.parameter_types and len(self.parameter_names) != len(
            self.parameter_types
        ):
            msg = "parameter_names and parameter_types must have equal lengths"
            raise ValueError(msg)
        if len(self.parameter_names) != len(set(self.parameter_names)):
            msg = "parameter_names must be unique"
            raise ValueError(msg)
        return self


class CapabilityCatalogRevision(RuntimeContract):
    """Content revision and expiration metadata for one scoped catalog."""

    catalog_id: str = Field(
        pattern=r"^cat_[0-9a-f]{32}$",
        min_length=36,
        max_length=36,
    )
    revision: str = Field(
        pattern=r"^rev_[0-9a-f]{32}$",
        min_length=36,
        max_length=36,
    )
    profile_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    policy_revision: str = Field(min_length=1, max_length=256)
    connector_scope_revision: str = Field(min_length=1, max_length=256)
    descriptor_count: NonNegativeInt
    deferred_schema_tokens: NonNegativeInt = 0
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "expires_at must be timezone-aware"
            raise ValueError(msg)
        return value


class CapabilityCatalog(RuntimeContract):
    """Immutable catalog tied to an exact run subject and policy snapshot."""

    scope: CapabilityCatalogScope
    revision: CapabilityCatalogRevision
    entries: tuple[CapabilityIndexEntry, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _revision_matches_catalog(self) -> Self:
        if self.revision.descriptor_count != len(self.entries):
            msg = "descriptor_count must equal the catalog entry count"
            raise ValueError(msg)
        if (
            self.revision.profile_id != self.scope.profile_id
            or self.revision.user_id != self.scope.user_id
            or self.revision.policy_revision != self.scope.policy_revision
            or self.revision.connector_scope_revision
            != self.scope.connector_scope_revision
        ):
            msg = "catalog revision does not match its scope"
            raise ValueError(msg)
        refs = [entry.capability_ref for entry in self.entries]
        if len(refs) != len(set(refs)):
            msg = "catalog capability refs must be unique"
            raise ValueError(msg)
        return self

    def is_active_for(
        self,
        context: AgentRuntimeContext,
        *,
        now: datetime,
    ) -> bool:
        """Return whether this catalog is owned by and unexpired for ``context``."""

        if now.tzinfo is None or now.utcoffset() is None:
            msg = "now must be timezone-aware"
            raise ValueError(msg)
        return self.scope.matches(context) and now < self.revision.expires_at


class CapabilitySearchFilters(RuntimeContract):
    """Deterministic filters that can only narrow catalog membership."""

    sources: frozenset[CapabilitySource] = Field(default_factory=frozenset)
    effect_classes: frozenset[CatalogEffectClass] = Field(default_factory=frozenset)
    connector_labels: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("connector_labels", mode="before")
    @classmethod
    def _normalize_connectors(cls, value: object) -> frozenset[str]:
        if value is None:
            return frozenset()
        if isinstance(value, (str, bytes)):
            msg = "connector_labels must be an iterable of strings"
            raise ValueError(msg)
        normalized: set[str] = set()
        for item in value:  # type: ignore[union-attr]
            if not isinstance(item, str) or not item.strip():
                msg = "connector_labels must contain non-empty strings"
                raise ValueError(msg)
            normalized.add(item.strip().casefold())
        return frozenset(normalized)


class CapabilitySearchRequest(RuntimeContract):
    """Bounded lexical query against one already-authorized catalog."""

    query: str = Field(min_length=1, max_length=512)
    limit: PositiveInt = Field(default=5, le=10)
    filters: CapabilitySearchFilters = Field(default_factory=CapabilitySearchFilters)

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            msg = "query must contain searchable text"
            raise ValueError(msg)
        return normalized


class CapabilityCandidate(RuntimeContract):
    """One bounded search result containing no full schema or private payload."""

    capability_ref: str = Field(pattern=r"^cap_[0-9a-f]{32}$")
    stable_name: str = Field(min_length=1, max_length=256)
    score: PositiveInt
    matched_terms: tuple[
        Annotated[str, Field(min_length=1, max_length=96)],
        ...,
    ] = Field(default_factory=tuple, max_length=64)
    source: CapabilitySource
    effect_class: CatalogEffectClass
    approval_cue: ApprovalCue


class CapabilitySearchResult(RuntimeContract):
    """Content-minimized result suitable for shadow evaluation and bridge output."""

    catalog_id: str = Field(pattern=r"^cat_[0-9a-f]{32}$")
    catalog_revision: str = Field(pattern=r"^rev_[0-9a-f]{32}$")
    query_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scanned_count: NonNegativeInt
    candidates: tuple[CapabilityCandidate, ...] = Field(
        default_factory=tuple,
        max_length=10,
    )


class CapabilityDiscoveryErrorCode(StrEnum):
    """Stable model-safe failure classes for discovery bridge calls."""

    INVALID_REQUEST = "invalid_request"
    CATALOG_INACTIVE = "catalog_inactive"
    CAPABILITY_NOT_FOUND = "capability_not_found"


class CapabilityDiscoveryError(RuntimeContract):
    """Content-free error returned instead of catalog or validation internals."""

    code: CapabilityDiscoveryErrorCode
    safe_message: str = Field(min_length=1, max_length=240)


class CapabilitySearchToolResult(RuntimeContract):
    """Exactly-one-outcome envelope returned by the model-facing search adapter."""

    search: CapabilitySearchResult | None = None
    error: CapabilityDiscoveryError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> Self:
        if (self.search is None) == (self.error is None):
            msg = "search result must contain exactly one outcome"
            raise ValueError(msg)
        return self

    @classmethod
    def ok(cls, search: CapabilitySearchResult) -> Self:
        """Return a successful bounded search result."""

        return cls(search=search)

    @classmethod
    def fail(
        cls,
        code: CapabilityDiscoveryErrorCode,
        safe_message: str,
    ) -> Self:
        """Return a safe failure without catalog metadata."""

        return cls(
            error=CapabilityDiscoveryError(
                code=code,
                safe_message=safe_message,
            )
        )


class CapabilityDescribeRequest(RuntimeContract):
    """Request compact metadata by the opaque ref returned from search."""

    capability_ref: str = Field(pattern=r"^cap_[0-9a-f]{32}$")


class CapabilityParameterHint(RuntimeContract):
    """Schema-free name/type hint; never a JSON schema or invocation contract."""

    name: str = Field(min_length=1, max_length=96)
    type_hint: str | None = Field(default=None, min_length=1, max_length=96)


class CapabilityDescription(RuntimeContract):
    """Bounded compact metadata for one member of the active catalog."""

    capability_ref: str = Field(pattern=r"^cap_[0-9a-f]{32}$")
    stable_name: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    concise_description: str = Field(min_length=1, max_length=512)
    source: CapabilitySource
    intent_tags: tuple[
        Annotated[str, Field(min_length=1, max_length=64)],
        ...,
    ] = Field(default_factory=tuple, max_length=16)
    parameters: tuple[CapabilityParameterHint, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    effect_class: CatalogEffectClass
    approval_cue: ApprovalCue
    connector_label: str = Field(min_length=1, max_length=256)
    descriptor_revision: str | None = Field(default=None, max_length=256)
    metadata_truncated: bool = False


class CapabilityDescribeResult(RuntimeContract):
    """Description plus the revision that made the opaque ref meaningful."""

    catalog_id: str = Field(pattern=r"^cat_[0-9a-f]{32}$")
    catalog_revision: str = Field(pattern=r"^rev_[0-9a-f]{32}$")
    capability: CapabilityDescription


class CapabilityDescribeToolResult(RuntimeContract):
    """Exactly-one-outcome envelope returned by the describe adapter."""

    description: CapabilityDescribeResult | None = None
    error: CapabilityDiscoveryError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> Self:
        if (self.description is None) == (self.error is None):
            msg = "describe result must contain exactly one outcome"
            raise ValueError(msg)
        return self

    @classmethod
    def ok(cls, description: CapabilityDescribeResult) -> Self:
        """Return a successful bounded description."""

        return cls(description=description)

    @classmethod
    def fail(
        cls,
        code: CapabilityDiscoveryErrorCode,
        safe_message: str,
    ) -> Self:
        """Return a safe failure without catalog metadata."""

        return cls(
            error=CapabilityDiscoveryError(
                code=code,
                safe_message=safe_message,
            )
        )
