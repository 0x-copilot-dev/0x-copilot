"""Protected schema artifacts: what describe hands out when a schema will not fit.

A capability whose input schema exceeds :class:`CapabilitySchemaBounds` has had
no honest representation.  Inlining a *prefix* of it is the worst of the
available answers: the model would author arguments against a schema the
capability does not have, and would have no way to tell that from a schema it
does.  This module is the second branch the F3.4 contract always named — the
schema is published whole, and describe returns a protected reference to it.

Two things are deliberately *not* invented here.

**Storage.**  The bytes go through the runtime's existing oversized-payload
seam, :data:`~agent_runtime.context.memory.summarization.OffloadWriter` — the
same ``Callable[[str], str]`` that parks an oversized tool result in the
content-addressed object store and returns a locator the ordinary read path
already resolves.  This module holds no store, writes no file, and reads no
bytes; it only decides who may learn the locator.

**Staleness.**  A protected reference is bound to exactly the run, subject, and
catalog generation its capability ref was bound to, and it is revalidated at use
through the shared Step RB primitive by way of
:class:`~agent_runtime.capabilities.discovery.revision_authority.CapabilityRefRevalidation`.
There is no private "is this still fresh" predicate here, because another
staleness implementation is precisely what Step RB exists to prevent.

The protection is therefore a *release* decision rather than an access-control
flag.  The locator never appears in a model-visible contract; it is disclosed
only after the ledger, the catalog, the binding, the shared revalidator, and the
keyed derivation all agree, so a reference echoed back by another run or another
subject resolves to nothing at all.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Protocol, runtime_checkable

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityCatalog,
    CapabilityIndexEntry,
    CapabilityRefBinding,
    CapabilitySchemaArtifactMinter,
    CapabilitySchemaArtifactRef,
    CapabilitySchemaDocument,
)
from agent_runtime.capabilities.discovery.revision_authority import (
    CapabilityRefRevalidation,
)
from agent_runtime.context.memory.summarization import OffloadWriter
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract

_LOGGER = logging.getLogger(__name__)

#: The write half of the protected-artifact path.  This is deliberately the
#: runtime's existing offload port rather than a discovery-local alias, so the
#: implementations F3 can be wired with are the ones already parking oversized
#: runtime payloads in the content-addressed object store.
CapabilitySchemaArtifactWriter = OffloadWriter


class CapabilitySchemaArtifactRelease(RuntimeContract):
    """What an authorized caller learns once a protected reference revalidates.

    ``locator`` is the reference the existing content-addressed read path
    already understands, so resolving an artifact adds no second read path.  It
    is absent from every model-visible contract: obtaining it means passing the
    resolver, not repeating a value the model was shown.
    """

    artifact_ref: str
    capability_ref: str
    locator: str
    content_digest: str
    parameter_count: int


class _SchemaArtifactRecord(RuntimeContract):
    """One published schema artifact, as this run's ledger remembers it.

    Internal by construction: it holds the locator, so it is never returned to a
    model-facing adapter and never embedded in a description.
    """

    artifact_ref: str
    capability_ref: str
    binding_digest: str
    content_digest: str
    locator: str
    parameter_count: int


@runtime_checkable
class CapabilityResolutionScope(Protocol):
    """The run binding a resolver needs, stated as the narrowest possible port.

    :class:`~agent_runtime.capabilities.discovery.tool_bridge.CapabilityCatalogAccess`
    satisfies this structurally.  Naming it as a protocol here rather than
    importing that class keeps the dependency one-way — the bridge composes this
    module, never the reverse — and keeps this module free of the adapters.
    """

    runtime_context: AgentRuntimeContext

    def active_catalog(self) -> CapabilityCatalog | None: ...

    def entry_for(
        self,
        catalog: CapabilityCatalog,
        capability_ref: str,
    ) -> CapabilityIndexEntry | None: ...

    def bind_ref(
        self,
        catalog: CapabilityCatalog,
        capability_ref: str,
    ) -> CapabilityRefBinding: ...


class RunScopedSchemaArtifactPublisher:
    """Publish over-bound capability schemas and remember what this run published.

    The ledger is per-run by construction, exactly like
    :class:`~agent_runtime.capabilities.discovery.dispatch.RunScopedCapabilityDisclosure`:
    a reference another run minted is simply absent from it, and an absent
    reference releases nothing.  That is the first of the resolver's narrowing
    checks rather than the only one — the ledger alone would still resolve if two
    runs were handed the same publisher, which is why the catalog, the binding,
    the shared revalidator, and the keyed derivation are each rechecked at use.

    ``minter`` must be keyed exactly as the catalog builder's was.  The
    derivation folds in the *binding* digest, so a reference is a function of the
    run, subject, and catalog generation rather than of the capability alone.
    """

    class Messages:
        """Safe public messages for publisher composition."""

        BINDING_MISMATCH: ClassVar[str] = (
            "a schema artifact must be bound to the capability it describes"
        )

    __slots__ = ("_minter", "_records", "_writer")

    def __init__(
        self,
        *,
        writer: CapabilitySchemaArtifactWriter,
        minter: CapabilitySchemaArtifactMinter,
    ) -> None:
        self._writer = writer
        self._minter = minter
        self._records: dict[str, _SchemaArtifactRecord] = {}

    def publish(
        self,
        *,
        entry: CapabilityIndexEntry,
        binding: CapabilityRefBinding,
    ) -> CapabilitySchemaArtifactRef | None:
        """Publish one capability's whole schema and return its protected ref.

        Returns ``None`` when the artifact could not be published.  Describe
        treats that as "no schema representation" rather than falling back to a
        truncated one: an unavailable schema is a worse answer than a complete
        one, but a *wrong* schema is worse than both.
        """

        if binding.capability_ref != entry.capability_ref:
            # A binding for a different capability would mint a reference whose
            # scope does not describe the document it points at.
            raise ValueError(self.Messages.BINDING_MISMATCH)
        document = CapabilitySchemaDocument.for_entry(entry)
        content_digest = document.content_digest
        try:
            locator = self._writer(document.canonical_content())
        except Exception:
            # The writer reaches a real store. Its internal detail never reaches
            # model output, and a failed publish narrows to "unavailable".
            _LOGGER.warning("capability_schema_artifact_publish_failed", exc_info=True)
            return None
        if not isinstance(locator, str) or not locator.strip():
            return None
        artifact_ref = self.expected_ref(
            binding=binding,
            content_digest=content_digest,
        )
        self._records[artifact_ref] = _SchemaArtifactRecord(
            artifact_ref=artifact_ref,
            capability_ref=entry.capability_ref,
            binding_digest=binding.binding_digest,
            content_digest=content_digest,
            locator=locator.strip(),
            parameter_count=document.parameter_count,
        )
        return CapabilitySchemaArtifactRef(
            artifact_ref=artifact_ref,
            parameter_count=document.parameter_count,
        )

    def record_for(self, artifact_ref: str) -> _SchemaArtifactRecord | None:
        """Return what this run published under ``artifact_ref``, or ``None``."""

        return self._records.get(artifact_ref)

    def expected_ref(
        self,
        *,
        binding: CapabilityRefBinding,
        content_digest: str,
    ) -> str:
        """Re-derive the reference a binding and content digest must produce."""

        return self._minter.mint_schema_artifact(
            binding_digest=binding.binding_digest,
            content_digest=content_digest,
        )


class CapabilitySchemaArtifactResolver:
    """Release a published schema's locator, but only to the run that owns it.

    Every check here narrows, and the ordering is deliberate: the cheapest
    structural refusals run before the shared revalidator is asked anything, and
    the keyed re-derivation runs last so a forged reference is refused even if
    every earlier lookup somehow admitted it.

    The staleness question is asked exactly once, and not by this class:
    :class:`CapabilityRefRevalidation` is the F3 instantiation of the Step RB
    primitive, and its closed decision is taken as final.  A resolver that
    compared generations itself would be the extra staleness implementation the
    primitive exists to prevent.
    """

    __slots__ = ("_publisher", "_revalidation", "_scope")

    def __init__(
        self,
        *,
        scope: CapabilityResolutionScope,
        publisher: RunScopedSchemaArtifactPublisher,
        revalidation: CapabilityRefRevalidation | None = None,
    ) -> None:
        self._scope = scope
        self._publisher = publisher
        self._revalidation = revalidation

    async def resolve(
        self,
        artifact_ref: str,
    ) -> CapabilitySchemaArtifactRelease | None:
        """Return the release for ``artifact_ref``, or ``None`` if it is not ours.

        Refusals are uniform and content-free on purpose.  A caller cannot tell
        "never published" from "published by another run" from "superseded", so
        the resolver never becomes an oracle for what some other run discovered.
        """

        revalidation = self._revalidation
        if revalidation is None:
            # An unrevalidatable reference is never usable, exactly as an
            # invocation with no revalidation seam is never dispatched.
            return None
        record = self._publisher.record_for(artifact_ref)
        if record is None:
            return None
        catalog = self._scope.active_catalog()
        generation = None if catalog is None else catalog.generation
        if catalog is None or generation is None:
            return None
        if self._scope.entry_for(catalog, record.capability_ref) is None:
            return None
        try:
            binding = self._scope.bind_ref(catalog, record.capability_ref)
        except Exception:
            # Binding narrows to this run's catalog and disclosure ledger; a ref
            # this run cannot bind is a ref this run cannot resolve.
            return None
        if binding.binding_digest != record.binding_digest:
            # The reference was minted under a different run, subject, or
            # catalog generation than the one asking for it.
            return None
        try:
            decision = await revalidation.decide(
                binding=binding,
                run_id=self._scope.runtime_context.run_id,
                live_generation=generation,
            )
        except Exception:
            _LOGGER.warning("capability_schema_artifact_revalidation_failed")
            return None
        if not decision.is_current:
            return None
        if (
            self._publisher.expected_ref(
                binding=binding,
                content_digest=record.content_digest,
            )
            != artifact_ref
        ):
            return None
        return CapabilitySchemaArtifactRelease(
            artifact_ref=record.artifact_ref,
            capability_ref=record.capability_ref,
            locator=record.locator,
            content_digest=record.content_digest,
            parameter_count=record.parameter_count,
        )


__all__ = (
    "CapabilityResolutionScope",
    "CapabilitySchemaArtifactRelease",
    "CapabilitySchemaArtifactResolver",
    "CapabilitySchemaArtifactWriter",
    "RunScopedSchemaArtifactPublisher",
)
