"""Bounded, deadline-governed second-tier expansion of authorized server cards.

Tier one is the compact catalog: authorized MCP *server* cards, which cost no
connection to rank.  A server card names where capabilities live, not a
capability, so tier two is what turns one into records the model can actually
act on: at most ``K`` ranked server cards are expanded through the existing
:class:`McpLoader` — and therefore through the F8 revision-aware discovery
cache — and their descriptors are projected into schema-free records the same
ranker can score.

Four properties are load-bearing, and each is enforced structurally rather
than by convention:

1. **Bounded fan-out.** Only ``limits.max_servers`` cards are ever admitted, so
   a cold discovery opens at most ``K`` servers no matter how many are
   authorized.  A warm discovery issues no second list call because the loader
   already resolves through the discovery cache.
2. **Single-flight.** Concurrent expansions of the same server share the
   cache's existing per-key load cohort.  This module deliberately adds no
   second single-flight layer; a private one would fragment the cohort and
   reintroduce the thundering herd it was written to prevent.
3. **One total deadline.** The budget is taken once, at entry, and covers
   ranking plus every concurrent load.  It is not a per-server timeout, so a
   slow server spends the shared budget rather than multiplying it.
4. **Partial failure narrows.** A server that fails, is denied, or is still
   running when the deadline expires contributes *zero* capabilities. There is
   no fallback that substitutes another source, and
   :class:`CapabilityExpansionResult` refuses to hold a capability whose owning
   server did not successfully expand — so "failure widened the result" is not
   a state this contract can represent.

Descriptors arriving from an MCP server are untrusted. They are projected into
name/description/parameter-name metadata only: no input schema is copied into
the index, so expansion never duplicates full schemas into the prompt. Nothing
a server asserts about itself may *lower* its disclosed effect or approval
posture; server-supplied risk signals are honored only when they escalate.

This module owns only the *executable* second tier. The shapes it produces live
in :mod:`agent_runtime.capabilities.discovery.contracts`, and the bounds it is
held to are resolved from configuration in
:mod:`agent_runtime.capabilities.discovery.activation`; both are re-exported
here so existing call sites keep resolving to the one definition.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from agent_runtime.capabilities.discovery.activation import CapabilityExpansionLimits
from agent_runtime.capabilities.discovery.contracts import (
    ApprovalCue,
    CapabilityCatalog,
    CapabilityExpansionError,
    CapabilityExpansionOutcome,
    CapabilityExpansionResult,
    CapabilityExpansionState,
    CapabilityIndexEntry,
    CapabilityReferenceMinter,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySearchResult,
    CapabilitySource,
    CatalogEffectClass,
    ExpandedCapability,
    HmacCapabilityReferenceMinter,
    RankedCapabilitySelection,
    TwoTierCapabilitySearchResult,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.capabilities.mcp.cards import (
    JsonSchema,
    McpLoadRequest,
    McpLoadResult,
    McpRiskLevel,
    McpToolDescriptor,
)
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.execution.contracts import AgentRuntimeContext

_LOGGER = logging.getLogger(__name__)


class ExpandedCapabilityProjector:
    """Project untrusted MCP descriptors into schema-free catalog-shaped records.

    Only names, a truncated description, and parameter name/type hints survive.
    The input schema itself is never copied, so tier two adds no full-schema
    prompt load. Effect class stays :attr:`CatalogEffectClass.UNKNOWN` because a
    server asserting ``readOnlyHint`` about itself must not be able to present
    as safer than an unclassified capability; risk signals are read only in the
    escalating direction.
    """

    _MAX_PARAMETERS: ClassVar[int] = 32
    _DESCRIPTION_MAX_CHARS: ClassVar[int] = 512
    _PARAMETER_MAX_CHARS: ClassVar[int] = 96
    _UNKNOWN_TYPE: ClassVar[str] = "unknown"
    _IDENTITY_PREFIX: ClassVar[str] = "tool"

    def project(
        self,
        *,
        catalog_id: str,
        owner: CapabilityIndexEntry,
        tool: McpToolDescriptor,
        minter: CapabilityReferenceMinter,
    ) -> ExpandedCapability | None:
        """Return one projected capability, or ``None`` when it cannot be safe."""

        stable_name = tool.name.strip()
        if not stable_name:
            return None
        parameter_names, parameter_types = self._parameters(tool.input_schema)
        identity = (
            f"{CapabilitySource.MCP_SERVER.value}:{self._IDENTITY_PREFIX}:"
            f"{owner.capability_ref}:{stable_name}"
        )
        entry = CapabilityIndexEntry(
            capability_ref=minter.mint(catalog_id=catalog_id, identity=identity),
            source=CapabilitySource.MCP_SERVER,
            stable_name=stable_name,
            display_name=stable_name,
            concise_description=self._description(tool, fallback=stable_name),
            parameter_names=parameter_names,
            parameter_types=parameter_types,
            effect_class=CatalogEffectClass.UNKNOWN,
            approval_cue=self._approval_cue(tool),
            connector_label=owner.connector_label,
        )
        return ExpandedCapability(
            owner_capability_ref=owner.capability_ref,
            server_name=owner.stable_name,
            tool_name=stable_name,
            entry=entry,
        )

    @classmethod
    def _description(cls, tool: McpToolDescriptor, *, fallback: str) -> str:
        description = tool.description.strip()[: cls._DESCRIPTION_MAX_CHARS].strip()
        return description or fallback

    @classmethod
    def _approval_cue(cls, tool: McpToolDescriptor) -> ApprovalCue:
        escalates = (
            tool.risk_level
            in {
                McpRiskLevel.HIGH,
                McpRiskLevel.CRITICAL,
            }
            or tool.read_only is False
        )
        return ApprovalCue.POLICY_DEPENDENT if escalates else ApprovalCue.UNKNOWN

    @classmethod
    def _parameters(
        cls,
        input_schema: JsonSchema,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        properties = (
            input_schema.get("properties")
            if isinstance(input_schema, Mapping)
            else None
        )
        if not isinstance(properties, Mapping):
            return (), ()
        names: list[str] = []
        types: list[str] = []
        seen: set[str] = set()
        for raw_name, raw_specification in properties.items():
            if len(names) >= cls._MAX_PARAMETERS:
                break
            if not isinstance(raw_name, str):
                continue
            normalized = raw_name.strip()[: cls._PARAMETER_MAX_CHARS].strip()
            if not normalized or normalized.casefold() in seen:
                continue
            seen.add(normalized.casefold())
            names.append(normalized)
            types.append(cls._type_hint(raw_specification))
        return tuple(names), tuple(types)

    @classmethod
    def _type_hint(cls, raw_specification: object) -> str:
        if isinstance(raw_specification, Mapping):
            raw_type = raw_specification.get("type")
            if isinstance(raw_type, str) and raw_type.strip():
                return raw_type.strip()[: cls._PARAMETER_MAX_CHARS]
        return cls._UNKNOWN_TYPE


@dataclass(frozen=True, slots=True)
class _ServerExpansion:
    """One server's contribution: an outcome plus the records it may admit."""

    outcome: CapabilityExpansionOutcome
    capabilities: tuple[ExpandedCapability, ...] = ()


class BoundedCapabilityExpander:
    """Expand at most ``K`` ranked server cards under one shared deadline.

    Concurrency is intentionally thin. Loads run through
    :class:`McpLoader`, which already routes every descriptor read through the
    discovery cache's per-key single-flight cohort, so two concurrent
    expansions of the same server produce one underlying load. The deadline is
    a single timer over the whole admitted set, injectable as ``sleep`` so
    tests drive it deterministically instead of waiting on a wall clock.
    """

    class Messages:
        """Safe public messages for expander preconditions."""

        SUBJECT_MISMATCH = "catalog scope does not match the runtime context"

    def __init__(
        self,
        *,
        loader: McpLoader,
        minter: CapabilityReferenceMinter,
        limits: CapabilityExpansionLimits | None = None,
        ranker: DeterministicLexicalRanker | None = None,
        projector: ExpandedCapabilityProjector | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._loader = loader
        self._minter = minter
        self._limits = limits or CapabilityExpansionLimits()
        self._ranker = ranker or DeterministicLexicalRanker()
        self._projector = projector or ExpandedCapabilityProjector()
        self._clock = clock
        self._sleep = sleep

    @property
    def limits(self) -> CapabilityExpansionLimits:
        """Return the configured bounds, including the ``K`` server budget."""

        return self._limits

    async def expand(
        self,
        *,
        catalog: CapabilityCatalog,
        context: AgentRuntimeContext,
        request: CapabilitySearchRequest,
        local_tool_names: frozenset[str] = frozenset(),
    ) -> CapabilityExpansionResult:
        """Rank server cards, expand at most ``K``, and never widen on failure."""

        if not catalog.scope.matches(context):
            raise CapabilityExpansionError(self.Messages.SUBJECT_MISMATCH)

        started_at = self._clock()
        server_request = self._server_request(request)
        if server_request is None:
            return CapabilityExpansionResult.empty(max_servers=self._limits.max_servers)

        selection = self._ranker.rank_entries(catalog.entries, server_request)
        admitted = self._admitted_entries(catalog, selection)
        if not admitted:
            return CapabilityExpansionResult(
                max_servers=self._limits.max_servers,
                considered_count=selection.scanned_count,
            )

        budget_seconds = self._limits.total_deadline_seconds - (
            self._clock() - started_at
        )
        if budget_seconds <= 0:
            return self._assemble(
                admitted=admitted,
                expansions=[
                    self._timed_out(entry.capability_ref) for entry in admitted
                ],
                considered_count=selection.scanned_count,
                deadline_exceeded=True,
            )

        expansions, deadline_exceeded = await self._run_bounded_loads(
            admitted=admitted,
            context=context,
            local_tool_names=local_tool_names,
            catalog_id=catalog.revision.catalog_id,
            budget_seconds=budget_seconds,
        )
        return self._assemble(
            admitted=admitted,
            expansions=expansions,
            considered_count=selection.scanned_count,
            deadline_exceeded=deadline_exceeded,
        )

    def _server_request(
        self,
        request: CapabilitySearchRequest,
    ) -> CapabilitySearchRequest | None:
        """Narrow the caller's request to the server tier, or refuse entirely.

        A caller filter that already excludes MCP servers must not be widened
        back open by the expansion pass, so it disables tier two outright.
        """

        filters = request.filters
        if filters.sources and CapabilitySource.MCP_SERVER not in filters.sources:
            return None
        return CapabilitySearchRequest(
            query=request.query,
            limit=self._limits.max_servers,
            filters=CapabilitySearchFilters(
                sources={CapabilitySource.MCP_SERVER},
                effect_classes=filters.effect_classes,
                connector_labels=filters.connector_labels,
            ),
        )

    @staticmethod
    def _admitted_entries(
        catalog: CapabilityCatalog,
        selection: RankedCapabilitySelection,
    ) -> tuple[CapabilityIndexEntry, ...]:
        """Resolve ranked refs back to catalog members, preserving rank order."""

        by_ref = {entry.capability_ref: entry for entry in catalog.entries}
        return tuple(
            entry
            for entry in (
                by_ref.get(candidate.capability_ref)
                for candidate in selection.candidates
            )
            if entry is not None
        )

    async def _run_bounded_loads(
        self,
        *,
        admitted: Sequence[CapabilityIndexEntry],
        context: AgentRuntimeContext,
        local_tool_names: frozenset[str],
        catalog_id: str,
        budget_seconds: float,
    ) -> tuple[list[_ServerExpansion], bool]:
        """Run every admitted load concurrently under one shared deadline."""

        tasks = [
            asyncio.create_task(
                self._expand_one(
                    entry=entry,
                    context=context,
                    local_tool_names=local_tool_names,
                    catalog_id=catalog_id,
                )
            )
            for entry in admitted
        ]
        completion = asyncio.create_task(self._await_all(tasks))
        timer = asyncio.create_task(self._sleep(budget_seconds))
        try:
            await asyncio.wait(
                {completion, timer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            deadline_exceeded = not completion.done()
        finally:
            timer.cancel()
            completion.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(
                completion,
                timer,
                *tasks,
                return_exceptions=True,
            )

        expansions: list[_ServerExpansion] = []
        for entry, task in zip(admitted, tasks, strict=True):
            if task.cancelled() or not task.done():
                expansions.append(self._timed_out(entry.capability_ref))
                continue
            if task.exception() is not None:
                expansions.append(self._unavailable(entry.capability_ref))
                continue
            expansions.append(task.result())
        return expansions, deadline_exceeded

    @staticmethod
    async def _await_all(tasks: Sequence[asyncio.Task[_ServerExpansion]]) -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _expand_one(
        self,
        *,
        entry: CapabilityIndexEntry,
        context: AgentRuntimeContext,
        local_tool_names: frozenset[str],
        catalog_id: str,
    ) -> _ServerExpansion:
        """Load one authorized server and project only what it really returned."""

        try:
            result = await self._loader.load_server(
                McpLoadRequest(
                    server_name=entry.stable_name,
                    runtime_context=context,
                    local_tool_names=local_tool_names,
                )
            )
        except Exception:  # noqa: BLE001 - a broken server narrows, never raises.
            _LOGGER.warning(
                "Bounded capability expansion failed for one server card",
                exc_info=True,
            )
            return self._unavailable(entry.capability_ref)

        loaded = self._loaded_server(result)
        if loaded is None:
            return self._unavailable(entry.capability_ref)

        capabilities = tuple(
            capability
            for capability in (
                self._projector.project(
                    catalog_id=catalog_id,
                    owner=entry,
                    tool=tool,
                    minter=self._minter,
                )
                for tool in loaded[: self._limits.max_capabilities_per_server]
            )
            if capability is not None
        )
        return _ServerExpansion(
            outcome=CapabilityExpansionOutcome(
                capability_ref=entry.capability_ref,
                state=CapabilityExpansionState.EXPANDED,
                admitted_count=len(capabilities),
            ),
            capabilities=capabilities,
        )

    @staticmethod
    def _loaded_server(
        result: McpLoadResult,
    ) -> tuple[McpToolDescriptor, ...] | None:
        """Return descriptors only for a genuinely successful load."""

        if not result.succeeded or result.loaded_server is None:
            return None
        return result.loaded_server.tools

    @staticmethod
    def _timed_out(capability_ref: str) -> _ServerExpansion:
        return _ServerExpansion(
            outcome=CapabilityExpansionOutcome(
                capability_ref=capability_ref,
                state=CapabilityExpansionState.DEADLINE_EXCEEDED,
            )
        )

    @staticmethod
    def _unavailable(capability_ref: str) -> _ServerExpansion:
        return _ServerExpansion(
            outcome=CapabilityExpansionOutcome(
                capability_ref=capability_ref,
                state=CapabilityExpansionState.UNAVAILABLE,
            )
        )

    def _assemble(
        self,
        *,
        admitted: Sequence[CapabilityIndexEntry],
        expansions: Sequence[_ServerExpansion],
        considered_count: int,
        deadline_exceeded: bool,
    ) -> CapabilityExpansionResult:
        """Build the result in ranked order so concurrency cannot reorder it."""

        return CapabilityExpansionResult(
            max_servers=self._limits.max_servers,
            considered_count=considered_count,
            admitted_count=len(admitted),
            deadline_exceeded=deadline_exceeded,
            outcomes=tuple(expansion.outcome for expansion in expansions),
            capabilities=tuple(
                capability
                for expansion in expansions
                for capability in expansion.capabilities
            ),
        )


class TwoTierCapabilitySearch:
    """Compose compact-card search with bounded server expansion.

    Tier one ranks the catalog, which holds only MCP *server* cards. A server
    card names a connector rather than a capability, so no tier-one candidate is
    ever something the model can invoke; every capability-granularity answer
    comes from tier two. Expansion is therefore not a fallback for a weak tier
    one — it is the only tier that can produce one.

    There is consequently no suppression heuristic here. The predecessor gate
    counted tier-one candidates whose source was ``TOOL_CARD``; product tool
    cards are no longer catalog members — they have no non-model dispatcher and
    stay directly registered — so that count is now structurally zero, and a
    gate reading it could only be an always-expand switch wearing the costume of
    a threshold. Cost is bounded by the expander's own contract instead: at most
    ``K`` servers under one shared deadline, resolved through the F8 discovery
    cache, so a warm expansion issues no list call. A caller filter that
    excludes MCP servers still disables tier two outright, inside
    :meth:`BoundedCapabilityExpander.expand`.

    Defining "the catalog cannot satisfy this query confidently" as a real,
    specified rule is open work. When one exists it belongs here, measured
    against something that can be true.
    """

    def __init__(
        self,
        *,
        expander: BoundedCapabilityExpander,
        ranker: DeterministicLexicalRanker | None = None,
    ) -> None:
        self._expander = expander
        self._ranker = ranker or DeterministicLexicalRanker()

    async def search(
        self,
        *,
        catalog: CapabilityCatalog,
        context: AgentRuntimeContext,
        request: CapabilitySearchRequest,
        local_tool_names: frozenset[str] = frozenset(),
    ) -> TwoTierCapabilitySearchResult:
        """Return one bounded ranked answer over catalog plus expanded records."""

        first_tier = self._ranker.rank_entries(catalog.entries, request)
        expansion = await self._expander.expand(
            catalog=catalog,
            context=context,
            request=request,
            local_tool_names=local_tool_names,
        )
        second_tier = self._ranker.rank_entries(
            (capability.entry for capability in expansion.capabilities),
            request,
        )
        merged = self._ranker.merge((first_tier, second_tier), limit=request.limit)
        return TwoTierCapabilitySearchResult(
            search=CapabilitySearchResult(
                catalog_id=catalog.revision.catalog_id,
                catalog_revision=catalog.revision.revision,
                query_digest=self._ranker.query_digest(request.query),
                scanned_count=merged.scanned_count,
                candidates=merged.candidates,
            ),
            expansion=expansion,
        )


__all__ = (
    # Owned here: the executable second tier.
    "BoundedCapabilityExpander",
    "ExpandedCapabilityProjector",
    "TwoTierCapabilitySearch",
    # Re-exported so existing ``from ...expansion import X`` call sites keep
    # resolving after the contracts moved to ``contracts`` and the
    # configuration-resolved bounds moved to ``activation``.
    "CapabilityExpansionError",
    "CapabilityExpansionLimits",
    "CapabilityExpansionOutcome",
    "CapabilityExpansionResult",
    "CapabilityExpansionState",
    "CapabilityReferenceMinter",
    "ExpandedCapability",
    "HmacCapabilityReferenceMinter",
    "TwoTierCapabilitySearchResult",
)
