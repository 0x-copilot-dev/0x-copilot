"""Capability-agnostic tool-policy pipeline contracts (MCP migration P0).

These are the **inert** interface stubs for the generic tool pipeline described
in ``docs/plan/mcp-langchain-migration/PLAN.md`` (§1) and specified in
``docs/specs/mcp-tool-policy-pipeline.md``. Nothing here is wired into the
running app: no source is registered, no middleware runs, no policy is
consulted. P0 ships the vocabulary the later phases implement against —
:class:`CapabilityDescriptor`, the URN scheme, and the four Protocols
(:class:`ToolSource`, :class:`PolicyService`, :class:`CredentialProvider`,
:class:`ToolMiddleware`) plus the fixed :data:`MIDDLEWARE_ORDER`.

Design rules this module keeps (per ``services/ai-backend/CLAUDE.md``):

* Pydantic at the boundary — frozen, ``extra="forbid"`` — for every data
  contract. ``PolicyContract`` redeclares that config locally rather than
  importing ``RuntimeContract`` so this module stays a leaf and cannot close an
  import cycle with ``execution.contracts`` (the same reason
  ``execution.filesystem_bypass`` redeclares its own base).
* ``StrEnum`` / ``Literal`` for every known domain (:class:`Action`,
  :class:`Posture`, :class:`PolicyDecision`, :class:`Trust`,
  :class:`ConnectorState`, :class:`UrnScheme`, :class:`MiddlewareStage`,
  ``CapabilitySource``).
* No long-lived ``dict[str, Any]`` domain state; no module-level helper
  functions — the URN build/parse behaviour lives **inside**
  :class:`CapabilityUrn`.
* Typed domain error (:class:`CapabilityUrnError`) with a safe message.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, field_validator

from agent_runtime.capabilities.surfaces.builtin import server_slug, tool_slug

if TYPE_CHECKING:  # pragma: no cover - typing only; never imported at runtime.
    import httpx
    from langchain_core.tools import BaseTool


# --------------------------------------------------------------------------- #
# Domain vocabulary (StrEnum / Literal for every known set).
# --------------------------------------------------------------------------- #


class Action(StrEnum):
    """The effect class a capability declares — the approval axis (PLAN §1/§2).

    Derived by a source from MCP annotations (``readOnlyHint`` /
    ``destructiveHint``) or a builtin's own tag; the PDP reads it, the source
    never decides on it. ``DESTRUCTIVE`` is a distinct rung here: unlike today's
    ``actions.ActionClass`` (READ/WRITE only, which collapses destructive to
    WRITE at ``actions/classifier.py:60-61``), the migrated descriptor stops
    collapsing it so the §2 matrix can hard-gate destructive in Manual.

    ``EXECUTE`` is running a shell command, and it is a fourth axis rather than
    a reuse of ``DESTRUCTIVE`` for two reasons. One knob would answer two
    questions — ``destructive: block`` to stop connector deletions would also
    stop ``npm test``. And ``WRITE`` is disqualified outright: ``WRITE``
    auto-runs under ``BYPASS``, so classing a command as ``WRITE`` would mean
    the composer bypass pill silently turns on unattended shell execution. A
    command is exactly the "second way to touch the disk" that pill's own
    module says it never creates. No source emits this today; the only thing
    that reads it is the EXECUTE rung in
    ``policy/service.py::_posture_decision``.
    """

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXECUTE = "execute"


class Posture(StrEnum):
    """The run-scoped approval posture — the composer pill (PLAN §2).

    Value-identical to ``execution.filesystem_bypass.FilesystemBypassMode`` so
    the connector-write lane and the host-write lane speak one vocabulary:
    ``MANUAL`` = "Writes wait for you", ``BYPASS`` = "writes auto".
    """

    MANUAL = "manual"
    BYPASS = "bypass"


class Trust(StrEnum):
    """Whether the connector is trusted for affirmative read auto-run (PLAN §2).

    Per MCP 2026-07-28 ("untrusted unless from a trusted server"): catalog /
    first-party and OAuth-authenticated connectors are ``TRUSTED`` — their
    ``readOnlyHint:true`` may auto-run. An ``UNTRUSTED`` connector (or one
    advertising ``openWorldHint``) never earns silent auto-run on the strength
    of its own annotation alone.
    """

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class PolicyDecision(StrEnum):
    """The unified tri-state a :class:`PolicyService` returns (PLAN §1/§2).

    A direct generalization of today's ``tools/runtime_gate.ToolGateAction``
    (``ALLOW`` / ``REQUIRE_APPROVAL`` / ``REJECT``):

    * ``ALLOW`` — dispatch now (``AUTO``).
    * ``GATE``  — park the run on a LangGraph ``interrupt`` and resume on
      approve, in the *same* run (``ASK`` / ``REQUIRE``). This is the hang fix.
    * ``DENY``  — refuse (availability off / unauthorized / policy ``BLOCK``);
      never dispatch.
    """

    ALLOW = "allow"
    GATE = "gate"
    DENY = "deny"


class ConnectorState(StrEnum):
    """Availability of a capability's connector — NOT authorization (PLAN §1).

    Sourced from ``AgentRuntimeContext.{paused_connectors, connector_access_modes}``
    and ``McpServerCard.access_mode`` (``ConnectorAccessMode`` OFF ⇒ ``OFF``).
    ``OFF`` is a hard ``DENY`` at the PDP; ``PAUSED`` is the per-chat toggle;
    ``LIVE`` is reachable. Kept on the descriptor so the availability half of
    ``mcp/permissions.py`` moves out of the MCP layer (PLAN §4).
    """

    LIVE = "live"
    PAUSED = "paused"
    OFF = "off"


class UrnScheme(StrEnum):
    """The recognised capability-URN schemes (PLAN §1).

    Two forms are in scope for P0: ``mcp:{server}:{tool}`` and
    ``builtin:{ns}:{op}``. A skill-URN scheme is deliberately not defined yet
    (skills surface via other rungs today); ``CapabilitySource`` still admits
    ``"skill"`` on the descriptor while its URN form remains an open item.
    """

    MCP = "mcp"
    BUILTIN = "builtin"


class MiddlewareStage(StrEnum):
    """The six generic pipeline stages, named (PLAN §1).

    Ordering is expressed once by :data:`MIDDLEWARE_ORDER`; a concrete
    middleware advertises its stage so a composer can assert the stack is
    assembled in the fixed order.
    """

    POLICY = "policy"
    EXEC_POLICY = "exec_policy"
    OBSERVE = "observe"
    ERROR_MAP = "error_map"
    CITATIONS = "citations"
    PRESENT = "present"


#: The source that produced a tool. ``Literal`` (not ``StrEnum``) because the
#: PLAN's interface sketch types ``CapabilityDescriptor.source`` as a literal
#: union; the three sources are fixed (mcp / builtin / skill).
CapabilitySource: TypeAlias = Literal["mcp", "builtin", "skill"]


# --------------------------------------------------------------------------- #
# Data contracts.
# --------------------------------------------------------------------------- #


class PolicyContract(BaseModel):
    """Frozen, strictly-validated base for policy-pipeline data contracts.

    Redeclares ``RuntimeContract``'s config locally (``extra="forbid"``,
    ``frozen=True``, ``validate_assignment=True``) to keep this module a leaf —
    importing ``RuntimeContract`` from ``execution.contracts`` would couple the
    policy vocabulary to the runtime foundation and risk an import cycle once a
    later phase threads a descriptor through the run context. Same shape, same
    rationale as ``execution.filesystem_bypass._BypassContract``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class CapabilityDescriptor(PolicyContract):
    """What a source declares about one tool, for the pipeline to police.

    Every source (mcp / builtin / skill) emits ``(BaseTool, CapabilityDescriptor)``
    pairs (:class:`ToolSource`); the middleware stack reads the descriptor, the
    inner ``BaseTool`` keeps its own name/args-schema. ``scopes`` is metadata the
    PDP consumes — the source does not decide on it (PLAN §1).
    """

    #: Stable resource identity the policy is written against, e.g.
    #: ``"mcp:linear:create_issue"`` or ``"builtin:fs:write"``. Build/parse via
    #: :class:`CapabilityUrn`.
    urn: str
    #: The effect axis, from MCP annotations or the builtin's own tag.
    action: Action
    #: Whether the owning connector is trusted for affirmative read auto-run.
    #: Set by the source at ``load()`` time alongside ``action`` — a static
    #: per-connector fact (authenticated / first-party ⇒ TRUSTED), not a per-call
    #: input. It lives here, mirroring ``action``, so the PDP reads both off the
    #: descriptor rather than taking a loose ``decide()`` argument with no home.
    trust: Trust
    #: OAuth/permission scopes the capability requires — metadata for the PDP,
    #: never a decision the source makes. Per-server today (no per-tool scope).
    scopes: tuple[str, ...] = ()
    #: Which source produced the tool.
    source: CapabilitySource
    #: Availability of the owning connector (not authorization).
    connector_state: ConnectorState

    @field_validator("urn")
    @classmethod
    def _validate_urn(cls, value: str) -> str:
        """Reject a malformed URN at the boundary, via the shared parser.

        Keeps a descriptor from carrying an identity the policy layer cannot
        resolve (CLAUDE.md: constrained strings for known domains; Pydantic at
        the boundary). ``CapabilityUrn`` is defined below and resolved at
        validation time, never at class definition.
        """

        CapabilityUrn.parse(value)
        return value


class ParsedUrn(PolicyContract):
    """The three segments of a capability URN (:meth:`CapabilityUrn.parse`)."""

    scheme: UrnScheme
    #: ``server`` for ``mcp:``, ``ns`` for ``builtin:`` — the middle segment.
    namespace: str
    #: ``tool`` for ``mcp:``, ``op`` for ``builtin:`` — the trailing segment
    #: (may itself contain the delimiter; it is everything past the 2nd colon).
    name: str


# --------------------------------------------------------------------------- #
# Typed domain error.
# --------------------------------------------------------------------------- #


class CapabilityUrnError(ValueError):
    """Raised when a string is not a well-formed capability URN.

    Carries a safe, non-leaking message (the offending URN is identity/config,
    not a secret) so it can surface to callers without exposing internals.
    """


# --------------------------------------------------------------------------- #
# URN build/parse — behaviour lives inside the class (no module-level helpers).
# --------------------------------------------------------------------------- #


class CapabilityUrn:
    """Build and parse capability URNs — the identity a policy is written against.

    Two schemes (PLAN §1): ``mcp:{server}:{tool}`` and ``builtin:{ns}:{op}``.
    Segments are normalised through the **same** slug helpers the surface layer
    uses (:func:`agent_runtime.capabilities.surfaces.builtin.server_slug` /
    :func:`tool_slug`, ``surfaces/builtin.py:40-59``) so a URN segment resolves
    byte-identically to the surface URI's server/tool segments — one connector,
    one identity, everywhere. ``tool_slug`` lowercases without stripping the
    delimiter, so :meth:`parse` treats everything past the second colon as the
    trailing name.
    """

    _DELIMITER = ":"

    @classmethod
    def for_mcp(cls, server: str, tool: str) -> str:
        """Build ``mcp:{server_slug(server)}:{tool_slug(tool)}``."""

        return cls._DELIMITER.join(
            (UrnScheme.MCP.value, server_slug(server), tool_slug(tool))
        )

    @classmethod
    def for_builtin(cls, namespace: str, op: str) -> str:
        """Build ``builtin:{server_slug(namespace)}:{tool_slug(op)}``."""

        return cls._DELIMITER.join(
            (UrnScheme.BUILTIN.value, server_slug(namespace), tool_slug(op))
        )

    @classmethod
    def parse(cls, urn: str) -> ParsedUrn:
        """Split a URN into ``(scheme, namespace, name)``.

        Raises :class:`CapabilityUrnError` on an unrecognised scheme, a missing
        segment, or fewer than three segments. The trailing name may contain the
        delimiter and is kept whole.
        """

        parts = urn.split(cls._DELIMITER, 2)
        if len(parts) != 3 or not all(parts):
            raise CapabilityUrnError(f"not a valid capability URN: {urn!r}")
        scheme_raw, namespace, name = parts
        try:
            scheme = UrnScheme(scheme_raw)
        except ValueError as exc:
            raise CapabilityUrnError(
                f"unrecognised capability-URN scheme: {scheme_raw!r}"
            ) from exc
        return ParsedUrn(scheme=scheme, namespace=namespace, name=name)


# --------------------------------------------------------------------------- #
# The fixed middleware order (outermost first).
# --------------------------------------------------------------------------- #

#: The generic pipeline wraps EVERY tool in this exact order, outermost first:
#: Policy sees the call before anyone; Citations sits closest to the inner tool.
#: (PLAN §1: Policy → Exec-policy → Observe → Error-map → Citations → tool.)
MIDDLEWARE_ORDER: tuple[MiddlewareStage, ...] = (
    MiddlewareStage.POLICY,
    MiddlewareStage.EXEC_POLICY,
    MiddlewareStage.OBSERVE,
    MiddlewareStage.ERROR_MAP,
    MiddlewareStage.CITATIONS,
    # Innermost, and last for a reason: a result can only be put on the Work
    # Ledger once it has survived every stage that could still refuse, retry or
    # re-shape it. Presenting earlier would render a call the ERROR_MAP stage
    # was about to turn into a failure.
    MiddlewareStage.PRESENT,
)


# --------------------------------------------------------------------------- #
# Protocols (P0 stubs — bodies are '...'; no implementations here).
# --------------------------------------------------------------------------- #


class Principal(Protocol):
    """The identity surface the PDP reads (PLAN §2 / descriptor dossier).

    Structural interface — ``execution.contracts.AgentRuntimeContext`` is the
    production ``Principal`` (it carries exactly these fields), while a desktop
    or test impl can supply a trivial object. Declaring the surface here rather
    than importing ``AgentRuntimeContext`` keeps the seam substrate-agnostic:
    "desktop trivial impl; web/self-host RBAC impl; same seam."
    """

    user_id: str
    org_id: str
    roles: frozenset[str]
    permission_scopes: frozenset[str]
    #: Concrete ``dict`` (not ``Mapping``) so ``AgentRuntimeContext`` — whose
    #: field is ``dict[str, frozenset[str]]`` — structurally satisfies this
    #: Protocol; Protocol data members are invariant, so a widened ``Mapping``
    #: here would reject the production Principal on this field alone.
    connector_scopes: dict[str, frozenset[str]]


class ToolSource(Protocol):
    """Produces self-describing tools. mcp / builtin / skill behind one seam.

    Each ``load`` yields ``(BaseTool, CapabilityDescriptor)`` pairs; the composer
    wraps every pair with the same middleware stack (PLAN §1). The MCP source
    (P2) is ``langchain-mcp-adapters`` + a :class:`CredentialProvider`.
    """

    async def load(self) -> list[tuple[BaseTool, CapabilityDescriptor]]: ...


class PolicyService(Protocol):
    """Unifies permissions AND approval into one tri-state (PLAN §1/§2).

    ``decide`` folds the three questions today answered separately —
    availability (paused / access-mode OFF / disabled / unhealthy),
    authorization (allowlists / session + connector scopes), and approval
    posture (``descriptor.action`` × ``descriptor.trust`` × ``posture``) —
    into a single ``ALLOW | GATE | DENY``, evaluated DENY-first (an
    unreachable or unauthorized capability denies before any posture gate).
    The ``str`` is the safe reason / message (``permission_denied`` /
    ``connector_unavailable`` / a per-axis approval prompt).

    Org/user allowlists (``mcp/permissions.py`` authz) are read PDP-side from
    the connector registry / runtime context keyed by the descriptor's URN —
    deployment/tenant policy, not a self-description the source emits, so they
    are deliberately NOT descriptor fields (P1 impl detail).
    """

    def decide(
        self,
        *,
        principal: Principal,
        descriptor: CapabilityDescriptor,
        args: Mapping[str, object],
        posture: Posture,
    ) -> tuple[PolicyDecision, str]: ...


class CredentialProvider(Protocol):
    """Yields per-server auth for the MCP client (PLAN §3).

    ``langchain-mcp-adapters`` accepts ``httpx.Auth``, so the provider plugs in
    at client construction. Desktop = OS keychain (ai-backend connects
    directly); web / self-host = a scoped, short-lived token fetched from
    ``services/backend`` (tenant isolation preserved). One injected port, chosen
    per deployment — the old "move the token into ai-backend or not" fork
    dissolves into this seam.
    """

    async def auth_for(self, server_id: str) -> httpx.Auth | Mapping[str, str]: ...


class ToolMiddleware(Protocol):
    """One cross-cutting stage of the generic pipeline (PLAN §1).

    ``wrap`` returns a **schema-identical** ``BaseTool`` that runs this stage's
    concern then delegates inward, so a wrapped tool registers with the Deep
    Agent exactly like the tool it wraps. Services (the policy service, event
    producer, credential provider, …) are constructor-injected into the
    concrete middleware; the uniform seam is ``(tool, descriptor) -> tool``.
    ``stage`` lets a composer assert the stack matches :data:`MIDDLEWARE_ORDER`.
    """

    stage: MiddlewareStage

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool: ...


__all__ = [
    "Action",
    "CapabilityDescriptor",
    "CapabilitySource",
    "CapabilityUrn",
    "CapabilityUrnError",
    "ConnectorState",
    "CredentialProvider",
    "MIDDLEWARE_ORDER",
    "MiddlewareStage",
    "ParsedUrn",
    "PolicyContract",
    "PolicyDecision",
    "PolicyService",
    "Posture",
    "Principal",
    "ToolMiddleware",
    "ToolSource",
    "Trust",
    "UrnScheme",
]
