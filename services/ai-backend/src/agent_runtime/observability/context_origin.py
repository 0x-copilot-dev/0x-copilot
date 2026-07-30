"""The declaration seam for everything that occupies the model's context window.

§3.2 of the Context Occupancy Ledger design rejects a central enum of known
contributors: a list maintained by whoever owns the ledger is stale the moment
someone adds a tool, and it puts the burden on the one team least able to know
what a new contributor is for. The inversion is that **anything that puts text
in front of the model declares what it is, at the point it is composed**, and
the ledger only collects, reconciles, and reports those declarations.

Exactly one thing here is a closed enum — :class:`ContextSegmentClass`, the
structural taxonomy of a provider request (``system`` + ``tools[]`` +
``messages[]`` + ``response_format``), which genuinely is closed. Labels are
deliberately **not** an enum: they are owner-namespaced ``owner:name`` strings
so ownership is intrinsic to the label and no central registry has to enumerate
them.

Nothing in this module raises on the runtime path. A contributor that cannot be
stamped (a frozen or slotted object) simply reads back as undeclared, and the
ledger records it as ``UNDECLARED`` and counts it into ``undeclared_tokens``
(§4.4). Completeness is enforced by the AST conformance gate at CI time, never
by taking a run down over an observability concern (§6.4).
"""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Annotated, Final

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.assembly import PromptCacheEligibility


_LOGGER = logging.getLogger(__name__)


UNDECLARED_CONTEXT_LABEL: Final[str] = "UNDECLARED"
"""Label recorded for measured bytes that match no declaration (§4.4).

Expected to be zero tokens on a first-party path. A non-zero
``undeclared_tokens`` means a contributor broke the declaration contract and is
actionable as a defect — which is why it is one reserved sentinel rather than a
per-caller free-text bucket.
"""


class ContextSegmentClass(StrEnum):
    """The closed structural taxonomy of one provider request.

    Every provider request this runtime issues is a system block, a tool block,
    a message list, and optionally a structured-output schema. That set is
    closed by the wire format rather than by our choices, which is why it is
    safe to model as an enum while labels are not.
    """

    SYSTEM = "system"
    TOOLS = "tools"
    MESSAGES = "messages"
    RESPONSE_FORMAT = "response_format"


class ContextLifecycle(StrEnum):
    """How often a contributor's bytes are re-sent to the model.

    This is the field that makes the occupancy report *actionable* rather than
    merely descriptive. ``RESIDENT`` bytes are rent charged on every call and
    are fixed by deferring or trimming the surface; ``PER_RESULT`` bytes are a
    multiplier on tool-call count and are fixed by shrinking the per-result
    note. The two demand opposite remedies, so collapsing them would produce a
    report that recommends the wrong change.
    """

    RESIDENT = "resident"
    PER_TURN = "per_turn"
    PER_RESULT = "per_result"
    ON_DEMAND = "on_demand"


LABEL_SEPARATOR: Final[str] = ":"
MAX_OWNER_LENGTH: Final[int] = 200
MAX_NAME_LENGTH: Final[int] = 200
# Derived, never restated. Any contract that stores a rendered ``owner:name``
# label must bound its column by THIS value rather than by a guessed literal.
# Getting that wrong is not a cosmetic defect: a segment contract with a
# narrower bound raises ``ValidationError`` on a perfectly legal declaration,
# and it raises on the model-call path, where §6.4 forbids raising at all. That
# is exactly the bug this constant exists to make unrepresentable.
MAX_LABEL_LENGTH: Final[int] = MAX_OWNER_LENGTH + len(LABEL_SEPARATOR) + MAX_NAME_LENGTH


class ContextOrigin(RuntimeContract):
    """One contributor's declaration of what it puts in front of the model.

    ``owner`` is the dotted module that owns the text and ``name`` is a local
    label unique within that owner, so :attr:`label` is globally unique without
    any central allocation step. ``owner`` is constrained to a dotted-identifier
    shape and ``name`` forbids ``:`` and whitespace precisely so ``label``
    round-trips: a reader can always split an ``owner:name`` label back into its
    two halves.

    ``third_party`` marks text this repository does not author (§4.3) — the
    ``deepagents`` prompts and tool descriptions declared on the library's
    behalf by one pinned adapter. It matters to a reader because a third-party
    segment cannot be fixed by editing our source; it is fixed by a profile
    exclusion or a dependency change.
    """

    owner: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_OWNER_LENGTH,
            pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$",
        ),
    ]
    name: Annotated[
        str,
        Field(min_length=1, max_length=MAX_NAME_LENGTH, pattern=r"^[^\s:]+$"),
    ]
    segment_class: ContextSegmentClass
    lifecycle: ContextLifecycle
    cache_eligibility: PromptCacheEligibility | None = None
    third_party: bool = False

    @property
    def label(self) -> str:
        """The globally unique ``owner:name`` label carried on every segment."""

        return f"{self.owner}{LABEL_SEPARATOR}{self.name}"


class ContextOriginBinding:
    """Attach and read a :class:`ContextOrigin` on a composed contributor.

    The binding is an attribute stamped on the object itself rather than an
    entry in a side table, for two reasons that both come from how tools travel
    through this runtime. Composed tools are copied repeatedly — display-schema
    decoration and tool-policy enforcement each produce a new object via
    ``model_copy`` or a fresh wrapper — and an attribute in the instance
    ``__dict__`` survives ``model_copy`` while an identity-keyed side table does
    not. Tools are also unhashable Pydantic models, so a ``WeakKeyDictionary``
    is not available in the first place.

    :meth:`of` walks a bounded chain of wrapper attributes so a declaration made
    at composition time is still readable after the tool has been wrapped by
    budget, policy, or citation adapters — the measurement site in PRD-05 sees
    only the outermost object.
    """

    ATTRIBUTE: Final[str] = "__context_origin__"

    # The wrapper attribute names this codebase actually uses to hold an inner
    # tool (``ToolBudgetGuardedTool.inner``, the policy and citation wrappers).
    # Bounded and explicit rather than a generic attribute crawl: an unbounded
    # search over arbitrary objects is how an observability read turns into a
    # surprise property access on a live tool.
    WRAPPER_ATTRIBUTES: Final[tuple[str, ...]] = ("inner", "tool", "wrapped")
    MAX_WRAPPER_DEPTH: Final[int] = 8

    @classmethod
    def declare(cls, target: object, origin: ContextOrigin) -> object:
        """Stamp ``origin`` on ``target`` and return ``target`` for inline use.

        Returning the target is what lets a declaration read as a wrapper at the
        composition site (``append(declare(build_tool(), origin))``), which is
        also what the PRD-02 AST gate looks for: a tool appended to the model
        surface with no lexically adjacent declaration is the failure it exists
        to catch.

        An object that refuses attribute assignment is logged and returned
        unchanged. Refusing to compose it instead would let an observability
        concern take a run down, which §6.4 forbids.
        """

        try:
            setattr(target, cls.ATTRIBUTE, origin)
        except Exception:  # noqa: BLE001 — declaration is never load-bearing
            _LOGGER.debug(
                "Could not declare context origin %s on %s; "
                "it will measure as UNDECLARED.",
                origin.label,
                type(target).__name__,
            )
        return target

    @classmethod
    def of(cls, target: object) -> ContextOrigin | None:
        """Return the declaration on ``target`` or its wrappers, or ``None``.

        A value stamped under the binding attribute that is not a
        :class:`ContextOrigin` is ignored rather than trusted: the attribute
        namespace is shared with whatever else stamps a tool, and a malformed
        declaration should read as absent so it lands in ``undeclared_tokens``
        where it is visible.
        """

        current = target
        for _ in range(cls.MAX_WRAPPER_DEPTH):
            if current is None:
                return None
            declared = getattr(current, cls.ATTRIBUTE, None)
            if isinstance(declared, ContextOrigin):
                return declared
            current = cls._unwrap(current)
        return None

    @classmethod
    def _unwrap(cls, target: object) -> object | None:
        """Return the single inner tool ``target`` wraps, or ``None``."""

        for attribute in cls.WRAPPER_ATTRIBUTES:
            inner = getattr(target, attribute, None)
            if inner is not None and inner is not target:
                return inner
        return None


def declare_context_origin(target: object, origin: ContextOrigin) -> object:
    """Declare what ``target`` contributes to the model's context window.

    Thin module-level seam over :meth:`ContextOriginBinding.declare` so
    composition sites read as the design document writes them (§4.1) and so the
    AST conformance gate has one call name to look for.
    """

    return ContextOriginBinding.declare(target, origin)


def context_origin_of(target: object) -> ContextOrigin | None:
    """Read the declaration attached to ``target``, or ``None`` when undeclared."""

    return ContextOriginBinding.of(target)


__all__ = (
    "UNDECLARED_CONTEXT_LABEL",
    "ContextLifecycle",
    "ContextOrigin",
    "ContextOriginBinding",
    "ContextSegmentClass",
    "context_origin_of",
    "declare_context_origin",
)
