"""Strict contracts for a run-scoped, authorization-projected capability catalog.

This module is the one place F3 states a discovery *shape*: the opaque
reference format and the single keyed derivation that mints it, the compact
catalog, the bounded search answer, and the bounded second-tier expansion
result.  Executable policy lives elsewhere — projection in ``builder``, ranking
in ``ranker``, expansion in ``expansion``, configuration-resolved bounds in
``activation`` — so every one of those modules states a contract once and
reads it from here rather than restating it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
import hashlib
import hmac
import json
import re
from typing import (
    Annotated,
    Any,
    ClassVar,
    Literal,
    Protocol,
    Self,
    runtime_checkable,
)

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
_OPAQUE_TOKEN_PATTERN = r"^[!-~]+$"
_REFERENCE_KEY_MIN_BYTES = 32


class CapabilityReferenceFormat:
    """The one wire shape every opaque F3 token is minted in and validated as.

    :class:`HmacCapabilityReferenceMinter` emits exactly what these patterns
    admit, so a reference can never be minted in a shape a contract would then
    refuse, and the accepted width cannot drift away from the derived width.
    """

    CATALOG_PREFIX: ClassVar[str] = "cat"
    CAPABILITY_PREFIX: ClassVar[str] = "cap"
    REVISION_PREFIX: ClassVar[str] = "rev"
    HEX_CHARS: ClassVar[int] = 32
    PREFIX_CHARS: ClassVar[int] = 3
    TOKEN_LENGTH: ClassVar[int] = PREFIX_CHARS + 1 + HEX_CHARS

    @classmethod
    def pattern(cls, prefix: str) -> str:
        """Return the anchored pattern for one prefixed opaque token."""

        return rf"^{prefix}_[0-9a-f]{{{cls.HEX_CHARS}}}$"


_CATALOG_ID_PATTERN = CapabilityReferenceFormat.pattern(
    CapabilityReferenceFormat.CATALOG_PREFIX
)
_CAPABILITY_REF_PATTERN = CapabilityReferenceFormat.pattern(
    CapabilityReferenceFormat.CAPABILITY_PREFIX
)
_CATALOG_REVISION_PATTERN = CapabilityReferenceFormat.pattern(
    CapabilityReferenceFormat.REVISION_PREFIX
)


class CapabilitySearchBounds:
    """The one candidate ceiling a bounded search request and answer share.

    The request limit and every contract that carries the resulting candidates
    are the same bound seen from two sides; naming it once keeps a widened
    request from producing an answer the result contract cannot hold.
    """

    MAX_CANDIDATES: ClassVar[int] = 10


class CapabilityExpansionBounds:
    """The absolute ceilings every bounded second-tier expansion is held to.

    These are the hard structural limits, not the configured policy.  The
    configuration-resolved policy in
    :mod:`agent_runtime.capabilities.discovery.activation` may only choose a
    value *within* them, and :class:`CapabilityExpansionResult` refuses to
    represent a wider one — so a bound the policy can express is always a bound
    the result can carry.
    """

    MAX_SERVERS: ClassVar[int] = 8
    MAX_TOTAL_DEADLINE_SECONDS: ClassVar[float] = 120.0
    MAX_CAPABILITIES_PER_SERVER: ClassVar[int] = 256
    MAX_TRIGGER_CANDIDATES: ClassVar[int] = 10
    MAX_EXPANDED_CAPABILITIES: ClassVar[int] = 2_048


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


class CapabilityBridgeToolName(StrEnum):
    """The closed set of model-facing F3 bridge tool names.

    This enum is the *single* source of truth for what counts as a bridge tool.
    The reserved-name guard is derived from its members by iteration rather than
    from a parallel literal list, so a fourth bridge tool cannot be added
    without the guard covering it, and the recursion invariant cannot drift.
    """

    SEARCH_CAPABILITIES = "search_capabilities"
    DESCRIBE_CAPABILITY = "describe_capability"
    INVOKE_CAPABILITY = "invoke_capability"

    @classmethod
    def reserved_names(cls) -> frozenset[str]:
        """Return every name a discoverable capability may never claim."""

        return frozenset(member.value for member in cls)

    @classmethod
    def is_reserved(cls, name: str) -> bool:
        """Return whether ``name`` denotes a bridge tool under any casing."""

        return name.strip().casefold() in cls.reserved_names()


class CapabilityCatalogIdentityError(ValueError):
    """Typed, model-safe failure of a catalog identity or ref binding."""


class CapabilityBridgeRecursionError(ValueError):
    """A bridge tool was about to become reachable from another bridge tool.

    The F3 bridge must never be able to resolve to itself.  That invariant is
    enforced structurally at every construction site a bridge name could enter
    -- catalog membership and dispatch target -- rather than by a call-site
    check a later edit could route around.
    """

    class Messages:
        """Safe public messages for bridge-recursion refusals."""

        RESERVED_CATALOG_NAME = (
            "a bridge tool name can never be a member of a capability catalog"
        )
        RESERVED_TARGET_NAME = (
            "a bridge invocation can never dispatch to another bridge tool"
        )


class CapabilityExpansionError(ValueError):
    """Typed, model-safe failure of a bounded capability expansion."""


@runtime_checkable
class CapabilityReferenceMinter(Protocol):
    """Mint the opaque reference for one capability identity."""

    def mint(self, *, catalog_id: str, identity: str) -> str: ...


class HmacCapabilityReferenceMinter:
    """Derive every opaque F3 reference from one secret reference key.

    Catalog identifiers, catalog-member refs, and second-tier expanded refs are
    all the *same* derivation: a keyed HMAC-SHA256 over a namespaced identity,
    truncated to :attr:`CapabilityReferenceFormat.HEX_CHARS` behind a typed
    prefix.  Keeping it in one place is what makes an expanded capability
    indistinguishable from a catalog member to the model, and what stops the
    catalog builder and the second-tier expander from drifting into two
    derivations that could mint the same ref for different inputs.

    Identities are namespaced by their minting path and are never parsed back.
    The catalog builder mints ``{source}:{source_id}:{name}``; the expander
    mints ``mcp_server:tool:{owner_ref}:{tool_name}``.  Because an MCP server
    name and an MCP tool name are both colon-free slugs, and ``owner_ref`` is
    itself a key-derived token nobody can predict, no card an operator can
    register reproduces an expanded identity.
    """

    MIN_KEY_BYTES: ClassVar[int] = _REFERENCE_KEY_MIN_BYTES

    class Messages:
        """Safe public messages for reference-minter construction."""

        # A nested class body cannot see the enclosing class namespace, so the
        # bound is stated once at module scope and read from there by both.
        WEAK_KEY = (
            f"reference_key must contain at least {_REFERENCE_KEY_MIN_BYTES} bytes"
        )

    def __init__(self, *, reference_key: bytes) -> None:
        if len(reference_key) < self.MIN_KEY_BYTES:
            raise CapabilityExpansionError(self.Messages.WEAK_KEY)
        self._reference_key = bytes(reference_key)

    def mint_catalog_id(self, *, scope_identity: str) -> str:
        """Return the opaque ``cat_`` identifier for one catalog scope."""

        return self._mint(CapabilityReferenceFormat.CATALOG_PREFIX, scope_identity)

    def mint(self, *, catalog_id: str, identity: str) -> str:
        """Return an opaque ``cap_`` reference for one capability identity.

        The catalog identifier is folded in, so the same capability identity in
        two catalogs is two references and neither is portable between them.
        """

        return self._mint(
            CapabilityReferenceFormat.CAPABILITY_PREFIX,
            f"{catalog_id}:{identity}",
        )

    def keyed_hexdigest(self, payload: bytes) -> str:
        """Return the one keyed digest every F3 identity derivation goes through."""

        return hmac.new(self._reference_key, payload, hashlib.sha256).hexdigest()

    def _mint(self, prefix: str, identity: str) -> str:
        digest = self.keyed_hexdigest(identity.encode("utf-8"))
        return f"{prefix}_{digest[: CapabilityReferenceFormat.HEX_CHARS]}"


class CapabilitySubjectFingerprint:
    """Derive the protected subject identity a catalog generation is keyed to.

    The shared revision-binding primitive constrains a bound scope's subject to
    lowercase SHA-256 hexadecimal, so raw organization and user identifiers are
    never carried into a generation, a ref, or an event.  Derivation is keyed
    and domain-separated: the same subject reproduces the same fingerprint, and
    a fingerprint minted for another purpose cannot be replayed here.

    The fingerprint is derived from the *verified* runtime context rather than
    accepted from a caller, so no call site can key a catalog to a subject it
    did not authenticate.  It goes through
    :class:`HmacCapabilityReferenceMinter`, so subject fingerprints and opaque
    refs can never be produced from different key-strength bars.
    """

    PURPOSE: ClassVar[str] = "capability-catalog-subject-v1"

    def __init__(self, *, reference_key: bytes) -> None:
        self._minter = HmacCapabilityReferenceMinter(reference_key=reference_key)

    def derive(self, context: AgentRuntimeContext) -> str:
        """Return the domain-separated fingerprint of one verified subject."""

        payload = json.dumps(
            {
                "purpose": self.PURPOSE,
                "org_id": context.org_id,
                "user_id": context.user_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._minter.keyed_hexdigest(payload)


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
    """Schema-free, model-searchable metadata for one authorized source record.

    Catalog membership is the *only* way a capability becomes resolvable by the
    bridge, so refusing a reserved bridge name here makes bridge recursion
    unrepresentable for every construction path -- builder, adapter, or test.
    """

    capability_ref: str = Field(
        pattern=_CAPABILITY_REF_PATTERN,
        min_length=CapabilityReferenceFormat.TOKEN_LENGTH,
        max_length=CapabilityReferenceFormat.TOKEN_LENGTH,
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

    @model_validator(mode="after")
    def _never_names_a_bridge_tool(self) -> Self:
        if CapabilityBridgeToolName.is_reserved(self.stable_name):
            raise CapabilityBridgeRecursionError(
                CapabilityBridgeRecursionError.Messages.RESERVED_CATALOG_NAME
            )
        return self


class CapabilityCatalogRevision(RuntimeContract):
    """Content revision and expiration metadata for one scoped catalog.

    ``revision`` digests the projected catalog *content*.  The optional
    ``generation`` digests the trusted *inputs* the content was projected from,
    and is what later lanes revalidate an opaque ref against.  A catalog built
    without a generation simply cannot mint bound refs — that fails closed.
    """

    catalog_id: str = Field(
        pattern=_CATALOG_ID_PATTERN,
        min_length=CapabilityReferenceFormat.TOKEN_LENGTH,
        max_length=CapabilityReferenceFormat.TOKEN_LENGTH,
    )
    revision: str = Field(
        pattern=_CATALOG_REVISION_PATTERN,
        min_length=CapabilityReferenceFormat.TOKEN_LENGTH,
        max_length=CapabilityReferenceFormat.TOKEN_LENGTH,
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
    the *binding*.  It does not define ``revalidate_at_use``, its outcome
    vocabulary, or any predicate that answers "is this still current" — the
    shared control-plane primitive owns those, and
    :class:`~agent_runtime.capabilities.discovery.revision_authority.CapabilityRefRevisionBinding`
    projects this binding onto it.  Asking a binding about its own currency is
    exactly the second staleness implementation Step RB exists to prevent.

    Field mapping onto that primitive:

    * ``capability_ref`` is the opaque reference;
    * ``issued_generation.generation_ref`` is the bound catalog-generation
      scope, alongside the run subject;
    * ``issued_generation.generation_digest`` is the revision it was minted
      against, compared for equality only; and
    * ``binding_digest`` is the digest that proves the binding.
    """

    schema_version: Literal[1] = 1
    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    catalog_id: str = Field(pattern=_CATALOG_ID_PATTERN)
    catalog_revision: str = Field(pattern=_CATALOG_REVISION_PATTERN)
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
    limit: PositiveInt = Field(default=5, le=CapabilitySearchBounds.MAX_CANDIDATES)
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

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
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

    catalog_id: str = Field(pattern=_CATALOG_ID_PATTERN)
    catalog_revision: str = Field(pattern=_CATALOG_REVISION_PATTERN)
    query_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scanned_count: NonNegativeInt
    candidates: tuple[CapabilityCandidate, ...] = Field(
        default_factory=tuple,
        max_length=CapabilitySearchBounds.MAX_CANDIDATES,
    )


class RankedCapabilitySelection(RuntimeContract):
    """Bounded ranked candidates plus how many entries were actually scored.

    This is the catalog-free half of a search result.  It exists so the second
    discovery tier can rank newly expanded records with the same deterministic
    scorer and merge them into one bounded answer without pretending the
    expanded records are catalog members.
    """

    scanned_count: NonNegativeInt = 0
    candidates: tuple[CapabilityCandidate, ...] = Field(
        default_factory=tuple,
        max_length=CapabilitySearchBounds.MAX_CANDIDATES,
    )


class CapabilityExpansionState(StrEnum):
    """Closed per-server expansion outcomes; only one of them admits records."""

    EXPANDED = "expanded"
    UNAVAILABLE = "unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class ExpandedCapability(RuntimeContract):
    """One schema-free capability projected from a successfully loaded server.

    ``owner_capability_ref`` is the catalog ref of the *server card* this
    capability came from. It is what makes the narrowing invariant checkable:
    a capability is admissible only while its owner is recorded as expanded.
    """

    owner_capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    server_name: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    entry: CapabilityIndexEntry


class CapabilityExpansionOutcome(RuntimeContract):
    """Per-server disclosure of what expansion did, without leaking why."""

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    state: CapabilityExpansionState
    admitted_count: NonNegativeInt = 0

    class Messages:
        """Safe public messages for outcome invariants."""

        UNEXPANDED_ADMISSION = "only an expanded server may admit capabilities"

    @model_validator(mode="after")
    def _only_expanded_servers_admit(self) -> Self:
        if self.admitted_count and self.state is not CapabilityExpansionState.EXPANDED:
            raise CapabilityExpansionError(self.Messages.UNEXPANDED_ADMISSION)
        return self


class CapabilityExpansionResult(RuntimeContract):
    """Bounded result of one expansion; widening is structurally unrepresentable.

    The validators are the enforcement point for the lane's central property.
    A result can never claim more admitted servers than the configured ``K``,
    can never carry a capability whose owner did not reach
    :attr:`CapabilityExpansionState.EXPANDED`, and can never carry more
    capabilities than the expanded servers actually admitted.
    """

    max_servers: PositiveInt = Field(le=CapabilityExpansionBounds.MAX_SERVERS)
    considered_count: NonNegativeInt = 0
    admitted_count: NonNegativeInt = 0
    deadline_exceeded: bool = False
    outcomes: tuple[CapabilityExpansionOutcome, ...] = Field(
        default_factory=tuple,
        max_length=CapabilityExpansionBounds.MAX_SERVERS,
    )
    capabilities: tuple[ExpandedCapability, ...] = Field(
        default_factory=tuple,
        max_length=CapabilityExpansionBounds.MAX_EXPANDED_CAPABILITIES,
    )

    class Messages:
        """Safe public messages for expansion-result invariants."""

        OVER_BUDGET = "expansion admitted more servers than the configured bound"
        OVER_CONSIDERED = "expansion admitted more servers than it considered"
        OUTCOME_COUNT = "expansion must record one outcome per admitted server"
        DUPLICATE_OUTCOME = "expansion cannot record two outcomes for one server"
        UNOWNED_CAPABILITY = (
            "an expanded capability must belong to a server that expanded"
        )
        ADMISSION_MISMATCH = (
            "expanded capability count must equal the admitted server totals"
        )

    @model_validator(mode="after")
    def _partial_failure_only_narrows(self) -> Self:
        if self.admitted_count > self.max_servers:
            raise CapabilityExpansionError(self.Messages.OVER_BUDGET)
        if self.admitted_count > self.considered_count:
            raise CapabilityExpansionError(self.Messages.OVER_CONSIDERED)
        if len(self.outcomes) != self.admitted_count:
            raise CapabilityExpansionError(self.Messages.OUTCOME_COUNT)
        refs = [outcome.capability_ref for outcome in self.outcomes]
        if len(refs) != len(set(refs)):
            raise CapabilityExpansionError(self.Messages.DUPLICATE_OUTCOME)
        expanded_refs = {
            outcome.capability_ref
            for outcome in self.outcomes
            if outcome.state is CapabilityExpansionState.EXPANDED
        }
        if any(
            capability.owner_capability_ref not in expanded_refs
            for capability in self.capabilities
        ):
            raise CapabilityExpansionError(self.Messages.UNOWNED_CAPABILITY)
        admitted_total = sum(outcome.admitted_count for outcome in self.outcomes)
        if admitted_total != len(self.capabilities):
            raise CapabilityExpansionError(self.Messages.ADMISSION_MISMATCH)
        return self

    @property
    def expanded_count(self) -> int:
        """Return how many admitted servers actually produced capabilities."""

        return sum(
            1
            for outcome in self.outcomes
            if outcome.state is CapabilityExpansionState.EXPANDED
        )

    @classmethod
    def empty(cls, *, max_servers: int) -> Self:
        """Return the result of an expansion that admitted nothing."""

        return cls(max_servers=max_servers)


class TwoTierCapabilitySearchResult(RuntimeContract):
    """A bounded search answer plus the audit of what tier two actually did."""

    search: CapabilitySearchResult
    expansion: CapabilityExpansionResult


class CapabilityDiscoveryErrorCode(StrEnum):
    """Stable model-safe failure classes for discovery bridge calls.

    A bridge tool reference is deliberately *not* given its own code.  Bridge
    names can never be catalog members, so asking to invoke one is answered by
    :attr:`CAPABILITY_NOT_FOUND` exactly like any other unknown reference, and
    the model cannot probe for the bridge's own existence.
    """

    INVALID_REQUEST = "invalid_request"
    CATALOG_INACTIVE = "catalog_inactive"
    CAPABILITY_NOT_FOUND = "capability_not_found"
    CAPABILITY_STALE = "capability_stale"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    EXECUTION_FAILED = "execution_failed"


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

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)


class CapabilityParameterHint(RuntimeContract):
    """Schema-free name/type hint; never a JSON schema or invocation contract."""

    name: str = Field(min_length=1, max_length=96)
    type_hint: str | None = Field(default=None, min_length=1, max_length=96)


class CapabilityDescription(RuntimeContract):
    """Bounded compact metadata for one member of the active catalog."""

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
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

    catalog_id: str = Field(pattern=_CATALOG_ID_PATTERN)
    catalog_revision: str = Field(pattern=_CATALOG_REVISION_PATTERN)
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


class CapabilityArgumentBounds:
    """The structural bounds every model-supplied argument object must satisfy.

    Arguments are untrusted model output.  These bounds are deliberately about
    *shape and size only*; validating arguments against the revalidated
    descriptor schema happens at invocation, behind the Operation Gateway.
    """

    MAX_KEYS = 64
    MAX_DEPTH = 8
    MAX_SERIALIZED_BYTES = 16_384
    KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"

    class Messages:
        """Safe public messages for argument-bound failures."""

        NOT_A_MAPPING = "capability arguments must be a JSON object"
        TOO_MANY_KEYS = "capability arguments exceed the argument-count bound"
        INVALID_KEY = "capability argument names are not well formed"
        TOO_DEEP = "capability arguments exceed the argument-nesting bound"
        NOT_SERIALIZABLE = "capability arguments must be JSON serializable"
        TOO_LARGE = "capability arguments exceed the argument-size bound"

    @classmethod
    def validate(cls, value: object) -> dict[str, Any]:
        """Return the bounded argument object, or raise a safe typed failure."""

        if not isinstance(value, Mapping):
            raise ValueError(cls.Messages.NOT_A_MAPPING)
        arguments = dict(value)
        if len(arguments) > cls.MAX_KEYS:
            raise ValueError(cls.Messages.TOO_MANY_KEYS)
        key_pattern = re.compile(cls.KEY_PATTERN)
        if any(
            not isinstance(key, str) or key_pattern.match(key) is None
            for key in arguments
        ):
            raise ValueError(cls.Messages.INVALID_KEY)
        cls._reject_excess_depth(arguments)
        try:
            encoded = json.dumps(
                arguments,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(cls.Messages.NOT_SERIALIZABLE) from exc
        if len(encoded) > cls.MAX_SERIALIZED_BYTES:
            raise ValueError(cls.Messages.TOO_LARGE)
        return arguments

    @classmethod
    def _reject_excess_depth(cls, arguments: Mapping[str, Any]) -> None:
        """Bound nesting iteratively so a hostile shape cannot recurse."""

        pending: list[tuple[object, int]] = [(arguments, 1)]
        while pending:
            value, depth = pending.pop()
            if depth > cls.MAX_DEPTH:
                raise ValueError(cls.Messages.TOO_DEEP)
            if isinstance(value, Mapping):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend((item, depth + 1) for item in value)


class CapabilityInvokeRequest(RuntimeContract):
    """Bounded invocation request naming one opaque catalog member."""

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_OPAQUE_TOKEN_PATTERN,
    )

    @field_validator("arguments", mode="before")
    @classmethod
    def _bounded_arguments(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        return CapabilityArgumentBounds.validate(value)


class CapabilityInvocationTarget(RuntimeContract):
    """The only value a bridge invocation may dispatch to.

    This is the third structural chokepoint of the bridge-recursion guard.  A
    target can only be produced from a catalog entry, and its own validator
    re-asserts the reserved-name refusal, so even an entry forged past
    validation (``model_construct``) cannot reach an executor.
    """

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    stable_name: str = Field(min_length=1, max_length=256)
    source: CapabilitySource
    connector_label: str = Field(min_length=1, max_length=256)
    effect_class: CatalogEffectClass
    approval_cue: ApprovalCue
    descriptor_revision: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _never_targets_a_bridge_tool(self) -> Self:
        if CapabilityBridgeToolName.is_reserved(self.stable_name):
            raise CapabilityBridgeRecursionError(
                CapabilityBridgeRecursionError.Messages.RESERVED_TARGET_NAME
            )
        return self

    @classmethod
    def from_catalog_entry(cls, entry: CapabilityIndexEntry) -> Self:
        """Project one authorized catalog member into a dispatchable target."""

        return cls(
            capability_ref=entry.capability_ref,
            stable_name=entry.stable_name,
            source=entry.source,
            connector_label=entry.connector_label,
            effect_class=entry.effect_class,
            approval_cue=entry.approval_cue,
            descriptor_revision=entry.descriptor_revision,
        )


class CapabilityInvocationStatus(StrEnum):
    """Closed model-visible disposition of one bridge invocation."""

    COMPLETED = "completed"
    STAGED = "staged"
    REFUSED = "refused"


class CapabilityInvocationReceipt(RuntimeContract):
    """Body-free receipt returned by the non-model capability executor.

    Raw results stay behind :attr:`invocation_ref`.  The receipt carries an
    opaque reference, a closed status, and one bounded safe summary — never
    connector payloads, arguments, or credentials.
    """

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    invocation_ref: str = Field(
        min_length=1,
        max_length=256,
        pattern=_OPAQUE_TOKEN_PATTERN,
    )
    status: CapabilityInvocationStatus
    safe_summary: str = Field(min_length=1, max_length=512)


class CapabilityInvokeResult(RuntimeContract):
    """Receipt plus the catalog revision that made the opaque ref meaningful."""

    catalog_id: str = Field(pattern=_CATALOG_ID_PATTERN)
    catalog_revision: str = Field(pattern=_CATALOG_REVISION_PATTERN)
    receipt: CapabilityInvocationReceipt


class CapabilityInvokeToolResult(RuntimeContract):
    """Exactly-one-outcome envelope returned by the invoke adapter."""

    invocation: CapabilityInvokeResult | None = None
    error: CapabilityDiscoveryError | None = None

    @model_validator(mode="after")
    def _require_exactly_one_outcome(self) -> Self:
        if (self.invocation is None) == (self.error is None):
            msg = "invoke result must contain exactly one outcome"
            raise ValueError(msg)
        return self

    @classmethod
    def ok(cls, invocation: CapabilityInvokeResult) -> Self:
        """Return a successful bounded invocation receipt."""

        return cls(invocation=invocation)

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


@runtime_checkable
class CapabilityExecutorPort(Protocol):
    """Non-model executor that enters the ordinary Operation Gateway.

    The port accepts only a :class:`CapabilityInvocationTarget`, so the type
    system — not a call-site string comparison — is what keeps a bridge tool
    from ever being dispatched.  Implementations own descriptor re-resolution,
    canonical argument validation against the revalidated schema, approval, and
    budget; this lane supplies the bounded seam and fails closed without one.
    """

    async def execute(
        self,
        *,
        target: CapabilityInvocationTarget,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
        runtime_context: AgentRuntimeContext,
    ) -> CapabilityInvocationReceipt: ...
