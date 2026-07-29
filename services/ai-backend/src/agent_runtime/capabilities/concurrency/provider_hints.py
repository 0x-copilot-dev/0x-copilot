"""The production source for F6.1's ``TRUSTED_PROVIDER`` precedence tier.

F6.1 built a four-source precedence chain — product catalog, user-approved
tightening, trusted provider tightening, conservative floor — and only
``PRODUCT_CATALOG`` ever had anything feeding it. Two thirds of the chain was
unreachable: a tier nothing can populate is a tier that has never been
exercised, and F6.1's own strongest claim (that a less authoritative source can
only narrow) had no production path to hold on.

This module supplies the tier the MCP protocol already answers: a server's own
``annotations`` block — ``readOnlyHint`` / ``destructiveHint`` /
``idempotentHint`` — which
:class:`~agent_runtime.capabilities.mcp.annotations.McpToolAnnotationsRegistry`
already captures per run at descriptor build. That is exactly what
``TRUSTED_PROVIDER`` was defined for: metadata the provider declared about
itself, crossing an untrusted boundary.

**F3.3's rule is the whole design.** A server's self-declared hints are honoured
only when they *narrow*. This is not enforced by reviewing the mapping below; it
is enforced by the only composition operator F6 exposes.
:meth:`CapabilityConcurrencyDeclaration.narrow` folds every field with
``narrowest``, a minimum over declaration-order authority rank, so a hint that
would make a capability look safer cannot change the outcome whatever it says.
The attempt is recorded as a typed
:class:`~agent_runtime.capabilities.concurrency.descriptor_policy.ConcurrencyPolicyRejection`
and the narrower value survives. The mapping therefore deliberately emits the
*safety-asserting* hints too — ``readOnlyHint: true`` becomes a ``read`` side
effect, ``idempotentHint: true`` becomes ``natural`` idempotency — because a
mapping that pre-filtered them would make the no-widening property a property of
this file rather than of the fold, and would leave it untested against a real
over-claim.

Two further limits are deliberate.

**A provider can never make a capability plannable.** F6 plans only capabilities
an operator declared, and :class:`ProviderNarrowedPolicySource` returns ``None``
for anything the catalog is silent about — before it ever looks at a hint. A
server cannot opt itself into parallel admission by describing itself.

**Silence is not a claim.** An absent ``annotations`` block, a non-boolean hint
value (``McpToolAnnotations`` already coerces those to ``None``), or a tool the
registry never saw all yield no declaration at all, so the catalog's own policy
survives untouched. That is distinct from a hint that *does* narrow.

This module is deliberately **not** re-exported from the package ``__init__``,
for the reason stated there about
:mod:`agent_runtime.capabilities.concurrency.graph_admission`: the package is
imported by every deployment, and the graph seam — which this module joins — is
what a deployment without F6 configured must not pay for.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from agent_runtime.capabilities.concurrency.contracts import (
    ConcurrencyMode,
    ConcurrencyPolicyField,
    IdempotencyKind,
    NarrowableEnum,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.concurrency.descriptor_policy import (
    CapabilityConcurrencyDeclaration,
    ConcurrencyDescriptorParser,
    ConcurrencyPolicyResolution,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    GraphConcurrencyPolicySource,
    graph_capability_key,
)
from agent_runtime.execution.contracts import RuntimeContract


class ProviderConcurrencyHints(RuntimeContract):
    """One MCP server's self-declared concurrency-relevant hints.

    A transport-free mirror of the three annotations the runtime already models,
    so this module's mapping can be tested without an MCP server and without the
    per-run registry. ``None`` means the server said nothing, which is not the
    same as saying "no".
    """

    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None

    @property
    def is_silent(self) -> bool:
        """Return whether the server declared nothing this domain can read."""

        return (self.read_only, self.destructive, self.idempotent) == (
            None,
            None,
            None,
        )

    def declaration_payload(self) -> dict[str, str]:
        """Return the wire payload these hints claim, folded by ``narrowest``.

        Hints can imply the same field twice — a server declaring both
        ``readOnlyHint`` and ``destructiveHint`` true has contradicted itself —
        so implications are folded with the domain's one composition operator
        rather than by last-write-wins. The narrower reading of a contradiction
        is the only safe one.
        """

        implied: dict[ConcurrencyPolicyField, list[NarrowableEnum]] = {}

        def imply(policy_field: ConcurrencyPolicyField, value: NarrowableEnum) -> None:
            implied.setdefault(policy_field, []).append(value)

        if self.read_only is True:
            imply(ConcurrencyPolicyField.SIDE_EFFECT, SideEffectKind.READ)
        if self.read_only is False:
            # "Not read-only" establishes that it writes and nothing about
            # whether the write can be undone, so it lands on the narrowest
            # write class rather than assuming the recoverable one.
            imply(
                ConcurrencyPolicyField.SIDE_EFFECT,
                SideEffectKind.IRREVERSIBLE_WRITE,
            )
        if self.destructive is True:
            imply(
                ConcurrencyPolicyField.SIDE_EFFECT,
                SideEffectKind.IRREVERSIBLE_WRITE,
            )
            imply(ConcurrencyPolicyField.MODE, ConcurrencyMode.SERIAL)
        # ``destructiveHint: false`` is a pure safety claim — "my updates are
        # additive" — and implies nothing. Mapping it to the recoverable write
        # class would be a widening the fold would discard anyway; leaving it
        # unmapped says the same thing without pretending a claim was made.
        if self.idempotent is True:
            imply(ConcurrencyPolicyField.IDEMPOTENCY, IdempotencyKind.NATURAL)
        if self.idempotent is False:
            imply(ConcurrencyPolicyField.IDEMPOTENCY, IdempotencyKind.NONE)

        return {
            policy_field.value: type(values[0]).narrowest(*values).value
            for policy_field, values in implied.items()
        }

    def declaration(
        self,
        *,
        capability_ref: str,
        parser: ConcurrencyDescriptorParser | None = None,
    ) -> CapabilityConcurrencyDeclaration | None:
        """Return the ``TRUSTED_PROVIDER`` declaration these hints support.

        Routed through F6.1's own parser rather than constructing the record
        directly, so provider metadata has exactly one entry point into the
        domain and an unparseable value lands on its vocabulary's conservative
        floor here for the same reason it does everywhere else.
        """

        payload = self.declaration_payload()
        if not payload:
            return None
        resolved = parser if parser is not None else ConcurrencyDescriptorParser()
        return resolved.parse(
            capability_ref=capability_ref,
            source=PolicySource.TRUSTED_PROVIDER,
            payload=payload,
        )


@runtime_checkable
class ProviderConcurrencyHintSource(Protocol):
    """Answer what one capability's provider declared about itself."""

    def hints_for(self, *, capability_key: str) -> ProviderConcurrencyHints | None:
        """Return the provider's hints, or ``None`` when it declared none."""


class McpAnnotationProviderHints:
    """Read this run's live MCP tool annotations as provider hints.

    Ambient by construction, and deliberately so: the annotations registry is
    the per-run ``ContextVar`` the MCP loader already fills at descriptor build
    and that :mod:`agent_runtime.surfaces_v2` and
    :mod:`agent_runtime.capabilities.operations` already read the same way. A
    snapshot taken when F6 is composed would be empty, because a run's
    descriptors are built after that; reading through the registry means the
    tier sees whatever the run has actually discovered by the time a turn is
    planned.

    An unbound registry, a tool the run never loaded, and a non-MCP capability
    key all answer ``None`` — the catalog's own policy, unchanged.
    """

    __slots__ = ()

    def hints_for(self, *, capability_key: str) -> ProviderConcurrencyHints | None:
        """Return the live hints for ``server:tool``, or ``None``."""

        from agent_runtime.capabilities.mcp.annotations import (  # noqa: PLC0415
            McpToolAnnotationsRegistry,
        )

        server, separator, tool = capability_key.rpartition(":")
        if not separator or not server or not tool:
            # Not an MCP call: ``graph_capability_key`` only joins on a colon
            # when both a server and a tool were present. A graph tool's own
            # name has no provider to declare anything about it.
            return None
        annotations = McpToolAnnotationsRegistry.get(server, tool)
        if annotations is None:
            return None
        return ProviderConcurrencyHints(
            read_only=annotations.read_only_hint,
            destructive=annotations.destructive_hint,
            idempotent=annotations.idempotent_hint,
        )


class ProviderNarrowedPolicySource:
    """Narrow an operator-declared policy by what the provider declared.

    A decorator over any :class:`GraphConcurrencyPolicySource`, so the catalog
    half keeps its one implementation and this adds exactly one tier. The
    composition is
    :meth:`CapabilityConcurrencyDeclaration.narrow` — F6.1's own fold, the same
    one :class:`~agent_runtime.capabilities.concurrency.descriptor_policy.ConcurrencyPolicyResolver`
    applies to every non-base source — which is what makes "a provider may only
    narrow" a structural property here rather than a reviewed one.

    Nothing this class does can produce a resolution the inner source did not
    already produce: an undeclared capability stays undeclared, and a hint that
    changes no field leaves the resolution byte-identical, digest included.
    """

    __slots__ = ("_hints", "_inner", "_parser")

    def __init__(
        self,
        inner: GraphConcurrencyPolicySource,
        *,
        hints: ProviderConcurrencyHintSource,
        parser: ConcurrencyDescriptorParser | None = None,
    ) -> None:
        self._inner = inner
        self._hints = hints
        self._parser = parser if parser is not None else ConcurrencyDescriptorParser()

    def resolution_for(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ConcurrencyPolicyResolution | None:
        """Return the catalog's resolution, narrowed by the provider's hints."""

        established = self._inner.resolution_for(
            tool_name=tool_name,
            arguments=arguments,
        )
        if established is None:
            # A provider cannot make a capability plannable. F6 plans what an
            # operator declared; silence stays silence.
            return None
        declaration = self._declaration_for(
            capability_key=graph_capability_key(
                tool_name=tool_name,
                arguments=arguments,
            ),
            capability_ref=established.capability_ref,
        )
        if declaration is None:
            return established
        return self._narrowed(established, declaration)

    def _declaration_for(
        self,
        *,
        capability_key: str,
        capability_ref: str,
    ) -> CapabilityConcurrencyDeclaration | None:
        try:
            hints = self._hints.hints_for(capability_key=capability_key)
        except Exception:  # noqa: BLE001 - a hint source that fails declares nothing.
            return None
        if not isinstance(hints, ProviderConcurrencyHints) or hints.is_silent:
            return None
        return hints.declaration(capability_ref=capability_ref, parser=self._parser)

    @staticmethod
    def _narrowed(
        established: ConcurrencyPolicyResolution,
        declaration: CapabilityConcurrencyDeclaration,
    ) -> ConcurrencyPolicyResolution:
        narrowing = declaration.narrow(established.policy)
        policy = narrowing.policy
        contributing = established.contributing_sources
        if narrowing.changed_fields:
            contributing = (*contributing, declaration.source)
            policy = policy.model_copy(
                update={
                    "policy_source": PolicySource.narrowest(
                        policy.policy_source,
                        declaration.source,
                    )
                }
            )
        return ConcurrencyPolicyResolution.of(
            capability_ref=established.capability_ref,
            policy=policy,
            # The provider tier declares no approval requirement: whether a
            # dispatch parks is decided by the run, never by the server being
            # dispatched to. The established fact carries through untouched.
            approval_requirement=established.approval_requirement,
            considered_sources=(
                *established.considered_sources,
                declaration.source,
            ),
            contributing_sources=tuple(dict.fromkeys(contributing)),
            rejections=(
                *established.rejections,
                *declaration.rejections,
                *narrowing.rejections,
            ),
        )


__all__ = (
    "McpAnnotationProviderHints",
    "ProviderConcurrencyHintSource",
    "ProviderConcurrencyHints",
    "ProviderNarrowedPolicySource",
)
