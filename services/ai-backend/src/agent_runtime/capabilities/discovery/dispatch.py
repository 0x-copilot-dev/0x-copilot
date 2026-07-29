"""What one run disclosed, and the only coordinates it may be dispatched to.

An opaque capability ref is meaningless to a connector.  This module owns the
run-scoped translation back to the ``(server, tool)`` pair the ordinary MCP
dispatcher understands, and the ledger of what a run's own second-tier
expansion actually disclosed to the model.

It exists as its own module for one structural reason.  Registration must be
able to hand the same run-scoped object to the search adapter (which *writes*
what it disclosed) and to the executor (which *reads* what may be dispatched),
and :mod:`agent_runtime.capabilities.discovery.executor` imports the concrete
MCP dispatcher — so a registration path that reached the binding types through
the executor would drag the whole dispatch route into the module that is only
allowed to decide *which tools register*.  Splitting the vocabulary out keeps
both halves honest: this module knows what a dispatch coordinate is and never
performs one, and the executor performs one and owns no vocabulary.

Two scoping rules are load-bearing and enforced here rather than by convention:

* **A disclosed capability must descend from a member of this run's catalog.**
  ``record`` refuses any capability whose owning server card is not in the
  catalog the ledger was built for, so a reference minted under another run's
  catalog — which is a different ``catalog_id``, and therefore a different
  keyed derivation — can never enter the table.
* **Disclosure never widens the dispatch surface.**  A ref may be re-disclosed
  when the model searches twice, and its schema identity may legitimately move
  with it, but its dispatch coordinates may not: those are a function of the
  identity the reference was minted from, so a coordinate change is ambiguity
  rather than a refresh, and is refused.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar, Protocol, Self, runtime_checkable

from pydantic import Field, field_validator

from agent_runtime.capabilities.discovery.contracts import (
    CapabilityBridgeRecursionError,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogIdentityError,
    CapabilityIndexEntry,
    CapabilityInputSchemaIdentity,
    CapabilityRefBinding,
    ExpandedCapability,
)
from agent_runtime.capabilities.mcp.cards import JsonSchema, McpToolDescriptor
from agent_runtime.execution.contracts import RuntimeContract

_CAPABILITY_REF_PATTERN = r"^cap_[0-9a-f]{32}$"
_SHA256_HEX_PATTERN = r"^[a-f0-9]{64}$"


class CapabilityDispatchBinding(RuntimeContract):
    """Non-model dispatch coordinates plus the schema identity describe disclosed.

    An opaque capability ref is meaningless to a connector.  This binding is the
    run-scoped translation back to the ``(server, tool)`` pair the ordinary MCP
    dispatcher understands, and it is the *only* way the executor can name a
    dispatch target — so a capability with no recorded binding is undispatchable
    rather than guessable.

    ``schema_digest`` is the digest of the input schema the capability was
    projected from at disclosure time.  Recording it here is what turns "the
    schema changed between describe and invoke" into a deterministic equality
    failure instead of an inference from whether stale arguments still validate.

    This is also the bridge-recursion guard's fourth structural chokepoint.  The
    first three (catalog membership, invocation target, dispatch-by-type) are
    F3.2's.  This one covers the surface F3.5 introduced: a *server supplied*
    tool name entering a dispatch coordinate.  Like the others, the reserved set
    is derived by iterating the closed tool-name enum, so a fourth bridge tool is
    covered without touching this file.
    """

    capability_ref: str = Field(pattern=_CAPABILITY_REF_PATTERN)
    server_name: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)
    schema_digest: str = Field(pattern=_SHA256_HEX_PATTERN)

    class Messages:
        """Safe public messages for dispatch-binding validation."""

        RESERVED_DISPATCH_NAME: ClassVar[str] = (
            "a bridge tool name can never be a dispatch coordinate"
        )
        BLANK_NAME: ClassVar[str] = "dispatch coordinates must be non-empty"

    @field_validator("server_name", "tool_name")
    @classmethod
    def _never_dispatches_to_a_bridge_tool(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(cls.Messages.BLANK_NAME)
        if CapabilityBridgeToolName.is_reserved(normalized):
            raise CapabilityBridgeRecursionError(cls.Messages.RESERVED_DISPATCH_NAME)
        return normalized

    @staticmethod
    def schema_digest_for(input_schema: JsonSchema) -> str:
        """Return the reproducible identity of one capability input schema."""

        return CapabilityInputSchemaIdentity.digest(input_schema)

    @classmethod
    def for_tool(
        cls,
        *,
        capability_ref: str,
        server_name: str,
        tool: McpToolDescriptor,
    ) -> Self:
        """Bind one disclosed capability to the descriptor it was projected from."""

        return cls(
            capability_ref=capability_ref,
            server_name=server_name,
            tool_name=tool.name,
            schema_digest=cls.schema_digest_for(tool.input_schema),
        )

    @classmethod
    def for_expanded(cls, capability: ExpandedCapability) -> Self:
        """Bind one tier-two record from the expansion result alone.

        This is the M-12 close: :class:`ExpandedCapability` now carries the
        schema identity it was projected from, so the expansion output is
        self-sufficient and a call site no longer has to keep a parallel stream
        of untrusted descriptors aligned with it by hand.
        """

        return cls(
            capability_ref=capability.entry.capability_ref,
            server_name=capability.server_name,
            tool_name=capability.tool_name,
            schema_digest=capability.schema_digest,
        )


@runtime_checkable
class CapabilityDispatchBindingPort(Protocol):
    """Resolve one opaque capability ref to its run-scoped dispatch coordinates.

    Implementations answer from what discovery actually disclosed for this run.
    They never derive coordinates from a model-supplied value and never widen:
    an unknown ref resolves to ``None``, which the executor treats as
    undispatchable.
    """

    def binding_for(self, capability_ref: str) -> CapabilityDispatchBinding | None: ...


class RunScopedCapabilityDispatchBindings:
    """Immutable per-run binding table keyed by opaque capability ref.

    Built once from what discovery disclosed and never mutated afterwards, so a
    later turn cannot introduce a dispatch coordinate the model was not shown.
    Duplicate refs are refused rather than last-write-wins: two bindings for one
    ref would make the dispatch target ambiguous.

    Use this when the full disclosure is already known at construction time.  A
    run whose disclosure accumulates as the model searches uses
    :class:`RunScopedCapabilityDisclosure` instead, which enforces the same
    non-ambiguity rule over a table that is allowed to grow.
    """

    class Messages:
        """Safe public messages for binding-table construction."""

        DUPLICATE_REF: ClassVar[str] = (
            "a capability ref may have only one dispatch binding"
        )

    __slots__ = ("_by_ref",)

    def __init__(self, bindings: Iterable[CapabilityDispatchBinding] = ()) -> None:
        by_ref: dict[str, CapabilityDispatchBinding] = {}
        for binding in bindings:
            if binding.capability_ref in by_ref:
                raise ValueError(self.Messages.DUPLICATE_REF)
            by_ref[binding.capability_ref] = binding
        self._by_ref = by_ref

    def binding_for(self, capability_ref: str) -> CapabilityDispatchBinding | None:
        """Return the recorded coordinates for ``capability_ref``, or ``None``."""

        return self._by_ref.get(capability_ref)

    @classmethod
    def from_disclosed(
        cls,
        disclosed: Iterable[tuple[str, str, McpToolDescriptor]],
    ) -> Self:
        """Build the table from ``(capability_ref, server_name, descriptor)`` rows.

        This is the shape a caller holds when it has the untrusted descriptors
        themselves.  Taking the descriptor rather than a precomputed digest keeps
        the one schema-identity derivation in this module.
        """

        return cls(
            CapabilityDispatchBinding.for_tool(
                capability_ref=capability_ref,
                server_name=server_name,
                tool=tool,
            )
            for capability_ref, server_name, tool in disclosed
        )

    @classmethod
    def from_expansion(cls, capabilities: Iterable[ExpandedCapability]) -> Self:
        """Build the table straight from tier-two expansion output."""

        return cls(
            CapabilityDispatchBinding.for_expanded(capability)
            for capability in capabilities
        )


class RunScopedCapabilityDisclosure:
    """The one run-scoped record of what tier two disclosed, and to whom.

    A catalog holds MCP *server* cards, so nothing in it is something the model
    can invoke; every capability-granularity answer comes from second-tier
    expansion, whose records are deliberately **not** catalog members.  That
    leaves a gap the bridge cannot cross on its own: a ref the model was just
    shown has no catalog entry to describe and no coordinates to dispatch to.
    This ledger is that gap closed, and it is the single object both halves
    share — the search adapter writes what it disclosed, the describe and invoke
    adapters resolve against it, and the executor reads its bindings.

    It is scoped by construction rather than by check.  The catalog it is built
    for names an exact run, org, and user, and a capability may only be recorded
    when its owning server card is a member of that catalog.  Because an opaque
    ref is a keyed derivation over the catalog id — itself derived from the whole
    scope identity — a ref minted for another run or another subject is simply
    absent from this table, and an absent ref is undispatchable.
    """

    class Messages:
        """Safe public messages for disclosure-ledger invariants."""

        UNOWNED_CAPABILITY: ClassVar[str] = (
            "a disclosed capability must descend from a member of this catalog"
        )
        AMBIGUOUS_COORDINATES: ClassVar[str] = (
            "a capability ref may never change its dispatch coordinates"
        )
        NOT_DISCLOSED: ClassVar[str] = "that capability was not disclosed to this run"

    __slots__ = ("_bindings", "_catalog", "_entries", "_member_refs")

    def __init__(self, *, catalog: CapabilityCatalog) -> None:
        self._catalog = catalog
        self._member_refs = frozenset(entry.capability_ref for entry in catalog.entries)
        self._entries: dict[str, CapabilityIndexEntry] = {}
        self._bindings: dict[str, CapabilityDispatchBinding] = {}

    @property
    def catalog(self) -> CapabilityCatalog:
        """Return the run-scoped catalog every disclosure must descend from."""

        return self._catalog

    def record(self, capabilities: Iterable[ExpandedCapability]) -> None:
        """Record what one expansion disclosed, refusing anything that widens."""

        for capability in capabilities:
            self._record_one(capability)

    def _record_one(self, capability: ExpandedCapability) -> None:
        if capability.owner_capability_ref not in self._member_refs:
            raise CapabilityCatalogIdentityError(self.Messages.UNOWNED_CAPABILITY)
        binding = CapabilityDispatchBinding.for_expanded(capability)
        recorded = self._bindings.get(binding.capability_ref)
        if recorded is not None and (
            recorded.server_name != binding.server_name
            or recorded.tool_name != binding.tool_name
        ):
            # Coordinates are a function of the identity the ref was minted
            # from, so they cannot legitimately move. Only the schema identity
            # may, and re-disclosure is what the model is acting on.
            raise CapabilityCatalogIdentityError(self.Messages.AMBIGUOUS_COORDINATES)
        self._bindings[binding.capability_ref] = binding
        self._entries[capability.entry.capability_ref] = capability.entry

    def binding_for(self, capability_ref: str) -> CapabilityDispatchBinding | None:
        """Return the recorded coordinates for ``capability_ref``, or ``None``."""

        return self._bindings.get(capability_ref)

    def entry_for(self, capability_ref: str) -> CapabilityIndexEntry | None:
        """Return the disclosed record for ``capability_ref``, or ``None``."""

        return self._entries.get(capability_ref)

    def bind_ref(self, capability_ref: str) -> CapabilityRefBinding:
        """Bind one disclosed ref to the generation its catalog was projected from.

        This is the disclosed-record twin of
        :meth:`~agent_runtime.capabilities.discovery.contracts.CapabilityCatalog.bind_ref`
        and narrows in exactly the same way: only a ref this ledger actually
        disclosed can be bound, and a catalog with no generation cannot bind at
        all.  The generation is the catalog's own, which is honest — expansion
        disclosed more of what the catalog's trusted inputs already authorized,
        it did not change those inputs — so a disclosed ref revalidates through
        the same shared primitive as a catalog member.
        """

        generation = self._catalog.generation
        if generation is None:
            raise CapabilityCatalogIdentityError(CapabilityCatalog.Messages.UNGENERATED)
        if capability_ref not in self._entries:
            raise CapabilityCatalogIdentityError(self.Messages.NOT_DISCLOSED)
        return CapabilityRefBinding.create(
            capability_ref=capability_ref,
            catalog_id=self._catalog.revision.catalog_id,
            catalog_revision=self._catalog.revision.revision,
            generation=generation,
        )


__all__ = (
    "CapabilityDispatchBinding",
    "CapabilityDispatchBindingPort",
    "RunScopedCapabilityDisclosure",
    "RunScopedCapabilityDispatchBindings",
)
