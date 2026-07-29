"""Bounded model-facing search, describe, and invoke adapters for one catalog.

Search is the only adapter that may reach a server, and it does so through the
bounded second tier — never by loading a schema into its own answer.  Describe
and invoke are deliberately incapable of loading anything.  Invoke dispatches
only through a non-model executor port that enters the ordinary Operation
Gateway, and only after the shared revision primitive confirms the reference is
still current.  All three operate on an immutable catalog projected for the
current run and recheck its subject and expiry on every call.

A catalog holds MCP *server* cards, so tier one can never answer at capability
granularity: every ref the model can actually act on is minted by tier-two
expansion, and those records are deliberately not catalog members.  The three
adapters therefore resolve against :class:`CapabilityCatalogAccess`, which spans
both the immutable catalog and the run's own disclosure ledger.  Without that
join a run could search, be shown a capability, and then be told it does not
exist — which is exactly the state BUG-08 named.

None of the three can ever resolve to another bridge tool.  That is structural
rather than checked here: a bridge name cannot become a catalog member, a
disclosed record is a :class:`CapabilityIndexEntry` and is refused by the same
validator, and the executor port accepts only a
:class:`CapabilityInvocationTarget`, which can only be produced from one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any, ClassVar

from pydantic import ValidationError

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogIdentityError,
    CapabilityDescribeRequest,
    CapabilityDescribeResult,
    CapabilityDescribeToolResult,
    CapabilityDescription,
    CapabilityDiscoveryErrorCode,
    CapabilityExecutorPort,
    CapabilityIndexEntry,
    CapabilityInvocationReceipt,
    CapabilityInvocationTarget,
    CapabilityInvokeRequest,
    CapabilityInvokeResult,
    CapabilityInvokeToolResult,
    CapabilityParameterHint,
    CapabilityRefBinding,
    CapabilitySchemaArtifactRef,
    CapabilitySchemaAvailability,
    CapabilitySchemaBounds,
    CapabilitySearchRequest,
    CapabilitySearchToolResult,
    ExpandedCapability,
)
from agent_runtime.capabilities.discovery.dispatch import (
    RunScopedCapabilityDisclosure,
)
from agent_runtime.capabilities.discovery.expansion import TwoTierCapabilitySearch
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.capabilities.discovery.revision_authority import (
    CapabilityRefRevalidation,
)
from agent_runtime.capabilities.discovery.schema_artifacts import (
    RunScopedSchemaArtifactPublisher,
)
from agent_runtime.execution.contracts import AgentRuntimeContext

_INTENT_TAG_LIMIT = CapabilitySchemaBounds.MAX_INTENT_TAGS
_PARAMETER_LIMIT = CapabilitySchemaBounds.MAX_PARAMETERS
_TAG_MAX_CHARS = CapabilitySchemaBounds.MAX_TAG_CHARS
_PARAMETER_VALUE_MAX_CHARS = CapabilitySchemaBounds.MAX_PARAMETER_CHARS

_INVALID_REQUEST_MESSAGE = "The capability discovery request is invalid."
_INACTIVE_CATALOG_MESSAGE = (
    "The capability catalog is unavailable for this run. Refresh discovery first."
)
_NOT_FOUND_MESSAGE = (
    "That capability is not present in the active catalog. Search again."
)
_STALE_CAPABILITY_MESSAGE = (
    "That capability reference is no longer current. Search again before invoking."
)
_UNAVAILABLE_CAPABILITY_MESSAGE = (
    "Capability invocation is unavailable for this run. Use the direct tools instead."
)
_EXECUTION_FAILED_MESSAGE = (
    "That capability could not be invoked. Try a different approach."
)
_INVALID_ARGUMENTS_MESSAGE = (
    "Those arguments do not match the capability's current schema. "
    "Describe it again before retrying."
)
_SEARCH_FAILED_MESSAGE = (
    "The capability search could not be completed. Try a narrower query."
)

_LOGGER = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CapabilityExecutionRefused(Exception):
    """A typed refusal a non-model executor raises instead of dispatching.

    It deliberately carries a *code only*.  The model-visible sentence is chosen
    by this module from :data:`_REFUSAL_MESSAGES`, so no connector, loader,
    registry, or store string can reach model output through the executor seam —
    sanitization is structural rather than a discipline each executor must
    remember.

    The admissible codes are a strict subset of
    :class:`CapabilityDiscoveryErrorCode`.  Two exclusions are load-bearing:
    an executor may not answer ``catalog_inactive``, which only the bridge's own
    subject/expiry recheck can decide, and it may not answer
    ``capability_not_found``, which is the bridge's membership answer — allowing
    it here would let a downstream component become the existence oracle the
    closed error vocabulary exists to prevent.  Any other code is coerced to
    :attr:`CapabilityDiscoveryErrorCode.EXECUTION_FAILED` rather than trusted.
    """

    ADMISSIBLE_CODES: ClassVar[frozenset[CapabilityDiscoveryErrorCode]] = frozenset(
        {
            CapabilityDiscoveryErrorCode.INVALID_REQUEST,
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE,
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE,
            CapabilityDiscoveryErrorCode.EXECUTION_FAILED,
        }
    )

    def __init__(self, code: CapabilityDiscoveryErrorCode) -> None:
        admitted = (
            code
            if code in self.ADMISSIBLE_CODES
            else CapabilityDiscoveryErrorCode.EXECUTION_FAILED
        )
        self.code = admitted
        super().__init__(admitted.value)


_REFUSAL_MESSAGES: dict[CapabilityDiscoveryErrorCode, str] = {
    CapabilityDiscoveryErrorCode.INVALID_REQUEST: _INVALID_ARGUMENTS_MESSAGE,
    CapabilityDiscoveryErrorCode.CAPABILITY_STALE: _STALE_CAPABILITY_MESSAGE,
    CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE: (
        _UNAVAILABLE_CAPABILITY_MESSAGE
    ),
    CapabilityDiscoveryErrorCode.EXECUTION_FAILED: _EXECUTION_FAILED_MESSAGE,
}


@dataclass(frozen=True)
class CapabilityCatalogAccess:
    """Injected run binding shared by all three model-facing discovery adapters.

    It answers two questions and nothing else: is this catalog still this run's,
    and is this opaque ref something this run may resolve.  The second question
    spans two sources — the immutable catalog of authorized server cards, and
    the ledger of what this run's own second tier disclosed — because a ref the
    model was shown by search must be describable and dispatchable by the same
    run.  ``disclosure`` is optional: a run that mounts no expansion resolves
    catalog members only, exactly as before.
    """

    catalog: CapabilityCatalog
    runtime_context: AgentRuntimeContext
    clock: Callable[[], datetime] = _utc_now
    disclosure: RunScopedCapabilityDisclosure | None = None

    class Messages:
        """Safe public messages for run-binding composition."""

        FOREIGN_DISCLOSURE: ClassVar[str] = (
            "the disclosure ledger must be scoped to this run's own catalog"
        )

    def __post_init__(self) -> None:
        # Two catalogs on one access would mean the ledger vouches for refs a
        # different projection minted, which is precisely the run-scoping this
        # object exists to hold. Checked rather than merely annotated, because a
        # frozen dataclass validates nothing on its own.
        if self.disclosure is not None and self.disclosure.catalog is not self.catalog:
            raise ValueError(self.Messages.FOREIGN_DISCLOSURE)

    def active_catalog(self) -> CapabilityCatalog | None:
        """Return the catalog only after exact run-subject and expiry checks."""

        if not self.catalog.is_active_for(
            self.runtime_context,
            now=self.clock(),
        ):
            return None
        return self.catalog

    def entry_for(
        self,
        catalog: CapabilityCatalog,
        capability_ref: str,
    ) -> CapabilityIndexEntry | None:
        """Return the record this run may resolve ``capability_ref`` to.

        ``catalog`` is taken as an argument rather than read from the field so a
        caller cannot reach this without having first passed
        :meth:`active_catalog`; resolving a ref for an expired or foreign
        catalog is not a state this method can be asked for.
        """

        member = next(
            (
                entry
                for entry in catalog.entries
                if entry.capability_ref == capability_ref
            ),
            None,
        )
        if member is not None:
            return member
        if self.disclosure is None:
            return None
        return self.disclosure.entry_for(capability_ref)

    def bind_ref(
        self,
        catalog: CapabilityCatalog,
        capability_ref: str,
    ) -> CapabilityRefBinding:
        """Bind one resolvable ref to the generation its catalog was built from.

        Catalog members bind through the catalog itself; records this run's
        expansion disclosed bind through the ledger that disclosed them.  Both
        narrow — an unknown ref binds nowhere — and both carry the same
        generation, so the shared revision primitive sees one kind of reference.
        """

        if any(entry.capability_ref == capability_ref for entry in catalog.entries):
            return catalog.bind_ref(capability_ref)
        if self.disclosure is None:
            raise CapabilityCatalogIdentityError(
                CapabilityCatalog.Messages.NOT_A_MEMBER
            )
        return self.disclosure.bind_ref(capability_ref)

    def record_disclosure(self, capabilities: Iterable[ExpandedCapability]) -> None:
        """Record what one expansion disclosed to this run, if it may hold it."""

        if self.disclosure is None:
            return
        self.disclosure.record(capabilities)


@dataclass(frozen=True)
class CapabilitySearchTool:
    """The one model-facing search adapter, with or without the second tier.

    There is deliberately a single search adapter rather than a catalog-only one
    and an expanding one.  Two would mean two names, two descriptions, and two
    output shapes that could drift, and the model must not be able to tell which
    one a run mounted — the answer shape is identical either way, so only the
    *reach* of the search differs.

    ``expansion`` is what gives the tool that reach.  A catalog holds MCP server
    cards, which name where capabilities live rather than a capability, so a
    run without it can only ever answer at server granularity.  With it, at most
    ``K`` ranked server cards are expanded under one shared deadline through the
    existing loader and F8 cache, and what comes back is recorded in the run's
    disclosure ledger so the very refs this answer names stay describable and
    dispatchable.

    Mounting expansion without that ledger would re-create the defect the ledger
    exists to close — an answer full of refs nothing else in the run can resolve
    — so the composition is refused rather than accepted and quietly broken.
    """

    access: CapabilityCatalogAccess
    ranker: DeterministicLexicalRanker = field(
        default_factory=DeterministicLexicalRanker
    )
    expansion: TwoTierCapabilitySearch | None = None
    local_tool_names: frozenset[str] = frozenset()
    name: str = CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
    description: str = (
        "Search the active run's authorized capability catalog. Returns at most "
        "10 opaque capability references and compact policy cues; it never "
        "loads a schema or invokes a capability."
    )

    class Messages:
        """Safe public messages for search-adapter composition."""

        UNRECORDABLE_EXPANSION: ClassVar[str] = (
            "an expanding search requires a disclosure ledger to record into"
        )
        SYNCHRONOUS_EXPANSION: ClassVar[str] = (
            "an expanding search must be awaited through ainvoke"
        )

    def __post_init__(self) -> None:
        if self.expansion is not None and self.access.disclosure is None:
            raise ValueError(self.Messages.UNRECORDABLE_EXPANSION)

    def invoke(
        self,
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Search the catalog alone, without loading any descriptors.

        Refusing outright when expansion is mounted is deliberate: silently
        answering from tier one would return a strictly narrower result than the
        run promised, and a narrower answer that looks like a complete one is
        worse than a composition error.
        """

        if self.expansion is not None:
            raise TypeError(self.Messages.SYNCHRONOUS_EXPANSION)
        request = self._parse(raw_input)
        if request is None:
            return self._invalid_request()
        catalog = self.access.active_catalog()
        if catalog is None:
            return self._inactive_catalog()
        return _dump(
            CapabilitySearchToolResult.ok(self.ranker.search(catalog, request))
        )

    async def ainvoke(
        self,
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Search, expanding at most ``K`` server cards when tier two is mounted."""

        expansion = self.expansion
        if expansion is None:
            return self.invoke(raw_input)
        request = self._parse(raw_input)
        if request is None:
            return self._invalid_request()
        catalog = self.access.active_catalog()
        if catalog is None:
            return self._inactive_catalog()
        try:
            result = await expansion.search(
                catalog=catalog,
                context=self.access.runtime_context,
                request=request,
                local_tool_names=self.local_tool_names,
            )
        except Exception:
            # Expansion reaches real servers through the loader. Its internal
            # detail never reaches model output, and a failed expansion narrows
            # to a refusal rather than to a silently catalog-only answer that
            # would look like a complete one.
            _LOGGER.warning("capability_search_expansion_failed", exc_info=True)
            return _dump(
                CapabilitySearchToolResult.fail(
                    CapabilityDiscoveryErrorCode.EXECUTION_FAILED,
                    _SEARCH_FAILED_MESSAGE,
                )
            )
        # Recorded before the answer is returned: a ref the model is about to be
        # shown must already be resolvable when it comes back with it.
        self.access.record_disclosure(result.expansion.capabilities)
        return _dump(CapabilitySearchToolResult.ok(result.search))

    async def __call__(
        self,
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Delegate to :meth:`ainvoke`."""

        return await self.ainvoke(raw_input)

    @staticmethod
    def _invalid_request() -> dict[str, Any]:
        return _dump(
            CapabilitySearchToolResult.fail(
                CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                _INVALID_REQUEST_MESSAGE,
            )
        )

    @staticmethod
    def _inactive_catalog() -> dict[str, Any]:
        return _dump(
            CapabilitySearchToolResult.fail(
                CapabilityDiscoveryErrorCode.CATALOG_INACTIVE,
                _INACTIVE_CATALOG_MESSAGE,
            )
        )

    @staticmethod
    def _parse(
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> CapabilitySearchRequest | None:
        if isinstance(raw_input, CapabilitySearchRequest):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {"query": raw_input}
        try:
            return CapabilitySearchRequest.model_validate(raw_input)
        except ValidationError:
            return None


@dataclass(frozen=True)
class CapabilityDescribeTool:
    """Return a bounded description, deferring an over-bound schema to an artifact.

    Describe answers with parameter hints whenever the capability's whole schema
    fits :class:`CapabilitySchemaBounds`, which is the ordinary case.  When it
    does not, the schema is *not* trimmed to fit: a prefix of a schema is
    indistinguishable, to the model, from the whole of one, and arguments
    authored against it would be authored against a contract the capability does
    not have.  Instead the schema is published whole through
    ``schema_artifacts`` and the description carries a protected reference to it.

    ``schema_artifacts`` is optional in the same way every other seam here is.
    A run that wires no publisher answers ``unavailable`` for an over-bound
    schema rather than falling back to a truncated one — fewer answers, never a
    wrong one.  The in-bound path is byte-identical either way.
    """

    access: CapabilityCatalogAccess
    schema_artifacts: RunScopedSchemaArtifactPublisher | None = None
    name: str = CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value
    description: str = (
        "Describe one opaque capability reference returned by "
        "search_capabilities. Returns only bounded compact metadata and "
        "parameter hints; it never returns a full schema or invokes a capability."
    )

    def invoke(
        self,
        raw_input: CapabilityDescribeRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Validate, authorize, and describe one current catalog member."""

        request = self._parse(raw_input)
        if request is None:
            return _dump(
                CapabilityDescribeToolResult.fail(
                    CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                    _INVALID_REQUEST_MESSAGE,
                )
            )
        catalog = self.access.active_catalog()
        if catalog is None:
            return _dump(
                CapabilityDescribeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CATALOG_INACTIVE,
                    _INACTIVE_CATALOG_MESSAGE,
                )
            )

        entry = self.access.entry_for(catalog, request.capability_ref)
        if entry is None:
            return _dump(
                CapabilityDescribeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND,
                    _NOT_FOUND_MESSAGE,
                )
            )

        return _dump(
            CapabilityDescribeToolResult.ok(
                CapabilityDescribeResult(
                    catalog_id=catalog.revision.catalog_id,
                    catalog_revision=catalog.revision.revision,
                    capability=_bounded_description(
                        entry,
                        artifact=self._schema_artifact_for(catalog, entry),
                    ),
                )
            )
        )

    def _schema_artifact_for(
        self,
        catalog: CapabilityCatalog,
        entry: CapabilityIndexEntry,
    ) -> CapabilitySchemaArtifactRef | None:
        """Publish an over-bound schema, or return ``None`` and defer to nothing.

        Publication is attempted only for a schema that genuinely does not fit,
        so the common path performs no store work at all.  Every failure — no
        publisher, an unbindable ref, a store that refused — narrows to ``None``,
        which the description renders as ``unavailable`` rather than as a
        partial schema.
        """

        publisher = self.schema_artifacts
        if publisher is None or CapabilitySchemaBounds.schema_fits_inline(
            parameter_names=entry.parameter_names,
            parameter_types=entry.parameter_types,
        ):
            return None
        try:
            binding = self.access.bind_ref(catalog, entry.capability_ref)
            return publisher.publish(entry=entry, binding=binding)
        except Exception:
            # Binding and publication both reach injected collaborators. Their
            # internal detail never reaches model output.
            _LOGGER.warning("capability_schema_artifact_unavailable", exc_info=True)
            return None

    async def ainvoke(
        self,
        raw_input: CapabilityDescribeRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Async-compatible pure adapter entry point."""

        return self.invoke(raw_input)

    async def __call__(
        self,
        raw_input: CapabilityDescribeRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Delegate to :meth:`ainvoke`."""

        return await self.ainvoke(raw_input)

    @staticmethod
    def _parse(
        raw_input: CapabilityDescribeRequest | Mapping[str, Any] | str,
    ) -> CapabilityDescribeRequest | None:
        if isinstance(raw_input, CapabilityDescribeRequest):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {"capability_ref": raw_input}
        try:
            return CapabilityDescribeRequest.model_validate(raw_input)
        except ValidationError:
            return None


@dataclass(frozen=True)
class CapabilityInvokeTool:
    """Invoke one catalog member through the non-model capability executor.

    The adapter itself performs no connector work.  It re-resolves the opaque
    reference inside the run's own catalog, re-asks the shared revision
    primitive whether that reference is still current, and only then hands a
    :class:`CapabilityInvocationTarget` to the executor, which enters the
    ordinary Operation Gateway.  Missing revalidation or a missing executor is
    a refusal, never a direct dispatch.
    """

    access: CapabilityCatalogAccess
    executor: CapabilityExecutorPort | None = None
    revalidation: CapabilityRefRevalidation | None = None
    name: str = CapabilityBridgeToolName.INVOKE_CAPABILITY.value
    description: str = (
        "Invoke one opaque capability reference returned by search_capabilities. "
        "The reference is re-authorized at call time and the real capability "
        "enforces its own approval, budget, and audit behavior."
    )

    async def ainvoke(
        self,
        raw_input: CapabilityInvokeRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate, re-authorize, and dispatch one bounded invocation."""

        request = self._parse(raw_input)
        if request is None:
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                    _INVALID_REQUEST_MESSAGE,
                )
            )
        catalog = self.access.active_catalog()
        generation = None if catalog is None else catalog.generation
        if catalog is None or generation is None:
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CATALOG_INACTIVE,
                    _INACTIVE_CATALOG_MESSAGE,
                )
            )
        entry = self.access.entry_for(catalog, request.capability_ref)
        if entry is None:
            # A bridge tool reference lands here and nowhere else: bridge names
            # can never be catalog members or disclosed records, so the model
            # cannot distinguish probing for the bridge from probing for any
            # unknown reference.
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND,
                    _NOT_FOUND_MESSAGE,
                )
            )
        revalidation = self.revalidation
        executor = self.executor
        if revalidation is None or executor is None:
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE,
                    _UNAVAILABLE_CAPABILITY_MESSAGE,
                )
            )
        try:
            decision = await revalidation.decide(
                binding=self.access.bind_ref(catalog, request.capability_ref),
                run_id=self.access.runtime_context.run_id,
                live_generation=generation,
            )
        except Exception:
            # Binding projection and revalidation are both typed, but the
            # revalidator itself is injected. An unusable revalidation path is a
            # refusal, and its internal detail never reaches model output.
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE,
                    _UNAVAILABLE_CAPABILITY_MESSAGE,
                )
            )
        if not decision.is_current:
            # Every non-current outcome — superseded, revoked, out of scope, or
            # an authority that could not answer — refuses identically, so the
            # closed reason code never becomes a model-visible oracle.
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.CAPABILITY_STALE,
                    _STALE_CAPABILITY_MESSAGE,
                )
            )
        return await self._execute(
            catalog=catalog,
            entry=entry,
            request=request,
            executor=executor,
        )

    async def __call__(
        self,
        raw_input: CapabilityInvokeRequest | Mapping[str, Any],
    ) -> dict[str, Any]:
        """Delegate to :meth:`ainvoke`."""

        return await self.ainvoke(raw_input)

    async def _execute(
        self,
        *,
        catalog: CapabilityCatalog,
        entry: CapabilityIndexEntry,
        request: CapabilityInvokeRequest,
        executor: CapabilityExecutorPort,
    ) -> dict[str, Any]:
        """Dispatch through the executor and bound whatever comes back."""

        try:
            target = CapabilityInvocationTarget.from_catalog_entry(entry)
            receipt = await executor.execute(
                target=target,
                arguments=request.arguments,
                idempotency_key=request.idempotency_key,
                runtime_context=self.access.runtime_context,
            )
        except CapabilityExecutionRefused as refusal:
            # A typed refusal names one closed code and no text. The sentence
            # comes from this module's own table, so a refusal cannot smuggle
            # connector detail into model output.
            return _dump(
                CapabilityInvokeToolResult.fail(
                    refusal.code,
                    _REFUSAL_MESSAGES.get(refusal.code, _EXECUTION_FAILED_MESSAGE),
                )
            )
        except Exception:
            # The executor is domain-supplied and may wrap connector, network,
            # or store failures. Internal detail never reaches model output.
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.EXECUTION_FAILED,
                    _EXECUTION_FAILED_MESSAGE,
                )
            )
        if (
            not isinstance(receipt, CapabilityInvocationReceipt)
            or receipt.capability_ref != request.capability_ref
        ):
            # An executor may not substitute a different capability for the one
            # the catalog authorized.
            return _dump(
                CapabilityInvokeToolResult.fail(
                    CapabilityDiscoveryErrorCode.EXECUTION_FAILED,
                    _EXECUTION_FAILED_MESSAGE,
                )
            )
        return _dump(
            CapabilityInvokeToolResult.ok(
                CapabilityInvokeResult(
                    catalog_id=catalog.revision.catalog_id,
                    catalog_revision=catalog.revision.revision,
                    receipt=receipt,
                )
            )
        )

    @staticmethod
    def _parse(
        raw_input: CapabilityInvokeRequest | Mapping[str, Any],
    ) -> CapabilityInvokeRequest | None:
        if isinstance(raw_input, CapabilityInvokeRequest):
            return raw_input
        try:
            return CapabilityInvokeRequest.model_validate(raw_input)
        except ValidationError:
            return None


def _bounded_description(
    entry: CapabilityIndexEntry,
    *,
    artifact: CapabilitySchemaArtifactRef | None = None,
) -> CapabilityDescription:
    """Project one entry into a description that never carries a partial schema.

    Intent tags are still trimmed to the bound, because a tag is a search cue
    and losing one costs a hint.  Parameters are not: they are the invocation
    contract, so the projection either carries all of them or none of them, and
    ``schema_availability`` says which happened.
    """

    tags = tuple(tag[:_TAG_MAX_CHARS] for tag in entry.intent_tags[:_INTENT_TAG_LIMIT])
    fits_inline = CapabilitySchemaBounds.schema_fits_inline(
        parameter_names=entry.parameter_names,
        parameter_types=entry.parameter_types,
    )
    if fits_inline:
        availability = CapabilitySchemaAvailability.INLINE
        parameter_hints = tuple(
            CapabilityParameterHint(
                name=name,
                type_hint=(
                    entry.parameter_types[index]
                    if index < len(entry.parameter_types)
                    else None
                ),
            )
            for index, name in enumerate(entry.parameter_names)
        )
    else:
        availability = (
            CapabilitySchemaAvailability.UNAVAILABLE
            if artifact is None
            else CapabilitySchemaAvailability.ARTIFACT
        )
        parameter_hints = ()
    metadata_truncated = len(entry.intent_tags) > _INTENT_TAG_LIMIT or any(
        len(tag) > _TAG_MAX_CHARS for tag in entry.intent_tags
    )
    return CapabilityDescription(
        capability_ref=entry.capability_ref,
        stable_name=entry.stable_name,
        display_name=entry.display_name,
        concise_description=entry.concise_description,
        source=entry.source,
        intent_tags=tags,
        parameters=parameter_hints,
        effect_class=entry.effect_class,
        approval_cue=entry.approval_cue,
        connector_label=entry.connector_label,
        descriptor_revision=entry.descriptor_revision,
        metadata_truncated=metadata_truncated,
        schema_availability=availability,
        schema_artifact=artifact
        if availability is (CapabilitySchemaAvailability.ARTIFACT)
        else None,
    )


def _dump(
    result: (
        CapabilitySearchToolResult
        | CapabilityDescribeToolResult
        | CapabilityInvokeToolResult
    ),
) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude_none=True)


__all__ = (
    "CapabilityCatalogAccess",
    "CapabilityDescribeTool",
    "CapabilityExecutionRefused",
    "CapabilityInvokeTool",
    "CapabilitySearchTool",
)
