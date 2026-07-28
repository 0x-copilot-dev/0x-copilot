"""F8 descriptor revisions bound to the shared Step RB revalidation primitive.

Step 7 shipped a private staleness check inside
:class:`~agent_runtime.capabilities.mcp.freshness.RevisionAwareMcpDiscoveryCache`.
Step RB then replaced five such implementations with one primitive, and F8 is
its first adopter.  This module supplies only the two things RB.1 asks an
adopter for:

* a stable, collision-free ``subject_fingerprint`` for an MCP descriptor
  subject; and
* a :class:`~agent_runtime.control_plane.revision_binding.RevisionAuthorityPort`
  answering the single question *what is authoritative now for this scope*.

Every comparison -- binding integrity, feature, scope dimensions, subject,
catalog generation, and revision equality -- belongs to
:class:`~agent_runtime.control_plane.revision_binding.RevisionBindingRevalidator`
and is deliberately not reimplemented here.

No authority is introduced.  :meth:`McpDescriptorRevisionAuthority.publish`
only ever records a revision that the trusted backend revision path (exact
check or feed notice) already resolved for a verified subject, so this class is
a projection of the backend authority rather than a second one.  It also
narrows: §9.2 keeps ai-backend on *opaque* revisions, so both the subject and
the revision cross this boundary as digests and no org, user, server, or
provider-shaped revision string is ever placed in a control contract.
"""

from __future__ import annotations

from typing import ClassVar

from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCacheKey
from agent_runtime.control_plane.feature_modes import AgentQualityFeature
from agent_runtime.control_plane.revision_binding import (
    BoundRevision,
    RevalidationDecision,
    RevalidationPolicy,
    RevisionAuthorityResult,
    RevisionAuthorityState,
    RevisionBindingRevalidator,
    RevisionBoundRef,
    RevisionBoundScope,
    RevisionScopeDimension,
    RevisionUseContext,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class McpDescriptorBindingIdentity:
    """Derives every opaque control identity F8 binds a descriptor view to.

    Both derivations are canonical-JSON digests over a fixed, labelled key set.
    That encoding is injective: the key set is closed, JSON escapes quotes
    inside string values, and no delimiter can therefore be confused for
    content.  ``("acme:sales", "u1")`` and ``("acme", "sales:u1")`` -- the
    classic separator-injection collision a naive ``f"{org}:{user}"`` admits --
    produce different documents here, so two distinct subjects can only share a
    fingerprint if SHA-256 itself collides.

    The ``KIND`` labels add domain separation, so an F8 subject fingerprint can
    never equal an F3/F5/F9/F11 fingerprint derived over a same-shaped tuple.
    """

    SUBJECT_KIND: ClassVar[str] = "mcp.descriptor.subject"
    REVISION_KIND: ClassVar[str] = "mcp.descriptor.revision"
    SCHEMA_VERSION: ClassVar[int] = 1
    REF_PREFIX: ClassVar[str] = "mcp-descriptor-"
    FEATURE: ClassVar[AgentQualityFeature] = AgentQualityFeature.F8_MCP_CONTROL_PLANE
    # The descriptor cache is process-wide and subject-scoped: it is shared by
    # every run in the worker, so no run may narrow it.  F8 therefore never
    # binds ``RevisionBoundScope.run_id``, which makes the run dimension inert
    # and this mandatory context value unable to admit or refuse anything.
    CACHE_RUN_SENTINEL: ClassVar[str] = "mcp-descriptor-cache"

    @classmethod
    def subject_fingerprint(cls, key: McpDiscoveryCacheKey) -> str:
        """Return the stable SHA-256 fingerprint of one descriptor subject.

        The subject is the discovery cache's own isolation key.  Binding to it
        rather than to ``(org_id, user_id)`` keeps per-server isolation, so a
        reference minted for one server can never be replayed on another.
        """

        return canonical_json_sha256(
            {
                "kind": cls.SUBJECT_KIND,
                "schema_version": cls.SCHEMA_VERSION,
                "org_id": key.org_id,
                "user_id": key.user_id,
                "server_name": key.server_name,
            }
        )

    @classmethod
    def bound_revision(cls, value: str) -> BoundRevision:
        """Return the opaque bound form of one control-plane revision.

        Backend revisions are provider-shaped (ETags, digests, sequence ids)
        and only bounded by :class:`McpDescriptorRevision`, so a value with
        inner whitespace is representable there but not as an
        ``OpaqueRefValue``.  Digesting is total, keeps the boundary body-free,
        and -- being injective -- preserves equality exactly, which is the only
        relation the primitive is allowed to use.
        """

        return BoundRevision(
            value=canonical_json_sha256(
                {
                    "kind": cls.REVISION_KIND,
                    "schema_version": cls.SCHEMA_VERSION,
                    "revision": value,
                }
            )
        )

    @classmethod
    def scope(
        cls,
        subject_fingerprint: str,
        *,
        catalog_generation: int,
    ) -> RevisionBoundScope:
        """Bind a descriptor view to one subject and one generation barrier."""

        return RevisionBoundScope(
            subject_fingerprint=subject_fingerprint,
            catalog_generation=str(catalog_generation),
        )

    @classmethod
    def use_context(
        cls,
        subject_fingerprint: str,
        *,
        catalog_generation: int,
    ) -> RevisionUseContext:
        """Project the verified at-use facts the cache already holds."""

        return RevisionUseContext(
            subject_fingerprint=subject_fingerprint,
            run_id=cls.CACHE_RUN_SENTINEL,
            catalog_generation=str(catalog_generation),
        )

    @classmethod
    def mint(cls, *, scope: RevisionBoundScope, revision: str) -> RevisionBoundRef:
        """Bind ``revision`` to ``scope`` reproducibly for the F8 feature."""

        return RevisionBoundRef.mint(
            feature=cls.FEATURE,
            opaque_ref=f"{cls.REF_PREFIX}{scope.subject_fingerprint}",
            scope=scope,
            revision=cls.bound_revision(revision),
        )


class McpDescriptorRevisionAuthority:
    """What the MCP control plane says is current *now* for one subject.

    It answers, and never compares: revision equality, scope narrowing, and
    binding integrity all belong to the shared revalidator.

    State is process-local, matching the descriptor cache it projects.  Every
    mutation happens on the event loop from a caller that already holds the
    cache's state lock, and :meth:`current_revision` reaches no suspension
    point, so the read is atomic with respect to those mutations without taking
    a second lock (which would introduce a lock-ordering hazard).
    """

    def __init__(self) -> None:
        self._current: dict[str, BoundRevision] = {}
        self._revoked: set[str] = set()
        self._unavailable = False

    def publish(self, *, subject_fingerprint: str, revision: str) -> None:
        """Record the trusted current revision resolved for one subject."""

        self._current[subject_fingerprint] = (
            McpDescriptorBindingIdentity.bound_revision(revision)
        )
        self._revoked.discard(subject_fingerprint)

    def revoke(self, *, subject_fingerprint: str) -> None:
        """Record that this subject's descriptor authority no longer exists."""

        self._current.pop(subject_fingerprint, None)
        self._revoked.add(subject_fingerprint)

    def forget(self, *, subject_fingerprint: str) -> None:
        """Drop a projection whose backing generation state is being released.

        Forgetting yields ``unknown`` rather than ``active``: a reference is
        never admitted because the projection went missing.
        """

        self._current.pop(subject_fingerprint, None)
        self._revoked.discard(subject_fingerprint)

    def set_unavailable(self, *, unavailable: bool) -> None:
        """Simulate or record an unreachable authority without losing state."""

        self._unavailable = unavailable

    async def current_revision(
        self,
        *,
        feature: AgentQualityFeature,
        scope: RevisionBoundScope,
    ) -> RevisionAuthorityResult:
        """Return what is authoritative now for ``scope``, or why it is not."""

        if self._unavailable:
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNAVAILABLE)
        if feature is not McpDescriptorBindingIdentity.FEATURE:
            # Unreachable: the revalidator refuses a feature mismatch before it
            # reaches any authority.  Answering ``unknown`` keeps this adapter
            # fail-closed even if it is ever consulted directly.
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        fingerprint = scope.subject_fingerprint
        if fingerprint in self._revoked:
            return RevisionAuthorityResult(state=RevisionAuthorityState.REVOKED)
        revision = self._current.get(fingerprint)
        if revision is None:
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        return RevisionAuthorityResult(
            state=RevisionAuthorityState.ACTIVE,
            current_revision=revision,
        )


class McpDescriptorRevisionBinder:
    """The F8 call site of the shared revalidation primitive.

    One binder owns one authority projection and one revalidator.  The cache
    asks it a single question -- *may this bound descriptor view still be used
    at this generation* -- and receives the primitive's closed decision.
    """

    #: F8 always binds the generation barrier, so a reference that omits it is
    #: refused structurally rather than silently treated as unfenced.
    POLICY: ClassVar[RevalidationPolicy] = RevalidationPolicy(
        feature=McpDescriptorBindingIdentity.FEATURE,
        required_dimensions=frozenset(
            {
                RevisionScopeDimension.SUBJECT,
                RevisionScopeDimension.CATALOG_GENERATION,
            }
        ),
    )

    def __init__(self, authority: McpDescriptorRevisionAuthority | None = None) -> None:
        self._authority = authority or McpDescriptorRevisionAuthority()
        self._revalidator = RevisionBindingRevalidator(self._authority)

    @property
    def authority(self) -> McpDescriptorRevisionAuthority:
        """Return the projection this binder resolves against."""

        return self._authority

    @property
    def revalidator(self) -> RevisionBindingRevalidator:
        """Return the shared revalidator under this binding."""

        return self._revalidator

    def forget(self, key: McpDiscoveryCacheKey) -> None:
        """Release the projection for ``key`` alongside its generation state."""

        self._authority.forget(
            subject_fingerprint=McpDescriptorBindingIdentity.subject_fingerprint(key)
        )

    async def revalidate(
        self,
        *,
        key: McpDiscoveryCacheKey,
        bound_revision: str,
        bound_generation: int,
        trusted_revision: str,
        observed_generation: int,
    ) -> RevalidationDecision:
        """Revalidate one bound descriptor view against current authority.

        ``bound_revision``/``bound_generation`` describe the material as it was
        minted -- admitted into the cache, or captured before a cold load.
        ``trusted_revision`` is what the backend revision authority resolved
        for this subject, and ``observed_generation`` is the barrier value read
        at the moment of use.
        """

        fingerprint = McpDescriptorBindingIdentity.subject_fingerprint(key)
        self._authority.publish(
            subject_fingerprint=fingerprint,
            revision=trusted_revision,
        )
        ref = McpDescriptorBindingIdentity.mint(
            scope=McpDescriptorBindingIdentity.scope(
                fingerprint,
                catalog_generation=bound_generation,
            ),
            revision=bound_revision,
        )
        return await self._revalidator.revalidate_at_use(
            ref,
            McpDescriptorBindingIdentity.use_context(
                fingerprint,
                catalog_generation=observed_generation,
            ),
            self.POLICY,
        )


__all__ = [
    "McpDescriptorBindingIdentity",
    "McpDescriptorRevisionAuthority",
    "McpDescriptorRevisionBinder",
]
