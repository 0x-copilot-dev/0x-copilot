"""F5 evidence references bind through the shared Step RB primitive.

The 15 behaviors asserted here are inherited from the published conformance
suite, not restated: F5 adds an instantiation, never a second staleness
implementation.  The harness drives the production
:class:`EvidenceRevisionAuthority` through the production
:class:`EvidenceResolverRegistry`'s revalidator, and mints through the
production :meth:`EvidenceGrant.issue`, so a regression in identity derivation,
in the authority projection, or in the lifecycle-to-authority table fails these
cases rather than being masked by a test double.

The only double is the source domain itself -- a resolver stands in for the
library, artifact store, or conversation history F5 will register in
production.  That is the correct seam: the suite is grading F5's adoption of the
primitive, not any one source domain's storage.
"""

from __future__ import annotations

from agent_runtime.context.evidence_registry import (
    EvidenceGrant,
    EvidenceKind,
    EvidenceLifecycle,
    EvidenceMaterial,
    EvidenceMaterialState,
    EvidenceRefIdentity,
    EvidenceResolutionHandle,
    EvidenceResolverRegistry,
    EvidenceSelector,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionRevalidatorPort,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from tests.unit.agent_runtime.control_plane.revision_binding_conformance import (
    RevisionBindingConformanceHarness,
    RevisionBindingConformanceSuite,
)


class ConformanceEvidenceResolver:
    """One source domain, as small as the port allows.

    It answers the two questions a resolver owes -- what is current now, and
    give me at most N characters -- and holds exactly the lifecycle the harness
    puts in it.  Nothing here compares revisions or decides scope: those are
    the shared revalidator's, which is what these cases grade.
    """

    def __init__(self) -> None:
        self._lifecycles: dict[str, EvidenceLifecycle] = {}
        self._unavailable = False

    @property
    def kind(self) -> EvidenceKind:
        return EvidenceKind.SOURCE

    def set_lifecycle(self, locator: str, lifecycle: EvidenceLifecycle) -> None:
        self._lifecycles[locator] = lifecycle

    def set_unavailable(self, *, unavailable: bool) -> None:
        self._unavailable = unavailable

    async def current_lifecycle(
        self,
        *,
        scope: RevisionBoundScope,
        locator: str,
    ) -> EvidenceLifecycle:
        if self._unavailable:
            return EvidenceLifecycle.for_state(EvidenceMaterialState.UNAVAILABLE)
        return self._lifecycles.get(
            locator,
            EvidenceLifecycle.for_state(EvidenceMaterialState.UNKNOWN),
        )

    async def read_material(
        self,
        *,
        scope: RevisionBoundScope,
        locator: str,
        selector: EvidenceSelector | None,
        max_chars: int,
    ) -> EvidenceMaterial:
        lifecycle = await self.current_lifecycle(scope=scope, locator=locator)
        if lifecycle.revision is None:
            return EvidenceMaterial.for_state(lifecycle.state)
        return EvidenceMaterial.available(
            revision=lifecycle.revision,
            content=f"evidence for {locator}"[:max_chars],
        )


class EvidenceRevisionBindingHarness:
    """Mint and drive F5 evidence references for the conformance suite.

    The locator is derived from the bound scope so that ``mint``, ``supersede``
    and ``revoke`` for one scope all address the same source item, which is how
    a real domain behaves: one evidence item, one lifecycle, many revisions.
    """

    KIND = EvidenceKind.SOURCE
    LOCATOR_PREFIX = "conformance-evidence-"

    def __init__(self) -> None:
        self._resolver = ConformanceEvidenceResolver()
        self._registry = EvidenceResolverRegistry([self._resolver])
        self._grants: dict[str, EvidenceGrant] = {}
        self._issued = 0

    @property
    def feature(self) -> AgentQualityFeature:
        return EvidenceRefIdentity.FEATURE

    @property
    def revalidator(self) -> RevisionRevalidatorPort:
        return self._registry.revalidator

    async def mint(self, scope: RevisionBoundScope) -> RevisionBoundRef:
        locator = self._locator_for(scope)
        revision = self._next_revision()
        self._resolver.set_lifecycle(
            locator,
            EvidenceLifecycle.available(revision=revision),
        )
        grant = EvidenceGrant.issue(
            scope=scope,
            kind=self.KIND,
            locator=locator,
            revision=revision,
        )
        self._grants[grant.token] = grant
        return grant.ref

    async def supersede(self, scope: RevisionBoundScope) -> None:
        self._resolver.set_lifecycle(
            self._locator_for(scope),
            EvidenceLifecycle.available(revision=self._next_revision()),
        )

    async def revoke(self, scope: RevisionBoundScope) -> None:
        self._resolver.set_lifecycle(
            self._locator_for(scope),
            EvidenceLifecycle.for_state(EvidenceMaterialState.ACCESS_REVOKED),
        )

    async def set_authority_unavailable(self, *, unavailable: bool) -> None:
        # An unreachable source is a lifecycle answer, not a flag on the
        # authority: F5's authority holds no state at all, so there is nothing
        # a test could set here that production would inherit.
        self._resolver.set_unavailable(unavailable=unavailable)

    def resolution_handle(
        self,
        ref: RevisionBoundRef,
    ) -> EvidenceResolutionHandle | None:
        grant = self._grants.get(ref.opaque_ref)
        if grant is None:
            return None
        return grant.resolution_handle(self._resolver)

    def _locator_for(self, scope: RevisionBoundScope) -> str:
        return f"{self.LOCATOR_PREFIX}{canonical_json_sha256(scope.model_dump())}"

    def _next_revision(self) -> str:
        self._issued += 1
        return f"evidence-revision-{self._issued}"


class EvidenceHarnessMixin:
    """Build one fresh F5 harness per inherited conformance case."""

    async def build_harness(self) -> RevisionBindingConformanceHarness:
        return EvidenceRevisionBindingHarness()


class TestEvidenceRevisionBindingConformance(
    EvidenceHarnessMixin,
    RevisionBindingConformanceSuite,
):
    """F5 evidence refs satisfy every published RB adopter behavior."""
