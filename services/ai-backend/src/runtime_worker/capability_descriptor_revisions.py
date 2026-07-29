"""The F8 descriptor revisions one run's catalog generation is keyed to.

F3 keys a catalog generation to four trusted inputs.  Three of them -- the
verified subject, the connector scope, and the F4 task-policy selection -- are
frozen for the life of a run by contract.  The fourth, the F8 descriptor
revisions, is the *only* one that can move while a run is still holding
references, so it is the only one that can make the shared Step RB revalidator
report a reference stale mid-run.  Supplying it is therefore not decoration:
without it the live authority re-derives the identical generation forever and
the revalidation lane contributes nothing to the safety property it exists for.

This module introduces no authority.  Every revision it reports is one the
trusted backend revision path already resolved for a verified subject through
:class:`~agent_runtime.capabilities.mcp.revision_resolver.McpDescriptorRevisionResolver`
-- the same resolver the MCP loader consults on every descriptor load, the same
one the F8 feed invalidates when the backend says a server moved.  This is a
projection of that answer into the shape F3's generation keys on, and nothing
else.

Three properties are deliberate:

*The read is synchronous, the refresh is not.*  ``CatalogDescriptorRevisionSourcePort``
is synchronous because ``RuntimeDependenciesFactory`` is, while the resolver is
async.  W2 crossed that boundary for MCP cards with an awaited snapshot folded
in at the composition root, and this is the same crossing -- with the one
addition the safety property requires: the snapshot is re-awaited each time the
live authority is asked, because a value captured once could never move.

*The source set is fixed when the catalog is projected.*  Revisions are read for
exactly the servers the run's authorized card snapshot named, and that set never
grows afterwards.  A source that discovered servers as the run opened them would
report a different generation after the first search than before it, and every
reference minted in the first turn would look stale for the rest of the run.

*Unresolved narrows, it never widens.*  A server whose revision cannot be
resolved -- untracked by the backend, momentarily unreachable, or missing the
server id registration needs -- contributes nothing.  The generation therefore
changes, and the revalidator refuses the reference: less capability, never more.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime.
    from agent_runtime.capabilities.discovery import CatalogDescriptorRevision
    from agent_runtime.capabilities.mcp.cards import McpServerCard
    from agent_runtime.execution.contracts import AgentRuntimeContext


_LOGGER = logging.getLogger(__name__)

#: Domain separation for the digest below, so an F3 catalog descriptor revision
#: can never equal a value derived here for any other purpose.
_REVISION_PURPOSE = "f3-catalog-descriptor-revision-v1"


@runtime_checkable
class McpRevisionResolverPort(Protocol):
    """The two F8 resolver calls this projection makes, and no others.

    Declared structurally rather than imported so the worker's composition root
    keeps naming only what it uses.  The production implementation is
    :class:`~agent_runtime.capabilities.mcp.revision_resolver.McpDescriptorRevisionResolver`,
    which is also what the MCP loader resolves through -- there is one revision
    authority in the process, not one per reader.
    """

    async def register(
        self, *, org_id: str, user_id: str, server_name: str, server_id: str
    ) -> None: ...

    async def resolve(self, *, org_id: str, user_id: str, server_name: str) -> Any: ...


@runtime_checkable
class RefreshableDescriptorRevisionSource(Protocol):
    """A descriptor-revision source that can be re-read against the authority.

    The plain :class:`CatalogDescriptorRevisionSourcePort` stays exactly as it
    was -- a synchronous callable -- so every source that has no live authority
    behind it keeps working unchanged.  This narrower shape is what lets the
    run-scoped generation authority re-read before it recomputes, and it is
    detected structurally so nothing has to be told which kind it holds.
    """

    async def refresh(self) -> None: ...

    def __call__(
        self, context: "AgentRuntimeContext"
    ) -> Sequence["CatalogDescriptorRevision"]: ...


class RunScopedDescriptorRevisions:
    """The F8 revisions for one run's authorized catalog, re-readable on demand.

    Constructing this performs no I/O: it only records which servers the run's
    card snapshot named.  :meth:`refresh` is where the resolver is consulted,
    and it is awaited twice in a run's life at minimum -- once at the composition
    root before the catalog is projected, and once per live-authority question
    thereafter.

    Neither method raises.  A refresh that cannot resolve a server drops it, and
    a refresh that fails wholesale leaves the snapshot empty rather than stale:
    an empty snapshot changes the generation and fails the reference closed,
    while a retained one would report a moved catalog as current.
    """

    def __init__(
        self,
        *,
        resolver: McpRevisionResolverPort,
        org_id: str,
        user_id: str,
        sources: Sequence[tuple[str, str, str]],
    ) -> None:
        self._resolver = resolver
        self._org_id = org_id
        self._user_id = user_id
        # ``(source_id, server_name, server_id)``, deduplicated and ordered so
        # two refreshes of an unchanged deployment produce an identical tuple.
        #
        # Deliberately not truncated. A prefix bound would be cheaper on a large
        # tenant and would silently stop keying the generation on every server
        # past the cut -- so a descriptor moving on one of them would go
        # unnoticed, which is the exact failure this whole projection exists to
        # remove. The set is already bounded by the cards the run was authorized
        # to see, which is the same set the catalog indexes.
        self._sources = tuple(sorted(set(sources)))
        self._snapshot: tuple[CatalogDescriptorRevision, ...] = ()
        self._registered = False

    @classmethod
    def for_cards(
        cls,
        *,
        resolver: McpRevisionResolverPort | None,
        context: "AgentRuntimeContext",
        cards: Sequence["McpServerCard"],
    ) -> "RunScopedDescriptorRevisions | None":
        """Project the run's authorized cards into a revision source, or ``None``.

        ``None`` is the right answer for every deployment with no F8 revision
        authority wired, and it is what keeps this lane byte-identical to the
        behaviour that shipped: the composer folds zero revisions, exactly as it
        did before.

        A card with no ``server_id`` is skipped rather than guessed at.  The
        resolver is keyed by the backend's server id, so there is nothing to
        register it under, and inventing one would ask the authority about a
        server it has never heard of.
        """

        if resolver is None:
            return None
        sources: list[tuple[str, str, str]] = []
        for card in cards:
            server_id = card.server_id
            if not server_id:
                continue
            sources.append((cls.source_id(card), card.name, server_id))
        if not sources:
            return None
        return cls(
            resolver=resolver,
            org_id=context.org_id,
            user_id=context.user_id,
            sources=sources,
        )

    @staticmethod
    def source_id(card: "McpServerCard") -> str:
        """Name the catalog source a revision belongs to.

        The generation only requires this to be stable across refreshes and
        unique per source -- it digests ``(source, revision)`` pairs and refuses
        two revisions for one source, and nothing cross-checks the value against
        a catalog entry.  It is nonetheless shaped like the identity
        :class:`~agent_runtime.capabilities.discovery.builder.AuthorizedCatalogBuilder`
        gives the same card, so a generation is legible next to the catalog it
        keys.  The label comes from the shared enum rather than a literal;
        server ids and names are normalized slugs, so the separator cannot be
        confused for content.
        """

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilitySource,
        )

        source_id = card.server_id or card.name
        return f"{CapabilitySource.MCP_SERVER.value}:{source_id}:{card.name}"

    @property
    def sources(self) -> tuple[tuple[str, str, str], ...]:
        """Return the fixed server set this source reports revisions for."""

        return self._sources

    async def refresh(self) -> None:
        """Re-read every source's trusted revision from the F8 authority.

        Registration is idempotent and is the same call the MCP loader makes
        before it loads a server, so a run that goes on to open one pays for it
        once rather than twice.  Resolution is served from the resolver's own
        TTL cache, which is shared by every run in the worker process: the
        network is reached when that TTL lapses or when the F8 feed invalidated
        the entry -- and the second of those is precisely the moment a revision
        moved.

        The loop is sequential rather than gathered.  On a warm resolver every
        iteration is a dictionary lookup, so the cost is a cold worker's first
        composition for a subject -- one bounded question per authorized server,
        never a fan-out proportional to anything the model said.
        """

        resolved: list[CatalogDescriptorRevision] = []
        for source_id, server_name, server_id in self._sources:
            revision = await self._revision_for(
                server_name=server_name,
                server_id=server_id,
            )
            if revision is None:
                continue
            entry = self._entry(source_id=source_id, revision=revision)
            if entry is not None:
                resolved.append(entry)
        self._registered = True
        self._snapshot = tuple(resolved)

    def __call__(
        self, context: "AgentRuntimeContext"
    ) -> Sequence["CatalogDescriptorRevision"]:
        """Return the revisions as of the last refresh.

        The context is accepted because the port's shape says a source may key
        on it; this one is already bound to a single run's verified subject, so
        reading it here would be reading the same facts twice.
        """

        del context
        return self._snapshot

    async def _revision_for(self, *, server_name: str, server_id: str) -> str | None:
        """Return one server's opaque backend revision, or ``None`` if unknown."""

        try:
            if not self._registered:
                # Registration is what makes a server resolvable at all; after
                # the first refresh the resolver already holds the key, and
                # re-registering an unchanged ``server_id`` is a no-op anyway.
                await self._resolver.register(
                    org_id=self._org_id,
                    user_id=self._user_id,
                    server_name=server_name,
                    server_id=server_id,
                )
            result = await self._resolver.resolve(
                org_id=self._org_id,
                user_id=self._user_id,
                server_name=server_name,
            )
        except Exception:
            # An unreachable or misbehaving authority narrows this run rather
            # than failing it: the source drops out, the generation changes, and
            # the reference is refused at use time.
            _LOGGER.debug(
                "capability_discovery.descriptor_revision_unresolved",
                exc_info=True,
            )
            return None
        revision = getattr(result, "revision", None)
        if revision is None:
            # ``not_found`` (the backend tracks no revision for this server) and
            # ``unavailable`` both arrive this way. Neither is a revision.
            return None
        return self._digest(revision)

    @staticmethod
    def _digest(revision: object) -> str | None:
        """Digest the backend revision into one opaque, comparable value.

        The backend's answer carries the revision string alongside the profile
        and subject-scope it was resolved under.  All three are folded, because
        a catalog projected under one profile is not the same catalog as one
        projected under another even when the revision string happens to match.
        Digesting keeps the boundary body-free and length-bounded, and -- being
        injective over a closed key set -- preserves the only relation the
        generation is allowed to use, equality.
        """

        from agent_runtime.surfaces_v2.canonical_json import (  # noqa: PLC0415
            canonical_json_sha256,
        )

        keyed: Mapping[str, object] = {
            "purpose": _REVISION_PURPOSE,
            "revision": str(getattr(revision, "revision", "")),
            "profile_id": str(getattr(revision, "profile_id", "")),
            "subject_scope_hash": str(getattr(revision, "subject_scope_hash", "")),
        }
        if not keyed["revision"]:
            return None
        return canonical_json_sha256(keyed)

    @staticmethod
    def _entry(
        *,
        source_id: str,
        revision: str,
    ) -> "CatalogDescriptorRevision | None":
        """Build the contract, or drop the source if it will not validate."""

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CatalogDescriptorRevision,
        )

        try:
            return CatalogDescriptorRevision(
                source_id=source_id,
                descriptor_revision=revision,
            )
        except Exception:  # pragma: no cover - both inputs are derived here.
            _LOGGER.debug(
                "capability_discovery.descriptor_revision_unrepresentable",
                exc_info=True,
            )
            return None


__all__ = (
    "McpRevisionResolverPort",
    "RefreshableDescriptorRevisionSource",
    "RunScopedDescriptorRevisions",
)
