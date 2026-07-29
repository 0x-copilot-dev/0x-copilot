"""F3's adoption of the one shared revision-binding primitive.

F3 capability refs obey the same rule as F5 evidence refs, F8 descriptor
revisions, F9 child grants, and F11 target manifests: a reference captured at
plan time must be re-resolved and reauthorized at use time, and a revision
mismatch fails closed.  Step RB published that rule once.  This module is F3's
*instantiation* of it — a projection and a small authority adapter — and
deliberately contains no staleness semantics of its own.  Every ordering,
scope, tamper, revocation, and unavailability decision belongs to
:class:`~agent_runtime.control_plane.revision_binding.RevisionBindingRevalidator`.

The mapping from the F3 binding onto the primitive is exact:

======================================  =====================================
F3 ``CapabilityRefBinding`` field       Step RB ``RevisionBoundRef`` element
======================================  =====================================
``capability_ref``                      opaque ref
``issued_generation.subject_fingerprint``  bound scope subject
run id of the projecting catalog        bound scope run
``issued_generation.generation_ref``    bound scope catalog generation
``issued_generation.generation_digest`` ``BoundRevision``
``binding_digest``                      binding digest (recomputed by RB)
======================================  =====================================

The catalog a run holds is a *snapshot*.  The authority is what the catalog
would be rebuilt as right now.  Projecting a snapshot-minted ref and asking the
shared revalidator whether the authority still reports the same generation is
therefore the whole staleness question, asked in one place.

RB.3 threads an optional resolution handle from the call site through to the
generation source.  ``subject_fingerprint`` is one-way by design, so a source
that must rebuild — or ask a store to rebuild — the catalog for the *original*
identity cannot recover it from the bound scope, and would otherwise need a
scope-keyed registry populated when the catalog was minted.  The handle is
supplied once, where the verified identity already lives, and forwarded
untouched.  It is deliberately not the run's own held generation: an authority
that answered from the snapshot under test would be validating the snapshot
against itself.
"""

from __future__ import annotations

from typing import ClassVar, Protocol, Self, runtime_checkable

from pydantic import model_validator

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityCatalogGeneration,
    CapabilityCatalogIdentityError,
    CapabilityRefBinding,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    BoundRevision,
    RevalidationDecision,
    RevalidationPolicy,
    RevisionAuthorityResult,
    RevisionAuthorityState,
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionResolutionHandle,
    RevisionRevalidatorPort,
    RevisionScopeDimension,
    RevisionUseContext,
)
from agent_runtime.execution.contracts import RuntimeContract


class CapabilityRefBindingError(CapabilityCatalogIdentityError):
    """A capability ref could not be projected onto the shared primitive."""

    class Messages:
        """Safe public messages for projection failures."""

        UNPROJECTABLE_SCOPE = (
            "this capability ref cannot be bound to a revalidatable scope"
        )


class LiveCapabilityCatalogGeneration(RuntimeContract):
    """What the F3 catalog source currently knows about one bound scope.

    The source may not hand back an arbitrary opaque revision string: an active
    answer must carry a real :class:`CapabilityCatalogGeneration`, whose digest
    authenticates the exact keyed inputs it claims.  That is what keeps a
    compromised or careless source from asserting freshness it cannot prove.
    """

    class Messages:
        """Safe public messages for live-generation validation."""

        ACTIVE_REQUIRES_GENERATION: ClassVar[str] = (
            "an active catalog generation answer must carry the generation"
        )
        GENERATION_NOT_PERMITTED: ClassVar[str] = (
            "only an active catalog generation answer may carry a generation"
        )

    state: RevisionAuthorityState
    generation: CapabilityCatalogGeneration | None = None

    @model_validator(mode="after")
    def _generation_matches_state(self) -> Self:
        if self.state is RevisionAuthorityState.ACTIVE:
            if self.generation is None:
                raise ValueError(self.Messages.ACTIVE_REQUIRES_GENERATION)
            return self
        if self.generation is not None:
            raise ValueError(self.Messages.GENERATION_NOT_PERMITTED)
        return self

    @classmethod
    def active(cls, generation: CapabilityCatalogGeneration) -> Self:
        """Return the live generation currently authoritative for a scope."""

        return cls(state=RevisionAuthorityState.ACTIVE, generation=generation)

    @classmethod
    def for_state(cls, state: RevisionAuthorityState) -> Self:
        """Return a non-active answer with no generation attached."""

        return cls(state=state)


@runtime_checkable
class CapabilityCatalogGenerationPort(Protocol):
    """The F3-owned source of the live catalog generation for one bound scope.

    Implementations answer one question only — what catalog generation is
    authoritative *now* for this scope.  They never inspect the generation a
    reference was minted against, never compare generations, and never widen a
    scope; comparison is the shared revalidator's job.

    ``resolution_handle`` is the F3-owned value supplied at the call site (see
    :class:`CapabilityRefRevalidation`).  A source that keys its own store by
    the bound scope may ignore it; one that must ask a backend keyed by the
    original identity uses it instead of keeping a scope-keyed registry.
    """

    async def live_generation(
        self,
        *,
        scope: RevisionBoundScope,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> LiveCapabilityCatalogGeneration: ...


class CapabilityCatalogRevisionAuthority:
    """The small ``RevisionAuthorityPort`` F3 supplies to the shared primitive.

    It translates an F3 answer into the primitive's closed authority vocabulary
    and does nothing else.  Two refusals are deliberate: a reference minted for
    another feature is answered as unknown rather than resolved, and a
    generation whose digest no longer authenticates its own inputs is answered
    as unavailable rather than trusted.  Both fail closed.
    """

    FEATURE: ClassVar[AgentQualityFeature] = AgentQualityFeature.F3_CAPABILITY_DISCOVERY

    def __init__(self, source: CapabilityCatalogGenerationPort) -> None:
        self._source = source

    async def current_revision(
        self,
        *,
        feature: AgentQualityFeature,
        scope: RevisionBoundScope,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> RevisionAuthorityResult:
        """Return the live catalog generation for ``scope`` as a revision."""

        if feature is not self.FEATURE:
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        live = await self._source.live_generation(
            scope=scope,
            resolution_handle=resolution_handle,
        )
        if not isinstance(live, LiveCapabilityCatalogGeneration):
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNAVAILABLE)
        if live.state is not RevisionAuthorityState.ACTIVE or live.generation is None:
            return RevisionAuthorityResult(state=live.state)
        try:
            live.generation.verify()
        except CapabilityCatalogIdentityError:
            # A generation that no longer authenticates its own keyed inputs is
            # not evidence of anything. Refusing to read it as freshness keeps
            # a tampered answer from ever producing ``current``.
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNAVAILABLE)
        return RevisionAuthorityResult(
            state=RevisionAuthorityState.ACTIVE,
            current_revision=CapabilityRefRevisionBinding.revision_for(live.generation),
        )


class CapabilityRefRevisionBinding:
    """Project one F3 capability-ref binding onto the shared RB primitive.

    Projection is pure and reproducible: identical bindings always project to
    an identical :class:`RevisionBoundRef`.  Nothing here decides currency.
    """

    FEATURE: ClassVar[AgentQualityFeature] = AgentQualityFeature.F3_CAPABILITY_DISCOVERY
    REQUIRED_DIMENSIONS: ClassVar[frozenset[RevisionScopeDimension]] = frozenset(
        {
            RevisionScopeDimension.SUBJECT,
            RevisionScopeDimension.RUN,
            RevisionScopeDimension.CATALOG_GENERATION,
        }
    )

    @classmethod
    def revision_for(cls, generation: CapabilityCatalogGeneration) -> BoundRevision:
        """Return the opaque revision a generation is compared by."""

        return BoundRevision(value=generation.generation_digest)

    @classmethod
    def scope_for(
        cls,
        generation: CapabilityCatalogGeneration,
        *,
        run_id: str,
    ) -> RevisionBoundScope:
        """Return the run/subject/catalog-generation scope a ref is bound to."""

        try:
            return RevisionBoundScope(
                subject_fingerprint=generation.subject_fingerprint,
                run_id=run_id,
                catalog_generation=generation.generation_ref,
            )
        except ValueError as exc:
            raise CapabilityRefBindingError(
                CapabilityRefBindingError.Messages.UNPROJECTABLE_SCOPE
            ) from exc

    @classmethod
    def bound_ref(
        cls,
        binding: CapabilityRefBinding,
        *,
        run_id: str,
    ) -> RevisionBoundRef:
        """Project an F3 binding into a revalidatable revision-bound ref."""

        binding.verify()
        generation = binding.issued_generation
        return RevisionBoundRef.mint(
            feature=cls.FEATURE,
            opaque_ref=binding.capability_ref,
            scope=cls.scope_for(generation, run_id=run_id),
            revision=cls.revision_for(generation),
        )

    @classmethod
    def use_context(
        cls,
        *,
        subject_fingerprint: str,
        run_id: str,
        generation: CapabilityCatalogGeneration,
    ) -> RevisionUseContext:
        """Return the verified at-use facts the shared revalidator compares to.

        ``subject_fingerprint`` must come from the verified runtime context and
        ``generation`` from the catalog the runtime currently holds — never from
        the reference being checked, which is exactly the value under suspicion.
        """

        try:
            return RevisionUseContext(
                subject_fingerprint=subject_fingerprint,
                run_id=run_id,
                catalog_generation=generation.generation_ref,
            )
        except ValueError as exc:
            raise CapabilityRefBindingError(
                CapabilityRefBindingError.Messages.UNPROJECTABLE_SCOPE
            ) from exc

    @classmethod
    def policy(cls) -> RevalidationPolicy:
        """Return the F3 call-site policy: subject, run, and generation bound."""

        return RevalidationPolicy(
            feature=cls.FEATURE,
            required_dimensions=cls.REQUIRED_DIMENSIONS,
        )


class CapabilityRefRevalidation:
    """Everything the bridge needs to re-resolve a capability ref at use time.

    This is a two-line composition over the shared primitive on purpose.  When
    it is absent from a bridge tool, the tool refuses rather than dispatching:
    an unrevalidatable reference is never usable.

    ``resolution_handle`` is bound here, next to the verified subject
    fingerprint and for the same reason: this object is constructed once per
    verified runtime context, which is the only place that legitimately holds
    the identity a fingerprint hides.  Binding it here rather than accepting it
    per call keeps it out of the bridge's untrusted request path — a model
    cannot influence which identity the authority resolves for.
    """

    def __init__(
        self,
        *,
        revalidator: RevisionRevalidatorPort,
        subject_fingerprint: str,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> None:
        self._revalidator = revalidator
        self._subject_fingerprint = subject_fingerprint
        self._resolution_handle = resolution_handle

    async def decide(
        self,
        *,
        binding: CapabilityRefBinding,
        run_id: str,
        live_generation: CapabilityCatalogGeneration,
    ) -> RevalidationDecision:
        """Return the shared primitive's closed decision for one capability ref."""

        return await self._revalidator.revalidate_at_use(
            CapabilityRefRevisionBinding.bound_ref(binding, run_id=run_id),
            CapabilityRefRevisionBinding.use_context(
                subject_fingerprint=self._subject_fingerprint,
                run_id=run_id,
                generation=live_generation,
            ),
            CapabilityRefRevisionBinding.policy(),
            resolution_handle=self._resolution_handle,
        )


__all__ = (
    "CapabilityCatalogGenerationPort",
    "CapabilityCatalogRevisionAuthority",
    "CapabilityRefBindingError",
    "CapabilityRefRevalidation",
    "CapabilityRefRevisionBinding",
    "LiveCapabilityCatalogGeneration",
)
