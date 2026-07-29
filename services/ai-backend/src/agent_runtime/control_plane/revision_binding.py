"""One narrowing revalidation primitive for revision-bound references.

F3 capability refs, F5 evidence refs, F8 descriptor revisions, F9 child grants,
and F11 target manifests all obey the same rule: a reference captured at plan
time must be re-resolved and reauthorized at use time, and a revision mismatch
fails closed.  Section 6.1 of the F1-F12 PRD already forbids treating the run
snapshot as an authorization cache.  This module stops that rule from being
reimplemented once per domain with five subtly different staleness semantics.

What the primitive is
---------------------

* :class:`RevisionBoundRef` binds an opaque reference to the closed scope it was
  issued for, the revision it was minted against, and a digest over that exact
  body.  Minting is a pure function of the bound body: the same inputs always
  produce the same digest, and no clock, counter, or nonce participates.
* :class:`RevisionRevalidatorPort` is the single call-time protocol.  Each
  domain supplies a :class:`RevisionAuthorityPort` resolver; the shared
  :class:`RevisionBindingRevalidator` owns the staleness semantics.
* :class:`RevalidationOutcome` is closed, and every outcome carries a stable
  low-cardinality :class:`RevalidationReason`.

What the primitive is not
-------------------------

It introduces no authority and caches no authorization decision.  It can only
narrow: every existing call-time boundary (Operation Gateway, connector scope,
effect policy, evidence access) stays exactly where it is.  The binding digest
proves that a reference was not edited after it was minted; it never proves that
minting was authorized.  Authority is always re-derived from the domain resolver
at use time.

No bodies cross this boundary.  Contracts carry identifiers, opaque revisions,
digests, closed scopes, and reason codes -- never prompt text, tool arguments or
results, credentials, host paths, or evidence text.  The accepted character set
for every opaque token is printable ASCII without whitespace, so a body cannot
be smuggled through a reference.

Two coercions are deliberately impossible, because "fail closed" has to survive
a careless call site:

* revisions expose no ordering operators, so a caller-supplied timestamp,
  counter, or "newer looking" value can never imply freshness; and
* neither :class:`RevalidationOutcome` nor :class:`RevalidationDecision` can be
  coerced to a boolean, so ``unavailable`` cannot be mistaken for success.  Use
  :attr:`RevalidationDecision.is_current` or
  :meth:`RevalidationDecision.require_current`.

Two shapes come from adoption rather than design (RB.3), because two adopters
bound to the primitive independently and hit the same two limits:

* **The run dimension is optional on both sides.**  A process-wide,
  subject-scoped consumer has no run at the layer where it revalidates, and
  fabricating an inert sentinel to satisfy a mandatory field is a lie the
  primitive should not require.  :attr:`RevisionUseContext.run_id` is therefore
  optional exactly as :attr:`RevisionBoundScope.run_id` is.  Relaxing the field
  does not relax the check: a policy that *requires* a dimension now demands it
  of the use context as well as the reference, so a call site that asks to be
  fenced by run and then presents no run is refused structurally.
* **An authority may be handed an opaque resolution handle.**  The subject
  fingerprint is deliberately one-way, so an authority that must ask a backend
  keyed by the original identity cannot recover it from the scope, and every
  adopter was otherwise forced into a scope-keyed side registry populated at
  mint time -- with the lifetime management that implies.
  :meth:`RevisionRevalidatorPort.revalidate_at_use` therefore accepts an
  optional ``resolution_handle`` and forwards it to the domain authority
  untouched.  It is **not** part of the bound body: it is never digested, never
  compared, never stored on the revalidator, and never placed in a decision, so
  a reference and a decision carry exactly what they carried before it existed.
  That is what keeps the no-raw-identity property intact -- the handle travels
  from an adopter's call site to that same adopter's authority, both of which
  already hold the verified identity, and nothing in between retains it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OPAQUE_TOKEN_PATTERN = r"^[!-~]+$"
_MAX_TOKEN_LENGTH = 256
_MAX_REF_LENGTH = 512

Sha256Hex = Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]
"""Lowercase SHA-256 hexadecimal digest."""

ControlToken = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=_MAX_TOKEN_LENGTH,
        pattern=_OPAQUE_TOKEN_PATTERN,
    ),
]
"""Bounded printable-ASCII control identifier that can never carry a body."""

OpaqueRefValue = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=_MAX_REF_LENGTH,
        pattern=_OPAQUE_TOKEN_PATTERN,
    ),
]
"""Bounded printable-ASCII reference value that can never carry a body."""

RevisionResolutionHandle = object
"""An adopter-owned handle the primitive forwards to that adopter's authority.

Typed as :class:`object` on purpose: the primitive has no vocabulary for its
contents and must not acquire one.  It is never interpreted, compared,
digested, serialized, logged, or retained here -- forwarding it verbatim is the
whole contract.  Adopters use it to carry whatever their own authority needs to
resolve directly (the verified identity a fingerprint destroyed, a revision the
caller already resolved, a connection), which is what removes the scope-keyed
side registry the port previously forced on every adopter.

It is deliberately *not* a field of :class:`RevisionBinding`,
:class:`RevisionBoundScope`, or :class:`RevalidationDecision`.  Those are the
values that get minted, persisted, replayed, and logged; keeping the handle out
of all three is what preserves the no-raw-identity property.
"""


class RevisionBindingError(RuntimeError):
    """Base class for typed revision-binding failures with safe messages."""


class RevisionOrderingNotSupported(TypeError):
    """A caller tried to order revisions instead of comparing them for equality."""

    MESSAGE: ClassVar[str] = (
        "revisions compare by equality only; ordering never implies freshness"
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class RevalidationBooleanCoercion(TypeError):
    """A caller tried to coerce a revalidation result to a boolean."""

    MESSAGE: ClassVar[str] = (
        "revalidation results are not boolean; check is_current or call "
        "require_current so an unavailable authority cannot read as success"
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class RevisionBoundRefNotCurrent(RevisionBindingError):
    """A revision-bound reference was used after it stopped being current."""

    MESSAGE_TEMPLATE: ClassVar[str] = (
        "revision-bound reference is not usable (outcome={outcome}, reason={reason})"
    )

    def __init__(
        self,
        *,
        outcome: "RevalidationOutcome",
        reason: "RevalidationReason",
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        super().__init__(
            self.MESSAGE_TEMPLATE.format(outcome=outcome.value, reason=reason.value)
        )


class BoundRevision(RuntimeContract):
    """An opaque control-plane revision that is compared for equality only.

    Providers emit ETags, content hashes, database sequence identifiers, and
    timestamps.  None of those shapes may be interpreted here: a value that
    merely *looks* newer is not evidence of freshness, and a caller-supplied
    ordering is exactly the attack this primitive exists to prevent.  Ordering
    operators therefore raise :class:`RevisionOrderingNotSupported` rather than
    being merely undocumented.
    """

    value: OpaqueRefValue

    def __lt__(self, other: object) -> bool:
        raise RevisionOrderingNotSupported

    __le__ = __lt__
    __gt__ = __lt__
    __ge__ = __lt__


class RevisionScopeDimension(StrEnum):
    """Closed set of dimensions a reference may be narrowed to."""

    SUBJECT = "subject"
    RUN = "run"
    CATALOG_GENERATION = "catalog_generation"


def _bound_dimensions(
    *,
    run_id: str | None,
    catalog_generation: str | None,
) -> frozenset[RevisionScopeDimension]:
    """Return the dimensions a subject-anchored value is actually narrowed to.

    Shared by the issuing scope and the use context so the two can never drift
    into disagreeing about what "bound to a dimension" means.  They stay
    separate types rather than sharing a base: a scope and a use context must
    not be structurally interchangeable, because confusing them would mean
    checking a reference against itself.
    """

    bound = {RevisionScopeDimension.SUBJECT}
    if run_id is not None:
        bound.add(RevisionScopeDimension.RUN)
    if catalog_generation is not None:
        bound.add(RevisionScopeDimension.CATALOG_GENERATION)
    return frozenset(bound)


class RevisionBoundScope(RuntimeContract):
    """The closed issuing scope a reference is bound to.

    ``subject_fingerprint`` is mandatory, so an unscoped reference is not
    representable and cross-subject replay is structurally impossible.  The
    optional dimensions narrow further; ``None`` means the reference was never
    bound to that dimension, never that the dimension is satisfied.
    """

    subject_fingerprint: Sha256Hex
    run_id: ControlToken | None = None
    catalog_generation: ControlToken | None = None

    @property
    def dimensions(self) -> frozenset[RevisionScopeDimension]:
        """Return every dimension this scope is actually bound to."""

        return _bound_dimensions(
            run_id=self.run_id,
            catalog_generation=self.catalog_generation,
        )

    def covers(self, required: frozenset[RevisionScopeDimension]) -> bool:
        """Return whether this scope is at least as narrow as ``required``."""

        return required <= self.dimensions


class RevisionUseContext(RuntimeContract):
    """Verified runtime facts at the moment a reference is used.

    Adapters project this from already-verified session/run state.  Model
    output, tool results, MCP descriptors, and other untrusted sources must
    never populate it -- a forged context would widen nothing here, but it would
    defeat the scope checks this primitive performs on the caller's behalf.

    Every dimension except the subject is optional, matching
    :class:`RevisionBoundScope`: a process-wide, subject-scoped consumer has no
    run at the layer where it revalidates, and inventing an inert sentinel to
    satisfy a mandatory field would state a verified fact that nobody verified.
    ``None`` means "this context carries no such fact", never "the dimension is
    satisfied" -- a reference bound to a dimension the context cannot supply is
    refused, and so is a policy that requires one.
    """

    subject_fingerprint: Sha256Hex
    run_id: ControlToken | None = None
    catalog_generation: ControlToken | None = None

    @property
    def dimensions(self) -> frozenset[RevisionScopeDimension]:
        """Return every dimension this context actually carries a fact for."""

        return _bound_dimensions(
            run_id=self.run_id,
            catalog_generation=self.catalog_generation,
        )

    def covers(self, required: frozenset[RevisionScopeDimension]) -> bool:
        """Return whether this context supplies every ``required`` dimension."""

        return required <= self.dimensions


class RevisionBinding(RuntimeContract):
    """The exact immutable body covered by a reference's binding digest."""

    schema_version: Literal[1] = 1
    feature: AgentQualityFeature
    opaque_ref: OpaqueRefValue
    scope: RevisionBoundScope
    revision: BoundRevision

    @property
    def digest(self) -> str:
        """Return the reproducible digest of this bound body.

        The digest covers the complete body and nothing else -- no clock, no
        counter, no nonce -- so minting the same binding twice always produces
        the same digest.
        """

        return canonical_json_sha256(self.model_dump(mode="json"))


class RevisionBoundRef(RuntimeContract):
    """An opaque reference bound to one scope and one minted-against revision."""

    class Messages:
        """Validation messages owned by this contract."""

        DIGEST_MISMATCH: ClassVar[str] = (
            "binding digest does not match the bound reference body"
        )

    schema_version: Literal[1] = 1
    binding: RevisionBinding
    binding_digest: Sha256Hex

    @model_validator(mode="after")
    def _digest_matches(self) -> "RevisionBoundRef":
        if self.binding_digest != self.binding.digest:
            raise ValueError(self.Messages.DIGEST_MISMATCH)
        return self

    @classmethod
    def mint(
        cls,
        *,
        feature: AgentQualityFeature,
        opaque_ref: str,
        scope: RevisionBoundScope,
        revision: BoundRevision,
    ) -> "RevisionBoundRef":
        """Bind ``opaque_ref`` to ``scope`` at ``revision`` reproducibly."""

        binding = RevisionBinding(
            feature=feature,
            opaque_ref=opaque_ref,
            scope=scope,
            revision=revision,
        )
        return cls(binding=binding, binding_digest=binding.digest)

    @property
    def feature(self) -> AgentQualityFeature:
        """Return the single domain allowed to consume this reference."""

        return self.binding.feature

    @property
    def opaque_ref(self) -> str:
        """Return the domain's opaque reference value."""

        return self.binding.opaque_ref

    @property
    def scope(self) -> RevisionBoundScope:
        """Return the closed issuing scope this reference is bound to."""

        return self.binding.scope

    @property
    def revision(self) -> BoundRevision:
        """Return the revision this reference was minted against."""

        return self.binding.revision

    @property
    def computed_binding_digest(self) -> str:
        """Return the digest recomputed from the presented body."""

        return self.binding.digest

    @property
    def binding_is_intact(self) -> bool:
        """Return whether the presented body still matches its digest.

        A validated reference always satisfies this.  It is rechecked at use
        time because a reference may have been rebuilt without validation --
        for example through ``model_copy`` or ``model_construct`` -- after it
        left the minting path.
        """

        return self.binding_digest == self.computed_binding_digest


class RevalidationOutcome(StrEnum):
    """Closed outcome of revalidating a revision-bound reference at use."""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    OUT_OF_SCOPE = "out_of_scope"
    UNAVAILABLE = "unavailable"

    @property
    def admits_use(self) -> bool:
        """Return whether this outcome permits the guarded operation."""

        return self is RevalidationOutcome.CURRENT

    def __bool__(self) -> bool:
        raise RevalidationBooleanCoercion


class RevalidationReason(StrEnum):
    """Stable low-cardinality reason codes carried by every outcome."""

    REVISION_MATCHES = "revision_matches"
    REVISION_CHANGED = "revision_changed"
    AUTHORITY_REVOKED = "authority_revoked"
    BINDING_DIGEST_MISMATCH = "binding_digest_mismatch"
    FEATURE_MISMATCH = "feature_mismatch"
    SCOPE_DIMENSION_MISSING = "scope_dimension_missing"
    SUBJECT_MISMATCH = "subject_mismatch"
    RUN_MISMATCH = "run_mismatch"
    CATALOG_GENERATION_MISMATCH = "catalog_generation_mismatch"
    UNKNOWN_REFERENCE = "unknown_reference"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    AUTHORITY_ERROR = "authority_error"
    AUTHORITY_CONTRACT_VIOLATION = "authority_contract_violation"

    @property
    def outcome(self) -> RevalidationOutcome:
        """Return the single outcome this reason is allowed to carry."""

        return RevalidationReasonOutcomes.BY_REASON[self]


class RevalidationReasonOutcomes:
    """Closed reason-to-outcome map, so no adapter can invent a pairing."""

    BY_REASON: ClassVar[Mapping[RevalidationReason, RevalidationOutcome]] = (
        MappingProxyType(
            {
                RevalidationReason.REVISION_MATCHES: RevalidationOutcome.CURRENT,
                RevalidationReason.REVISION_CHANGED: RevalidationOutcome.SUPERSEDED,
                RevalidationReason.AUTHORITY_REVOKED: RevalidationOutcome.REVOKED,
                RevalidationReason.BINDING_DIGEST_MISMATCH: (
                    RevalidationOutcome.OUT_OF_SCOPE
                ),
                RevalidationReason.FEATURE_MISMATCH: RevalidationOutcome.OUT_OF_SCOPE,
                RevalidationReason.SCOPE_DIMENSION_MISSING: (
                    RevalidationOutcome.OUT_OF_SCOPE
                ),
                RevalidationReason.SUBJECT_MISMATCH: RevalidationOutcome.OUT_OF_SCOPE,
                RevalidationReason.RUN_MISMATCH: RevalidationOutcome.OUT_OF_SCOPE,
                RevalidationReason.CATALOG_GENERATION_MISMATCH: (
                    RevalidationOutcome.OUT_OF_SCOPE
                ),
                RevalidationReason.UNKNOWN_REFERENCE: RevalidationOutcome.OUT_OF_SCOPE,
                RevalidationReason.AUTHORITY_UNAVAILABLE: (
                    RevalidationOutcome.UNAVAILABLE
                ),
                RevalidationReason.AUTHORITY_ERROR: RevalidationOutcome.UNAVAILABLE,
                RevalidationReason.AUTHORITY_CONTRACT_VIOLATION: (
                    RevalidationOutcome.UNAVAILABLE
                ),
            }
        )
    )


class RevalidationPolicy(RuntimeContract):
    """Call-site policy that may only narrow what a reference admits.

    ``feature`` is the domain the call site is acting for; a reference minted
    for another feature is out of scope, so an F5 evidence ref can never be
    replayed on an F3 capability path.  ``required_dimensions`` lets a call site
    demand a narrower binding than the reference may carry -- it can never
    accept a broader one.
    """

    schema_version: Literal[1] = 1
    feature: AgentQualityFeature
    # ``validate_default`` keeps the subject floor structural: an omitted value
    # must be narrowed exactly like a supplied one.
    required_dimensions: frozenset[RevisionScopeDimension] = Field(
        default_factory=frozenset,
        validate_default=True,
    )

    @field_validator("required_dimensions", mode="after")
    @classmethod
    def _subject_is_always_required(
        cls,
        value: frozenset[RevisionScopeDimension],
    ) -> frozenset[RevisionScopeDimension]:
        return value | {RevisionScopeDimension.SUBJECT}


class RevalidationDecision(RuntimeContract):
    """Body-free result of revalidating one reference at use time."""

    class Messages:
        """Validation messages owned by this contract."""

        REASON_OUTCOME_MISMATCH: ClassVar[str] = (
            "revalidation reason does not belong to the recorded outcome"
        )
        CURRENT_REQUIRES_REVISION: ClassVar[str] = (
            "a current decision must record the confirmed revision"
        )
        REVISION_NOT_PERMITTED: ClassVar[str] = (
            "only current and superseded decisions may record a revision"
        )

    _REVISION_BEARING_OUTCOMES: ClassVar[frozenset[RevalidationOutcome]] = frozenset(
        {RevalidationOutcome.CURRENT, RevalidationOutcome.SUPERSEDED}
    )

    schema_version: Literal[1] = 1
    feature: AgentQualityFeature
    outcome: RevalidationOutcome
    reason: RevalidationReason
    ref_binding_digest: Sha256Hex
    current_revision: BoundRevision | None = None

    @model_validator(mode="after")
    def _reason_belongs_to_outcome(self) -> "RevalidationDecision":
        if self.reason.outcome is not self.outcome:
            raise ValueError(self.Messages.REASON_OUTCOME_MISMATCH)
        return self

    @model_validator(mode="after")
    def _revision_visibility_is_closed(self) -> "RevalidationDecision":
        if self.outcome not in self._REVISION_BEARING_OUTCOMES:
            if self.current_revision is not None:
                raise ValueError(self.Messages.REVISION_NOT_PERMITTED)
            return self
        if (
            self.outcome is RevalidationOutcome.CURRENT
            and self.current_revision is None
        ):
            raise ValueError(self.Messages.CURRENT_REQUIRES_REVISION)
        return self

    @classmethod
    def for_reason(
        cls,
        *,
        feature: AgentQualityFeature,
        reason: RevalidationReason,
        ref_binding_digest: str,
        current_revision: BoundRevision | None = None,
    ) -> "RevalidationDecision":
        """Build a decision whose outcome is derived from its reason code."""

        return cls(
            feature=feature,
            outcome=reason.outcome,
            reason=reason,
            ref_binding_digest=ref_binding_digest,
            current_revision=current_revision,
        )

    @property
    def is_current(self) -> bool:
        """Return whether the reference may be used for the guarded operation."""

        return self.outcome.admits_use

    def require_current(self) -> BoundRevision:
        """Return the confirmed revision or raise the typed stale-use error."""

        if not self.is_current or self.current_revision is None:
            raise RevisionBoundRefNotCurrent(outcome=self.outcome, reason=self.reason)
        return self.current_revision

    def __bool__(self) -> bool:
        raise RevalidationBooleanCoercion


class RevisionAuthorityState(StrEnum):
    """Closed state a domain authority may report for one bound scope."""

    ACTIVE = "active"
    REVOKED = "revoked"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class RevisionAuthorityResult(RuntimeContract):
    """What a domain authority currently knows about one bound scope."""

    class Messages:
        """Validation messages owned by this contract."""

        ACTIVE_REQUIRES_REVISION: ClassVar[str] = (
            "an active authority result must carry the current revision"
        )
        REVISION_NOT_PERMITTED: ClassVar[str] = (
            "only an active authority result may carry a revision"
        )

    state: RevisionAuthorityState
    current_revision: BoundRevision | None = None

    @model_validator(mode="after")
    def _revision_matches_state(self) -> "RevisionAuthorityResult":
        if self.state is RevisionAuthorityState.ACTIVE:
            if self.current_revision is None:
                raise ValueError(self.Messages.ACTIVE_REQUIRES_REVISION)
            return self
        if self.current_revision is not None:
            raise ValueError(self.Messages.REVISION_NOT_PERMITTED)
        return self


@runtime_checkable
class RevisionAuthorityPort(Protocol):
    """The per-domain resolver F3/F5/F8/F9/F11 each supply.

    Implementations answer one question only -- what is authoritative *now* for
    this bound scope.  They never inspect the minted revision, never compare
    revisions themselves, and never widen a scope.

    ``resolution_handle`` is the adopter's own opaque value, forwarded verbatim
    from its own call site (see :data:`RevisionResolutionHandle`).  It exists
    because ``scope.subject_fingerprint`` is one-way: an authority that must ask
    a backend keyed by the original identity cannot recover it from the scope,
    and would otherwise need a scope-keyed side registry populated at mint time.
    It is a resolution *key*, never an answer -- an authority that reads
    freshness out of a caller-supplied handle it did not derive is validating
    the caller against itself.
    """

    async def current_revision(
        self,
        *,
        feature: AgentQualityFeature,
        scope: RevisionBoundScope,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> RevisionAuthorityResult: ...


@runtime_checkable
class RevisionRevalidatorPort(Protocol):
    """The single call-time revalidation protocol every adopter binds to."""

    async def revalidate_at_use(
        self,
        ref: RevisionBoundRef,
        runtime_context: RevisionUseContext,
        policy: RevalidationPolicy,
        *,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> RevalidationDecision: ...


class RevisionBindingRevalidator:
    """The one shared implementation of revision-bound revalidation.

    Checks run narrowest-first and cheapest-first: binding integrity, then the
    feature the reference was minted for, then the scope dimensions the call
    site demands, then the scope values against verified runtime facts, and only
    then the domain authority.  Every failure produces a closed outcome; no path
    returns ``current`` without an authority-confirmed equality match.
    """

    def __init__(self, authority: RevisionAuthorityPort) -> None:
        self._authority = authority

    async def revalidate_at_use(
        self,
        ref: RevisionBoundRef,
        runtime_context: RevisionUseContext,
        policy: RevalidationPolicy,
        *,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> RevalidationDecision:
        """Re-resolve ``ref`` against current authority and narrow accordingly.

        ``resolution_handle`` is forwarded to the domain authority untouched and
        is never retained: it is a parameter of this call and not of the
        revalidator, so it cannot outlive the resolution it was supplied for.
        No structural refusal consults it, so a handle can neither rescue a
        reference the checks above reject nor cause an authority call the
        checks above prevent.
        """

        presented_digest = ref.computed_binding_digest
        refusal = self._refusal_reason(ref, runtime_context, policy)
        if refusal is not None:
            return RevalidationDecision.for_reason(
                feature=policy.feature,
                reason=refusal,
                ref_binding_digest=presented_digest,
            )
        result = await self._resolve(ref, resolution_handle)
        if isinstance(result, RevalidationReason):
            return RevalidationDecision.for_reason(
                feature=policy.feature,
                reason=result,
                ref_binding_digest=presented_digest,
            )
        state_reason = self._state_reason(result.state)
        if state_reason is not None:
            return RevalidationDecision.for_reason(
                feature=policy.feature,
                reason=state_reason,
                ref_binding_digest=presented_digest,
            )
        matches = result.current_revision == ref.revision
        return RevalidationDecision.for_reason(
            feature=policy.feature,
            reason=(
                RevalidationReason.REVISION_MATCHES
                if matches
                else RevalidationReason.REVISION_CHANGED
            ),
            ref_binding_digest=presented_digest,
            current_revision=result.current_revision,
        )

    def _refusal_reason(
        self,
        ref: RevisionBoundRef,
        runtime_context: RevisionUseContext,
        policy: RevalidationPolicy,
    ) -> RevalidationReason | None:
        """Return the first structural refusal, before any authority call."""

        if not ref.binding_is_intact:
            return RevalidationReason.BINDING_DIGEST_MISMATCH
        if ref.feature is not policy.feature:
            return RevalidationReason.FEATURE_MISMATCH
        if not ref.scope.covers(policy.required_dimensions):
            return RevalidationReason.SCOPE_DIMENSION_MISSING
        if not runtime_context.covers(policy.required_dimensions):
            # The required dimensions bind both sides.  A call site that asks to
            # be fenced by a dimension and then presents no verified fact for it
            # has nothing to compare against, so the optional context fields
            # relax what is representable without relaxing what is checked.
            return RevalidationReason.SCOPE_DIMENSION_MISSING
        if ref.scope.subject_fingerprint != runtime_context.subject_fingerprint:
            return RevalidationReason.SUBJECT_MISMATCH
        if ref.scope.run_id is not None and ref.scope.run_id != runtime_context.run_id:
            return RevalidationReason.RUN_MISMATCH
        if (
            ref.scope.catalog_generation is not None
            and ref.scope.catalog_generation != runtime_context.catalog_generation
        ):
            return RevalidationReason.CATALOG_GENERATION_MISMATCH
        return None

    async def _resolve(
        self,
        ref: RevisionBoundRef,
        resolution_handle: RevisionResolutionHandle | None,
    ) -> RevisionAuthorityResult | RevalidationReason:
        """Consult the domain authority, converting every failure to a reason."""

        try:
            result = await self._authority.current_revision(
                feature=ref.feature,
                scope=ref.scope,
                resolution_handle=resolution_handle,
            )
        except Exception:
            # The resolver is domain-supplied and may wrap network or store
            # failures. Internal detail never reaches the caller, the model, or
            # an event: an unusable authority is simply unavailable. An
            # authority whose signature predates the handle raises TypeError
            # here and lands on the same fail-closed path.
            return RevalidationReason.AUTHORITY_ERROR
        if not isinstance(result, RevisionAuthorityResult):
            return RevalidationReason.AUTHORITY_CONTRACT_VIOLATION
        return result

    def _state_reason(
        self,
        state: RevisionAuthorityState,
    ) -> RevalidationReason | None:
        """Map every non-active authority state to its closed reason code."""

        if state is RevisionAuthorityState.UNAVAILABLE:
            return RevalidationReason.AUTHORITY_UNAVAILABLE
        if state is RevisionAuthorityState.UNKNOWN:
            return RevalidationReason.UNKNOWN_REFERENCE
        if state is RevisionAuthorityState.REVOKED:
            return RevalidationReason.AUTHORITY_REVOKED
        return None


__all__ = [
    "BoundRevision",
    "ControlToken",
    "OpaqueRefValue",
    "RevalidationBooleanCoercion",
    "RevalidationDecision",
    "RevalidationOutcome",
    "RevalidationPolicy",
    "RevalidationReason",
    "RevalidationReasonOutcomes",
    "RevisionAuthorityPort",
    "RevisionAuthorityResult",
    "RevisionAuthorityState",
    "RevisionBinding",
    "RevisionBindingError",
    "RevisionBindingRevalidator",
    "RevisionBoundRef",
    "RevisionBoundRefNotCurrent",
    "RevisionBoundScope",
    "RevisionOrderingNotSupported",
    "RevisionResolutionHandle",
    "RevisionRevalidatorPort",
    "RevisionScopeDimension",
    "RevisionUseContext",
    "Sha256Hex",
]
