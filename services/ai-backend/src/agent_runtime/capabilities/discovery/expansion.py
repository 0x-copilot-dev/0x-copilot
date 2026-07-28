"""Bounded, deadline-governed second-tier expansion of authorized server cards.

Tier one is the compact catalog: authorized tool cards plus authorized MCP
*server* cards, none of which cost a connection.  Tier two is this module.
When tier one cannot answer a query confidently, at most ``K`` ranked server
cards are expanded through the existing :class:`McpLoader` — and therefore
through the F8 revision-aware discovery cache — and their descriptors are
projected into schema-free records the same ranker can score.

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
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, Self, runtime_checkable

from pydantic import Field, NonNegativeInt, PositiveFloat, PositiveInt, model_validator

from agent_runtime.capabilities.discovery.contracts import (
    ApprovalCue,
    CapabilityCatalog,
    CapabilityIndexEntry,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySearchResult,
    CapabilitySource,
    CatalogEffectClass,
)
from agent_runtime.capabilities.discovery.ranker import (
    DeterministicLexicalRanker,
    RankedCapabilitySelection,
)
from agent_runtime.capabilities.mcp.cards import (
    JsonSchema,
    McpLoadRequest,
    McpLoadResult,
    McpRiskLevel,
    McpToolDescriptor,
)
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract

_CAPABILITY_REF_PATTERN = r"^cap_[0-9a-f]{32}$"
_MAX_SERVER_CEILING = 8
_MAX_EXPANDED_CAPABILITIES = 2_048

_LOGGER = logging.getLogger(__name__)


class CapabilityExpansionError(ValueError):
    """Typed, model-safe failure of a bounded capability expansion."""


class CapabilityExpansionState(StrEnum):
    """Closed per-server expansion outcomes; only one of them admits records."""

    EXPANDED = "expanded"
    UNAVAILABLE = "unavailable"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class CapabilityExpansionLimits(RuntimeContract):
    """Configuration-driven bounds for one bounded discovery expansion.

    Every bound is conservative by default and clamps by construction.
    ``max_servers`` is the ``K`` of the ``O(NQ + R log K)`` budget and of the
    "cold discovery opens at most K servers" exit criterion; the first release
    ceiling from the F3 PRD is ``K <= 3``.
    """

    max_servers: PositiveInt = Field(default=3, le=_MAX_SERVER_CEILING)
    total_deadline_seconds: PositiveFloat = Field(default=8.0, le=120.0)
    max_capabilities_per_server: PositiveInt = Field(default=64, le=256)
    expansion_trigger_candidates: PositiveInt = Field(default=3, le=10)

    class Env:
        """Environment keys that may narrow or widen the bounded defaults."""

        MAX_SERVERS: ClassVar[str] = "F3_DISCOVERY_MAX_EXPANDED_SERVERS"
        TOTAL_DEADLINE_SECONDS: ClassVar[str] = "F3_DISCOVERY_TOTAL_DEADLINE_SECONDS"
        MAX_CAPABILITIES_PER_SERVER: ClassVar[str] = (
            "F3_DISCOVERY_MAX_CAPABILITIES_PER_SERVER"
        )
        EXPANSION_TRIGGER_CANDIDATES: ClassVar[str] = (
            "F3_DISCOVERY_EXPANSION_TRIGGER_CANDIDATES"
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        """Read the bounds from configuration, defaulting on anything invalid.

        A missing, blank, non-numeric, or out-of-range value resolves to the
        conservative default rather than to the ceiling, so a typo can never
        raise the fan-out or the deadline. ``environ`` is injectable so tests
        assert both branches without mutating process state.
        """

        source = environ if environ is not None else os.environ
        defaults = cls()
        return cls(
            max_servers=cls._read_int(
                source,
                cls.Env.MAX_SERVERS,
                default=defaults.max_servers,
                minimum=1,
                maximum=_MAX_SERVER_CEILING,
            ),
            total_deadline_seconds=cls._read_float(
                source,
                cls.Env.TOTAL_DEADLINE_SECONDS,
                default=defaults.total_deadline_seconds,
                maximum=120.0,
            ),
            max_capabilities_per_server=cls._read_int(
                source,
                cls.Env.MAX_CAPABILITIES_PER_SERVER,
                default=defaults.max_capabilities_per_server,
                minimum=1,
                maximum=256,
            ),
            expansion_trigger_candidates=cls._read_int(
                source,
                cls.Env.EXPANSION_TRIGGER_CANDIDATES,
                default=defaults.expansion_trigger_candidates,
                minimum=1,
                maximum=10,
            ),
        )

    @staticmethod
    def _read_int(
        source: Mapping[str, str],
        key: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        raw = source.get(key, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        if value < minimum or value > maximum:
            return default
        return value

    @staticmethod
    def _read_float(
        source: Mapping[str, str],
        key: str,
        *,
        default: float,
        maximum: float,
    ) -> float:
        raw = source.get(key, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        if value <= 0 or value > maximum:
            return default
        return value


@runtime_checkable
class CapabilityReferenceMinter(Protocol):
    """Mint the opaque reference for one expanded capability identity."""

    def mint(self, *, catalog_id: str, identity: str) -> str: ...


class HmacCapabilityReferenceMinter:
    """Derive unguessable, run-stable refs from a secret reference key.

    The derivation intentionally mirrors the catalog builder's so an expanded
    capability is indistinguishable from a catalog member to the model. Supply
    the same ``reference_key`` the builder was constructed with; identities are
    namespaced so an expanded capability can never collide with a server card.
    """

    _MIN_KEY_BYTES: ClassVar[int] = 32

    class Messages:
        """Safe public messages for reference-minter construction."""

        WEAK_KEY = "reference_key must contain at least 32 bytes"

    def __init__(self, *, reference_key: bytes) -> None:
        if len(reference_key) < self._MIN_KEY_BYTES:
            raise CapabilityExpansionError(self.Messages.WEAK_KEY)
        self._reference_key = bytes(reference_key)

    def mint(self, *, catalog_id: str, identity: str) -> str:
        """Return an opaque ``cap_`` reference for one capability identity."""

        digest = hmac.new(
            self._reference_key,
            f"{catalog_id}:{identity}".encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"cap_{digest}"


class ExpandedCapability(RuntimeContract):
    """One schema-free capability projected from a successfully loaded server.

    ``owner_capability_ref`` is the catalog ref of the *server card* this
    capability came from. It is what makes the narrowing invariant checkable:
    a capability is admissible only while its owner is recorded as expanded.
    """

    owner_capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    server_name: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    entry: CapabilityIndexEntry


class CapabilityExpansionOutcome(RuntimeContract):
    """Per-server disclosure of what expansion did, without leaking why."""

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    state: CapabilityExpansionState
    admitted_count: NonNegativeInt = 0

    class Messages:
        """Safe public messages for outcome invariants."""

        UNEXPANDED_ADMISSION = "only an expanded server may admit capabilities"

    @model_validator(mode="after")
    def _only_expanded_servers_admit(self) -> Self:
        if self.admitted_count and self.state is not CapabilityExpansionState.EXPANDED:
            raise CapabilityExpansionError(self.Messages.UNEXPANDED_ADMISSION)
        return self


class CapabilityExpansionResult(RuntimeContract):
    """Bounded result of one expansion; widening is structurally unrepresentable.

    The validators are the enforcement point for the lane's central property.
    A result can never claim more admitted servers than the configured ``K``,
    can never carry a capability whose owner did not reach
    :attr:`CapabilityExpansionState.EXPANDED`, and can never carry more
    capabilities than the expanded servers actually admitted.
    """

    max_servers: PositiveInt = Field(le=_MAX_SERVER_CEILING)
    considered_count: NonNegativeInt = 0
    admitted_count: NonNegativeInt = 0
    deadline_exceeded: bool = False
    outcomes: tuple[CapabilityExpansionOutcome, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_SERVER_CEILING,
    )
    capabilities: tuple[ExpandedCapability, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_EXPANDED_CAPABILITIES,
    )

    class Messages:
        """Safe public messages for expansion-result invariants."""

        OVER_BUDGET = "expansion admitted more servers than the configured bound"
        OVER_CONSIDERED = "expansion admitted more servers than it considered"
        OUTCOME_COUNT = "expansion must record one outcome per admitted server"
        DUPLICATE_OUTCOME = "expansion cannot record two outcomes for one server"
        UNOWNED_CAPABILITY = (
            "an expanded capability must belong to a server that expanded"
        )
        ADMISSION_MISMATCH = (
            "expanded capability count must equal the admitted server totals"
        )

    @model_validator(mode="after")
    def _partial_failure_only_narrows(self) -> Self:
        if self.admitted_count > self.max_servers:
            raise CapabilityExpansionError(self.Messages.OVER_BUDGET)
        if self.admitted_count > self.considered_count:
            raise CapabilityExpansionError(self.Messages.OVER_CONSIDERED)
        if len(self.outcomes) != self.admitted_count:
            raise CapabilityExpansionError(self.Messages.OUTCOME_COUNT)
        refs = [outcome.capability_ref for outcome in self.outcomes]
        if len(refs) != len(set(refs)):
            raise CapabilityExpansionError(self.Messages.DUPLICATE_OUTCOME)
        expanded_refs = {
            outcome.capability_ref
            for outcome in self.outcomes
            if outcome.state is CapabilityExpansionState.EXPANDED
        }
        if any(
            capability.owner_capability_ref not in expanded_refs
            for capability in self.capabilities
        ):
            raise CapabilityExpansionError(self.Messages.UNOWNED_CAPABILITY)
        admitted_total = sum(outcome.admitted_count for outcome in self.outcomes)
        if admitted_total != len(self.capabilities):
            raise CapabilityExpansionError(self.Messages.ADMISSION_MISMATCH)
        return self

    @property
    def expanded_count(self) -> int:
        """Return how many admitted servers actually produced capabilities."""

        return sum(
            1
            for outcome in self.outcomes
            if outcome.state is CapabilityExpansionState.EXPANDED
        )

    @classmethod
    def empty(cls, *, max_servers: int) -> Self:
        """Return the result of an expansion that admitted nothing."""

        return cls(max_servers=max_servers)


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


class TwoTierCapabilitySearchResult(RuntimeContract):
    """A bounded search answer plus the audit of what tier two actually did."""

    search: CapabilitySearchResult
    expansion: CapabilityExpansionResult


class TwoTierCapabilitySearch:
    """Compose compact-card search with bounded server expansion.

    Tier one always runs and is always sufficient on its own. Tier two runs
    only when tier one produced fewer confident capability-tier candidates than
    the configured trigger, so a query the catalog already answers costs no
    connection at all.
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
        expansion = await self._expand_if_needed(
            catalog=catalog,
            context=context,
            request=request,
            local_tool_names=local_tool_names,
            first_tier=first_tier,
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

    async def _expand_if_needed(
        self,
        *,
        catalog: CapabilityCatalog,
        context: AgentRuntimeContext,
        request: CapabilitySearchRequest,
        local_tool_names: frozenset[str],
        first_tier: RankedCapabilitySelection,
    ) -> CapabilityExpansionResult:
        limits = self._expander.limits
        if (
            self._capability_tier_hits(first_tier)
            >= limits.expansion_trigger_candidates
        ):
            return CapabilityExpansionResult.empty(max_servers=limits.max_servers)
        return await self._expander.expand(
            catalog=catalog,
            context=context,
            request=request,
            local_tool_names=local_tool_names,
        )

    @staticmethod
    def _capability_tier_hits(selection: RankedCapabilitySelection) -> int:
        """Count candidates that are already capabilities, not server cards."""

        return sum(
            1
            for candidate in selection.candidates
            if candidate.source is CapabilitySource.TOOL_CARD
        )


__all__ = (
    "BoundedCapabilityExpander",
    "CapabilityExpansionError",
    "CapabilityExpansionLimits",
    "CapabilityExpansionOutcome",
    "CapabilityExpansionResult",
    "CapabilityExpansionState",
    "CapabilityReferenceMinter",
    "ExpandedCapability",
    "ExpandedCapabilityProjector",
    "HmacCapabilityReferenceMinter",
    "TwoTierCapabilitySearch",
    "TwoTierCapabilitySearchResult",
)
