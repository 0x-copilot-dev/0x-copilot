"""Bounded model-facing search and describe adapters for one active catalog.

These adapters are deliberately incapable of loading or invoking a capability.
They operate only on an immutable catalog projected for the current run and
recheck its subject and expiry on every call.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityCatalog,
    CapabilityDescribeRequest,
    CapabilityDescribeResult,
    CapabilityDescribeToolResult,
    CapabilityDescription,
    CapabilityDiscoveryErrorCode,
    CapabilityIndexEntry,
    CapabilityParameterHint,
    CapabilitySearchRequest,
    CapabilitySearchToolResult,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
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
    name: str = "search_capabilities"
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
    name: str = "describe_capability"
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
    result: CapabilitySearchToolResult | CapabilityDescribeToolResult,
) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude_none=True)


__all__ = (
    "CapabilityCatalogAccess",
    "CapabilityDescribeTool",
    "CapabilitySearchTool",
)
