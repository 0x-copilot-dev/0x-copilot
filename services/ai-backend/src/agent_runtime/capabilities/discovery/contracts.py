"""Strict contracts for a run-scoped, authorization-projected capability catalog."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_SHA256_HEX_PATTERN = r"^[a-f0-9]{64}$"
_ZERO_DIGEST = "0" * 64
_MAX_DESCRIPTOR_REVISIONS = 4_096


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


class CapabilityCatalogIdentityError(ValueError):
    """Typed, model-safe failure of a catalog identity or ref binding."""


class CatalogDescriptorRevision(RuntimeContract):
    """One opaque ``(source, descriptor revision)`` pair keyed into a generation.

    ``descriptor_revision`` is the F8 control-plane revision.  It is compared
    for equality only; a caller-supplied ordering, timestamp, or "newer
    looking" value never implies freshness.  Values must already be canonical
    because they are digest inputs — silently normalizing them here would make
    two different inputs collide onto one identity.
    """

    source_id: str = Field(min_length=1, max_length=256)
    descriptor_revision: str = Field(min_length=1, max_length=512)

    class Messages:
        """Safe public messages for descriptor-revision validation."""

        UNCANONICAL = "catalog descriptor revision values must not be padded"

    @field_validator("source_id", "descriptor_revision")
    @classmethod
    def _reject_uncanonical(cls, value: str) -> str:
        if value != value.strip():
            raise CapabilityCatalogIdentityError(cls.Messages.UNCANONICAL)
        return value


class CapabilityCatalogGeneration(RuntimeContract):
    """Reproducible, body-free identity of the inputs one catalog was built from.

    The generation is keyed to exactly four trusted inputs: the verified
    subject fingerprint, the connector scope revision, the F4 task-policy
    selection reference, and the folded F8 descriptor revisions.  Two catalogs
    built from different inputs are therefore distinguishable by
    :attr:`generation_digest` alone, and rebuilding from identical inputs
    reproduces the same digest.

    Activation mode is deliberately *not* keyed in.  Activation governs whether
    the discovery bridge is registered; it does not change which capabilities a
    catalog may contain, and folding it in would make a narrowing kill switch
    look like a different catalog.

    The contract carries identifiers, revisions, digests, and counts only.  It
    never carries descriptors, schemas, arguments, prompt text, or credentials.
    """

    schema_version: Literal[1] = 1
    subject_fingerprint: str = Field(pattern=_SHA256_HEX_PATTERN)
    connector_scope_revision: str = Field(min_length=1, max_length=256)
    task_policy_selection_ref: str = Field(min_length=1, max_length=512)
    descriptor_revision_digest: str = Field(pattern=_SHA256_HEX_PATTERN)
    descriptor_revision_count: int = Field(
        default=0, ge=0, le=_MAX_DESCRIPTOR_REVISIONS
    )
    generation_digest: str = Field(pattern=_SHA256_HEX_PATTERN)

    class Messages:
        """Safe public messages for catalog generation identity failures."""

        UNCANONICAL = "catalog generation key values must not be padded"
        DIGEST_MISMATCH = (
            "catalog generation digest does not match its canonical identity"
        )
        CONFLICTING_REVISION = (
            "a catalog source cannot contribute two descriptor revisions"
        )
        TOO_MANY_REVISIONS = "catalog generation exceeds the descriptor revision bound"

    @field_validator("connector_scope_revision", "task_policy_selection_ref")
    @classmethod
    def _reject_uncanonical(cls, value: str) -> str:
        if value != value.strip():
            raise CapabilityCatalogIdentityError(cls.Messages.UNCANONICAL)
        return value

    @model_validator(mode="after")
    def _generation_authenticates(self) -> Self:
        return self.verify()

    def verify(self) -> Self:
        """Recheck that the digest still authenticates the exact keyed inputs."""

        if self.generation_digest != canonical_json_sha256(self.digest_payload()):
            raise CapabilityCatalogIdentityError(self.Messages.DIGEST_MISMATCH)
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return every keyed identity input, excluding the digest itself."""

        return self.model_dump(mode="json", exclude={"generation_digest"})

    @property
    def generation_ref(self) -> str:
        """Return an opaque reference that discloses no keyed input."""

        return f"capability-catalog-generation://sha256/{self.generation_digest}"

    def is_same_generation(self, other: "CapabilityCatalogGeneration") -> bool:
        """Compare two generations for equality only; never for ordering."""

        return self.generation_digest == other.generation_digest

    @classmethod
    def fold_descriptor_revisions(
        cls,
        descriptor_revisions: Iterable[CatalogDescriptorRevision],
    ) -> tuple[str, int]:
        """Fold source revisions into one order-insensitive digest and count."""

        by_source: dict[str, str] = {}
        for entry in descriptor_revisions:
            existing = by_source.get(entry.source_id)
            if existing is not None and existing != entry.descriptor_revision:
                raise CapabilityCatalogIdentityError(cls.Messages.CONFLICTING_REVISION)
            by_source[entry.source_id] = entry.descriptor_revision
        if len(by_source) > _MAX_DESCRIPTOR_REVISIONS:
            raise CapabilityCatalogIdentityError(cls.Messages.TOO_MANY_REVISIONS)
        ordered = [
            {"source_id": source_id, "descriptor_revision": revision}
            for source_id, revision in sorted(by_source.items())
        ]
        return canonical_json_sha256(ordered), len(ordered)

    @classmethod
    def create(
        cls,
        *,
        subject_fingerprint: str,
        connector_scope_revision: str,
        task_policy_selection_ref: str,
        descriptor_revisions: Iterable[CatalogDescriptorRevision] = (),
    ) -> Self:
        """Derive the one reproducible identity for a set of trusted inputs."""

        digest, count = cls.fold_descriptor_revisions(descriptor_revisions)
        payload = {
            "schema_version": 1,
            "subject_fingerprint": subject_fingerprint,
            "connector_scope_revision": connector_scope_revision,
            "task_policy_selection_ref": task_policy_selection_ref,
            "descriptor_revision_digest": digest,
            "descriptor_revision_count": count,
        }
        draft = cls.model_construct(**payload, generation_digest=_ZERO_DIGEST)
        return cls(
            **payload,
            generation_digest=canonical_json_sha256(draft.digest_payload()),
        )


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
    """Content revision and expiration metadata for one scoped catalog.

    ``revision`` digests the projected catalog *content*.  The optional
    ``generation`` digests the trusted *inputs* the content was projected from,
    and is what later lanes revalidate an opaque ref against.  A catalog built
    without a generation simply cannot mint bound refs — that fails closed.
    """

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
    generation: CapabilityCatalogGeneration | None = None

    class Messages:
        """Safe public messages for catalog revision validation."""

        NAIVE_EXPIRY = "expires_at must be timezone-aware"
        SCOPE_DRIFT = (
            "catalog generation connector scope does not match the catalog revision"
        )

    @field_validator("expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(cls.Messages.NAIVE_EXPIRY)
        return value

    @model_validator(mode="after")
    def _generation_matches_scope(self) -> Self:
        if (
            self.generation is not None
            and self.generation.connector_scope_revision
            != self.connector_scope_revision
        ):
            raise CapabilityCatalogIdentityError(self.Messages.SCOPE_DRIFT)
        return self


class CapabilityCatalog(RuntimeContract):
    """Immutable catalog tied to an exact run subject and policy snapshot."""

    scope: CapabilityCatalogScope
    revision: CapabilityCatalogRevision
    entries: tuple[CapabilityIndexEntry, ...] = Field(default_factory=tuple)

    class Messages:
        """Safe public messages for catalog membership and binding failures."""

        NAIVE_NOW = "now must be timezone-aware"
        UNGENERATED = "this catalog has no generation and cannot bind refs"
        NOT_A_MEMBER = "that capability is not a member of this catalog"

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
            raise ValueError(self.Messages.NAIVE_NOW)
        return self.scope.matches(context) and now < self.revision.expires_at

    @property
    def generation(self) -> CapabilityCatalogGeneration | None:
        """Return the trusted input identity this catalog was projected from."""

        return self.revision.generation

    def bind_ref(self, capability_ref: str) -> "CapabilityRefBinding":
        """Bind one existing member ref to this catalog's generation.

        Binding is a *narrowing* operation: a ref that is not already a member
        of this authorization-projected catalog cannot be bound, and a catalog
        with no generation cannot bind at all.
        """

        generation = self.revision.generation
        if generation is None:
            raise CapabilityCatalogIdentityError(self.Messages.UNGENERATED)
        if not any(entry.capability_ref == capability_ref for entry in self.entries):
            raise CapabilityCatalogIdentityError(self.Messages.NOT_A_MEMBER)
        return CapabilityRefBinding.create(
            capability_ref=capability_ref,
            catalog_id=self.revision.catalog_id,
            catalog_revision=self.revision.revision,
            generation=generation,
        )


class CapabilityRefBinding(RuntimeContract):
    """One opaque capability ref bound to the catalog generation that minted it.

    This is the F3 half of the shared revision-binding rule: a reference
    captured at plan time must be re-resolved and reauthorized at use time, and
    a revision mismatch fails closed.  This module deliberately implements only
    the *binding*.  It does not define ``revalidate_at_use`` or its outcome
    vocabulary — the shared control-plane primitive owns those, and F3 binds to
    it rather than reimplementing staleness semantics.

    Field mapping onto that primitive:

    * ``capability_ref`` is the opaque reference;
    * ``catalog_id`` plus ``issued_generation`` is the issuing scope;
    * ``issued_generation.generation_digest`` is the revision it was minted
      against, compared for equality only; and
    * ``binding_digest`` is the digest that proves the binding.
    """

    schema_version: Literal[1] = 1
    capability_ref: str = Field(pattern=r"^cap_[0-9a-f]{32}$")
    catalog_id: str = Field(pattern=r"^cat_[0-9a-f]{32}$")
    catalog_revision: str = Field(pattern=r"^rev_[0-9a-f]{32}$")
    issued_generation: CapabilityCatalogGeneration
    binding_digest: str = Field(pattern=_SHA256_HEX_PATTERN)

    class Messages:
        """Safe public messages for ref-binding failures."""

        DIGEST_MISMATCH = "capability ref binding digest does not match its identity"

    @model_validator(mode="after")
    def _binding_authenticates(self) -> Self:
        return self.verify()

    def verify(self) -> Self:
        """Recheck that this ref is still provably bound to its generation."""

        self.issued_generation.verify()
        if self.binding_digest != canonical_json_sha256(self.digest_payload()):
            raise CapabilityCatalogIdentityError(self.Messages.DIGEST_MISMATCH)
        return self

    def digest_payload(self) -> dict[str, object]:
        """Return every bound identity field, excluding the digest itself."""

        return self.model_dump(mode="json", exclude={"binding_digest"})

    @property
    def binding_ref(self) -> str:
        """Return an opaque reference that discloses no capability identity."""

        return f"capability-ref-binding://sha256/{self.binding_digest}"

    def is_bound_to(self, generation: CapabilityCatalogGeneration) -> bool:
        """Return whether this ref was minted against ``generation`` exactly."""

        return self.issued_generation.is_same_generation(generation)

    @classmethod
    def create(
        cls,
        *,
        capability_ref: str,
        catalog_id: str,
        catalog_revision: str,
        generation: CapabilityCatalogGeneration,
    ) -> Self:
        """Mint one provable binding between a member ref and a generation."""

        payload = {
            "schema_version": 1,
            "capability_ref": capability_ref,
            "catalog_id": catalog_id,
            "catalog_revision": catalog_revision,
            "issued_generation": generation,
        }
        draft = cls.model_construct(**payload, binding_digest=_ZERO_DIGEST)
        return cls(
            **payload,
            binding_digest=canonical_json_sha256(draft.digest_payload()),
        )


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
