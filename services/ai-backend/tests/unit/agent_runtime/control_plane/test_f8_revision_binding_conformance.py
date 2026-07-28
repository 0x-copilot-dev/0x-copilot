"""F8 descriptor revisions bind through the shared Step RB primitive.

The 15 behaviors asserted here are inherited from the published conformance
suite, not restated: RB.2 adds an instantiation, never a second staleness
implementation.  The harness drives the production
:class:`McpDescriptorRevisionAuthority` and the production
:class:`McpDescriptorRevisionBinder`, so a regression in either fails these
cases rather than being masked by a test double.
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.descriptor_revision_binding import (
    McpDescriptorAuthorityResolution,
    McpDescriptorBindingIdentity,
    McpDescriptorRevisionBinder,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    RevisionAuthorityState,
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionRevalidatorPort,
)
from tests.unit.agent_runtime.control_plane.revision_binding_conformance import (
    RevisionBindingConformanceHarness,
    RevisionBindingConformanceSuite,
)


class McpDescriptorRevisionBindingHarness:
    """Mint and drive F8 descriptor references for the conformance suite.

    The backend answer travels as the RB.3 resolution handle, exactly as
    :meth:`McpDescriptorRevisionBinder.revalidate` passes it in production.  The
    per-subject map below belongs to the harness, not to F8: it stands in for
    the backend revision path the real caller has already consulted by the time
    it asks whether a bound view is still usable.
    """

    def __init__(self) -> None:
        self._binder = McpDescriptorRevisionBinder()
        self._issued = 0
        self._backend: dict[str, McpDescriptorAuthorityResolution] = {}

    @property
    def feature(self) -> AgentQualityFeature:
        return McpDescriptorBindingIdentity.FEATURE

    @property
    def revalidator(self) -> RevisionRevalidatorPort:
        return self._binder.revalidator

    async def mint(self, scope: RevisionBoundScope) -> RevisionBoundRef:
        revision = self._next_revision()
        self._backend[scope.subject_fingerprint] = (
            McpDescriptorAuthorityResolution.active(revision)
        )
        return McpDescriptorBindingIdentity.mint(scope=scope, revision=revision)

    async def supersede(self, scope: RevisionBoundScope) -> None:
        self._backend[scope.subject_fingerprint] = (
            McpDescriptorAuthorityResolution.active(self._next_revision())
        )

    async def revoke(self, scope: RevisionBoundScope) -> None:
        self._backend[scope.subject_fingerprint] = (
            McpDescriptorAuthorityResolution.for_state(RevisionAuthorityState.REVOKED)
        )

    async def set_authority_unavailable(self, *, unavailable: bool) -> None:
        self._binder.authority.set_unavailable(unavailable=unavailable)

    def resolution_handle(
        self,
        ref: RevisionBoundRef,
    ) -> McpDescriptorAuthorityResolution | None:
        return self._backend.get(ref.scope.subject_fingerprint)

    def _next_revision(self) -> str:
        self._issued += 1
        return f"mcp-descriptor-revision-{self._issued}"


class McpDescriptorHarnessMixin:
    """Build one fresh F8 harness per inherited conformance case."""

    async def build_harness(self) -> RevisionBindingConformanceHarness:
        return McpDescriptorRevisionBindingHarness()


class TestMcpDescriptorRevisionBindingConformance(
    McpDescriptorHarnessMixin,
    RevisionBindingConformanceSuite,
):
    """F8 descriptor revisions satisfy every published RB adopter behavior."""
