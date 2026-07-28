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
    McpDescriptorBindingIdentity,
    McpDescriptorRevisionBinder,
)
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionRevalidatorPort,
)
from tests.unit.agent_runtime.control_plane.revision_binding_conformance import (
    RevisionBindingConformanceHarness,
    RevisionBindingConformanceSuite,
)


class McpDescriptorRevisionBindingHarness:
    """Mint and drive F8 descriptor references for the conformance suite."""

    def __init__(self) -> None:
        self._binder = McpDescriptorRevisionBinder()
        self._issued = 0

    @property
    def feature(self) -> AgentQualityFeature:
        return McpDescriptorBindingIdentity.FEATURE

    @property
    def revalidator(self) -> RevisionRevalidatorPort:
        return self._binder.revalidator

    async def mint(self, scope: RevisionBoundScope) -> RevisionBoundRef:
        revision = self._next_revision()
        self._binder.authority.publish(
            subject_fingerprint=scope.subject_fingerprint,
            revision=revision,
        )
        return McpDescriptorBindingIdentity.mint(scope=scope, revision=revision)

    async def supersede(self, scope: RevisionBoundScope) -> None:
        self._binder.authority.publish(
            subject_fingerprint=scope.subject_fingerprint,
            revision=self._next_revision(),
        )

    async def revoke(self, scope: RevisionBoundScope) -> None:
        self._binder.authority.revoke(
            subject_fingerprint=scope.subject_fingerprint,
        )

    async def set_authority_unavailable(self, *, unavailable: bool) -> None:
        self._binder.authority.set_unavailable(unavailable=unavailable)

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
