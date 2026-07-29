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

No authority is introduced.  The only revision this module ever reports is one
the trusted backend revision path (exact check or feed notice) already resolved
for a verified subject, so this class is a projection of the backend authority
rather than a second one.  It also narrows: §9.2 keeps ai-backend on *opaque*
revisions, so both the subject and the revision cross this boundary as digests
and no org, user, server, or provider-shaped revision string is ever placed in
a control contract.

RB.3 removed the side registry this adoption originally needed.  The backend's
answer arrives with the request that asks the question, so the authority is
handed it directly as the RB.3 resolution handle instead of writing it into a
fingerprint-keyed dict at the top of every call and reading it back one frame
later.  Nothing is cached, so nothing has to be released.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    RevisionResolutionHandle,
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
        """Project the verified at-use facts the cache already holds.

        The descriptor cache is process-wide and subject-scoped: it is shared by
        every run in the worker, so no run may narrow it and F8 binds no run on
        either side.  Since RB.3 the context can say so instead of asserting an
        invented run that no verified state backs.
        """

        return RevisionUseContext(
            subject_fingerprint=subject_fingerprint,
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


@dataclass(frozen=True, slots=True)
class McpDescriptorAuthorityResolution:
    """The RB.3 resolution handle F8 hands its own authority.

    The backend revision path resolves a subject's current revision *before*
    the cache asks whether a bound view is still usable -- the answer arrives on
    the request itself.  Carrying it here is what lets the authority resolve
    directly, and it is exactly the identity-recovery problem RB.3 names: the
    subject fingerprint is one-way, so an authority handed only a scope cannot
    re-ask the backend and has to keep a fingerprint-keyed dict instead.

    The handle is opaque to the shared primitive, which forwards it without
    interpreting it.  It never reaches a minted reference or a decision, so no
    org, user, server, or provider-shaped revision string is persisted or
    logged because of it; the state below is the same closed control vocabulary
    the port already speaks.
    """

    state: RevisionAuthorityState
    revision: BoundRevision | None = None

    @classmethod
    def active(cls, revision: str) -> "McpDescriptorAuthorityResolution":
        """Carry the trusted revision the backend already resolved."""

        return cls(
            state=RevisionAuthorityState.ACTIVE,
            revision=McpDescriptorBindingIdentity.bound_revision(revision),
        )

    @classmethod
    def for_state(
        cls,
        state: RevisionAuthorityState,
    ) -> "McpDescriptorAuthorityResolution":
        """Carry a non-active answer, which never admits a reference."""

        return cls(state=state)


class McpDescriptorRevisionAuthority:
    """What the MCP control plane says is current *now* for one subject.

    It answers, and never compares: revision equality, scope narrowing, and
    binding integrity all belong to the shared revalidator.

    It also keeps no per-subject state.  Before RB.3 the port could only be
    handed ``(feature, scope)``, so this class kept a fingerprint-keyed
    projection that every call wrote and then immediately read back, plus the
    ``forget`` path that dict's unbounded growth required.  The handle removes
    both: an answer the caller already holds is passed in, and a call that
    presents no answer resolves to ``unknown`` rather than to something stale.

    The one piece of state left is the reachability flag, which is a property of
    the authority itself rather than of any subject.  It is read on the event
    loop with no suspension point, so it needs no lock of its own.
    """

    def __init__(self) -> None:
        self._unavailable = False

    def set_unavailable(self, *, unavailable: bool) -> None:
        """Simulate or record an unreachable authority without losing state."""

        self._unavailable = unavailable

    async def current_revision(
        self,
        *,
        feature: AgentQualityFeature,
        scope: RevisionBoundScope,
        resolution_handle: RevisionResolutionHandle | None = None,
    ) -> RevisionAuthorityResult:
        """Return what is authoritative now for ``scope``, or why it is not."""

        if self._unavailable:
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNAVAILABLE)
        if feature is not McpDescriptorBindingIdentity.FEATURE:
            # Unreachable: the revalidator refuses a feature mismatch before it
            # reaches any authority.  Answering ``unknown`` keeps this adapter
            # fail-closed even if it is ever consulted directly.
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        if not isinstance(resolution_handle, McpDescriptorAuthorityResolution):
            # No handle, or one belonging to another domain: this authority has
            # nothing to resolve from and must not guess.
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        if resolution_handle.state is not RevisionAuthorityState.ACTIVE:
            return RevisionAuthorityResult(state=resolution_handle.state)
        if resolution_handle.revision is None:
            return RevisionAuthorityResult(state=RevisionAuthorityState.UNKNOWN)
        return RevisionAuthorityResult(
            state=RevisionAuthorityState.ACTIVE,
            current_revision=resolution_handle.revision,
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

        The trusted revision is handed to the authority as the RB.3 resolution
        handle.  It is the same value the authority used to answer with, from
        the same source, so the verdict is unchanged -- it simply travels as an
        argument rather than through a dict this binder had to keep alive.
        """

        fingerprint = McpDescriptorBindingIdentity.subject_fingerprint(key)
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
            resolution_handle=McpDescriptorAuthorityResolution.active(trusted_revision),
        )


__all__ = [
    "McpDescriptorAuthorityResolution",
    "McpDescriptorBindingIdentity",
    "McpDescriptorRevisionAuthority",
    "McpDescriptorRevisionBinder",
]
