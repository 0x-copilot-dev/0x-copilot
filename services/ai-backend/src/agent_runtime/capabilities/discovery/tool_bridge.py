"""Bounded model-facing search, describe, and invoke adapters for one catalog.

Search and describe are deliberately incapable of loading or invoking a
capability.  Invoke dispatches only through a non-model executor port that
enters the ordinary Operation Gateway, and only after the shared revision
primitive confirms the reference is still current.  All three operate on an
immutable catalog projected for the current run and recheck its subject and
expiry on every call.

None of the three can ever resolve to another bridge tool.  That is structural
rather than checked here: a bridge name cannot become a catalog member, and the
executor port accepts only a :class:`CapabilityInvocationTarget`, which can only
be produced from a catalog member.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityBridgeToolName,
    CapabilityCatalog,
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
    CapabilitySearchRequest,
    CapabilitySearchToolResult,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.capabilities.discovery.revision_authority import (
    CapabilityRefRevalidation,
)
from agent_runtime.execution.contracts import AgentRuntimeContext

_INTENT_TAG_LIMIT = 16
_PARAMETER_LIMIT = 32
_TAG_MAX_CHARS = 64
_PARAMETER_VALUE_MAX_CHARS = 96

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


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CapabilityCatalogAccess:
    """Injected run binding used by both model-facing discovery operations."""

    catalog: CapabilityCatalog
    runtime_context: AgentRuntimeContext
    clock: Callable[[], datetime] = _utc_now

    def active_catalog(self) -> CapabilityCatalog | None:
        """Return the catalog only after exact run-subject and expiry checks."""

        if not self.catalog.is_active_for(
            self.runtime_context,
            now=self.clock(),
        ):
            return None
        return self.catalog


@dataclass(frozen=True)
class CapabilitySearchTool:
    """Pure search adapter suitable for later ``StructuredTool`` wrapping."""

    access: CapabilityCatalogAccess
    ranker: DeterministicLexicalRanker = field(
        default_factory=DeterministicLexicalRanker
    )
    name: str = CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
    description: str = (
        "Search the active run's authorized capability catalog. Returns at most "
        "10 opaque capability references and compact policy cues; it never "
        "loads a schema or invokes a capability."
    )

    def invoke(
        self,
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Validate, authorize, and search without loading any descriptors."""

        request = self._parse(raw_input)
        if request is None:
            return _dump(
                CapabilitySearchToolResult.fail(
                    CapabilityDiscoveryErrorCode.INVALID_REQUEST,
                    _INVALID_REQUEST_MESSAGE,
                )
            )
        catalog = self.access.active_catalog()
        if catalog is None:
            return _dump(
                CapabilitySearchToolResult.fail(
                    CapabilityDiscoveryErrorCode.CATALOG_INACTIVE,
                    _INACTIVE_CATALOG_MESSAGE,
                )
            )
        return _dump(
            CapabilitySearchToolResult.ok(self.ranker.search(catalog, request))
        )

    async def ainvoke(
        self,
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Async-compatible pure adapter entry point."""

        return self.invoke(raw_input)

    async def __call__(
        self,
        raw_input: CapabilitySearchRequest | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Delegate to :meth:`ainvoke`."""

        return await self.ainvoke(raw_input)

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
    """Return bounded schema-free metadata for one search result."""

    access: CapabilityCatalogAccess
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

        entry = _find_entry(catalog, request.capability_ref)
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
                    capability=_bounded_description(entry),
                )
            )
        )

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
        entry = _find_entry(catalog, request.capability_ref)
        if entry is None:
            # A bridge tool reference lands here and nowhere else: bridge names
            # can never be catalog members, so the model cannot distinguish
            # probing for the bridge from probing for any unknown reference.
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
                binding=catalog.bind_ref(request.capability_ref),
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


def _bounded_description(entry: CapabilityIndexEntry) -> CapabilityDescription:
    tags = tuple(tag[:_TAG_MAX_CHARS] for tag in entry.intent_tags[:_INTENT_TAG_LIMIT])
    parameter_hints = tuple(
        CapabilityParameterHint(
            name=name[:_PARAMETER_VALUE_MAX_CHARS],
            type_hint=(
                entry.parameter_types[index][:_PARAMETER_VALUE_MAX_CHARS]
                if index < len(entry.parameter_types)
                else None
            ),
        )
        for index, name in enumerate(entry.parameter_names[:_PARAMETER_LIMIT])
    )
    metadata_truncated = (
        len(entry.intent_tags) > _INTENT_TAG_LIMIT
        or len(entry.parameter_names) > _PARAMETER_LIMIT
        or any(len(tag) > _TAG_MAX_CHARS for tag in entry.intent_tags)
        or any(
            len(value) > _PARAMETER_VALUE_MAX_CHARS
            for value in (*entry.parameter_names, *entry.parameter_types)
        )
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
    )


def _find_entry(
    catalog: CapabilityCatalog,
    capability_ref: str,
) -> CapabilityIndexEntry | None:
    return next(
        (entry for entry in catalog.entries if entry.capability_ref == capability_ref),
        None,
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
    "CapabilityInvokeTool",
    "CapabilitySearchTool",
)
