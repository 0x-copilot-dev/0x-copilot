"""Body-free measurement of the tool block the model is shown on every call.

This is PRD-03 of the Context Occupancy Ledger: audit items F, G and H — our
runtime tool descriptions, the ``args_schema`` JSON each one expands to, and the
two ``_display_*`` fields the display middleware adds to every wrapped tool.
Together they are the headline number the design document opens with:
``publish_artifact`` alone costs roughly 650 estimated tokens on *every* model
call of *every* run, and no surface in the system reports that today.

**One serialization, not two.** The tool block already has a digest —
``tool_schema_revision`` — that the prompt layer binds into prompt-cache
identity. Measuring the same block through a second, separately written
serializer would let the two drift, and a drifted digest is not an observability
bug: it silently changes cache identity on a live deployment.
:meth:`ToolSchemaLedger.revision` and :meth:`ToolSchemaLedger.measure` therefore
share :meth:`ToolSchemaLedger.schema_entry`, and ``factory._model_tool_schema_
revision`` delegates here rather than keeping a private copy.

**Only one of the two may fail open.** ``revision`` keeps the pre-existing
failure behaviour exactly — a tool whose ``model_json_schema()`` raises still
takes the harness build down, because a digest that quietly degrades is worse
than one that refuses. ``measure`` is observability and degrades per tool
instead (§6.4): a tool that cannot be serialized still appears in the report
with a zero footprint, because a missing row reads as "this tool is free" while
a zero row reads as "we failed to measure this tool".

Token counts here are the repo's char/4 stand-in. PRD-04's
:class:`~agent_runtime.observability.context_token_counter.ContextTokenCounter`
routes counting through the provider's real tokenizer; ``measure`` takes a
``counter`` callable so that swap is an injection rather than a rewrite.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from typing import Final

from pydantic import Field, NonNegativeInt

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    context_origin_of,
)
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json,
    canonical_json_bytes,
    canonical_json_sha256,
)


_LOGGER = logging.getLogger(__name__)


ToolSchemaTokenCounter = Callable[[str], int]
"""Counts the tokens of one tool's canonical schema text.

Deliberately a plain callable rather than a protocol: the only thing the ledger
needs from a counter is ``text -> int``, and every candidate implementation
(PRD-04's chain, a test double, a provider tokenizer) satisfies that without
inheriting anything.
"""


class HeuristicToolSchemaTokenCounter:
    """The char/4 stand-in PRD-04 replaces with the real tokenizer chain.

    Counts UTF-8 *bytes* rather than characters so the reported
    ``estimated_tokens`` is exactly ``ceil(byte_count / 4)`` over the same
    ``byte_count`` the footprint carries. A description with non-ASCII text
    would otherwise report fewer tokens than its measured size implies, and the
    two fields sitting side by side in one record must agree.

    Rounds up, matching
    :class:`~agent_runtime.context.memory.token_budget.TokenBudgetEvaluator`, so
    a small contributor never rounds down to a free one.
    """

    CHARS_PER_TOKEN: Final[int] = 4

    @classmethod
    def count(cls, text: str) -> int:
        """Return ``ceil(len(utf-8 bytes) / 4)`` for ``text``."""

        byte_count = len(text.encode("utf-8"))
        return (byte_count + cls.CHARS_PER_TOKEN - 1) // cls.CHARS_PER_TOKEN


class ToolSchemaFootprint(RuntimeContract):
    """What one tool's body-free schema costs, and who declared it.

    Per-tool rather than per-owner (design §10, decided): a per-owner aggregate
    would report "``agent_runtime.capabilities.backends`` costs 1,014 tokens",
    which names no action. Per-tool names the description to trim or the tool to
    move behind the capability bridge.

    Carries counts and identifiers only — never description text or schema
    bodies. Occupancy is exposed over an HTTP read API (§6.5), and ``tool_name``
    is the widest identifier that surface may carry.
    """

    tool_name: str = Field(max_length=200)
    label: str = Field(min_length=1, max_length=400)
    segment_class: ContextSegmentClass
    lifecycle: ContextLifecycle
    third_party: bool = False
    byte_count: NonNegativeInt
    estimated_tokens: NonNegativeInt
    declared: bool


class ToolSchemaLedger:
    """Serialize, digest, and measure the model-visible tool block.

    The class holds three operations over one shared serialization:

    * :meth:`schema_entry` — the exact body-free record one tool contributes.
    * :meth:`revision` — the load-bearing ``tool_schema_revision`` digest.
    * :meth:`measure` — the per-tool footprints the occupancy ledger reports.

    ``revision`` reproduces the digest byte-for-byte, including its sort order
    and its wrapper keys, because prompt-cache identity is keyed on it. Any
    change to the payload shape below changes cache identity for every existing
    deployment and must be treated as a migration, not a refactor.
    """

    class Keys:
        """Payload keys of the digested tool-block document."""

        SCHEMA_REVISION: Final[str] = "schema_revision"
        TOOLS: Final[str] = "tools"
        NAME: Final[str] = "name"
        DESCRIPTION: Final[str] = "description"
        ARGS_SCHEMA: Final[str] = "args_schema"

    class Values:
        """Constant values embedded in the digested document."""

        SCHEMA_REVISION: Final[str] = "model-visible-tools-v1"

    UNDECLARED_LABEL: Final[str] = UNDECLARED_CONTEXT_LABEL

    # An undeclared tool is still, structurally, part of the tool block and is
    # still re-sent on every call. Defaulting its class and lifecycle to the
    # truth of where it sits keeps a missing declaration a *labelling* gap
    # rather than also corrupting the lifecycle breakdown the report is read by.
    UNDECLARED_SEGMENT_CLASS: Final[ContextSegmentClass] = ContextSegmentClass.TOOLS
    UNDECLARED_LIFECYCLE: Final[ContextLifecycle] = ContextLifecycle.RESIDENT

    @classmethod
    def schema_entry(cls, tool: object) -> dict[str, object]:
        """Return the body-free fields the provider is shown for one tool.

        Exactly ``name``, ``description`` and the expanded ``args_schema`` — the
        three things that cross the wire — and nothing about the tool's
        implementation. ``args_schema`` is ``None`` for a tool that exposes no
        Pydantic schema, which is a real state for framework-supplied tools.

        Deliberately does **not** absorb a raising ``model_json_schema()``. Both
        callers depend on that: ``revision`` must keep the pre-ledger failure
        behaviour, and ``measure`` needs to see the failure to record a degraded
        footprint rather than a silently wrong one.
        """

        args_schema = getattr(tool, "args_schema", None)
        schema: object = None
        model_json_schema = getattr(args_schema, "model_json_schema", None)
        if callable(model_json_schema):
            schema = model_json_schema()
        return {
            cls.Keys.NAME: str(getattr(tool, "name", "")),
            cls.Keys.DESCRIPTION: str(getattr(tool, "description", "")),
            cls.Keys.ARGS_SCHEMA: schema,
        }

    @classmethod
    def revision(cls, model_tools: Sequence[object]) -> str:
        """Digest exactly the body-free tool schema fields visible to the model.

        Sorted by tool name so a composition-order change that does not change
        what the model is shown does not invalidate the prompt cache. The
        digest is content identity, not sequence identity.

        This is the single producer of ``tool_schema_revision``. It is
        byte-identical to the factory-local implementation it replaced, and a
        unit test pins that equality against a hand-built expected digest so the
        payload shape cannot drift unnoticed.
        """

        schemas = [cls.schema_entry(tool) for tool in model_tools]
        return canonical_json_sha256(
            {
                cls.Keys.SCHEMA_REVISION: cls.Values.SCHEMA_REVISION,
                cls.Keys.TOOLS: sorted(
                    schemas, key=lambda item: str(item[cls.Keys.NAME])
                ),
            }
        )

    @classmethod
    def measure(
        cls,
        model_tools: Sequence[object],
        *,
        counter: ToolSchemaTokenCounter | None = None,
    ) -> tuple[ToolSchemaFootprint, ...]:
        """Return one footprint per tool, in composition order.

        Composition order rather than the digest's sorted order: a reader of the
        occupancy report is looking at the surface as it was assembled, and the
        gated Wave-1 block sitting last is information about *why* those tools
        are the ones to defer.

        Never raises. A tool that cannot be serialized, a counter that throws,
        and a declaration that fails validation all degrade to a recorded row —
        occupancy measurement is best-effort and must never fail a run (§6.4).
        """

        count = counter or HeuristicToolSchemaTokenCounter.count
        return tuple(cls._footprint(tool, counter=count) for tool in model_tools)

    @classmethod
    def _footprint(
        cls,
        tool: object,
        *,
        counter: ToolSchemaTokenCounter,
    ) -> ToolSchemaFootprint:
        """Measure one tool, degrading to a zero footprint on any failure."""

        origin = cls._origin_of(tool)
        tool_name = cls._tool_name(tool)
        try:
            entry = cls.schema_entry(tool)
            byte_count = len(canonical_json_bytes(entry))
            estimated_tokens = cls._count(canonical_json(entry), counter=counter)
        except Exception:  # noqa: BLE001 — a failed measurement is still a row
            _LOGGER.warning(
                "Could not measure the tool-schema footprint of %r; "
                "recording a zero footprint.",
                tool_name,
                exc_info=True,
            )
            byte_count = 0
            estimated_tokens = 0
        return ToolSchemaFootprint(
            tool_name=tool_name,
            label=cls.UNDECLARED_LABEL if origin is None else origin.label,
            segment_class=(
                cls.UNDECLARED_SEGMENT_CLASS if origin is None else origin.segment_class
            ),
            lifecycle=(
                cls.UNDECLARED_LIFECYCLE if origin is None else origin.lifecycle
            ),
            third_party=False if origin is None else origin.third_party,
            byte_count=byte_count,
            estimated_tokens=estimated_tokens,
            declared=origin is not None,
        )

    @classmethod
    def _origin_of(cls, tool: object) -> ContextOrigin | None:
        """Read a tool's declaration, treating any failure as undeclared."""

        try:
            return context_origin_of(tool)
        except Exception:  # noqa: BLE001 — an unreadable declaration is absent
            _LOGGER.debug(
                "Could not read the context origin of a composed tool; "
                "measuring it as UNDECLARED.",
                exc_info=True,
            )
            return None

    @classmethod
    def _count(cls, text: str, *, counter: ToolSchemaTokenCounter) -> int:
        """Count ``text`` through ``counter``, falling back to the heuristic.

        An injected counter is the one part of this path written elsewhere, so
        it is the one part treated as untrusted: a raised exception, a negative
        number, or a non-integer all fall back to arithmetic that cannot fail.
        """

        try:
            counted = counter(text)
        except Exception:  # noqa: BLE001 — the fallback is the error handling
            _LOGGER.debug(
                "Tool-schema token counter failed; using the char/4 heuristic.",
                exc_info=True,
            )
            return HeuristicToolSchemaTokenCounter.count(text)
        if not isinstance(counted, int) or isinstance(counted, bool) or counted < 0:
            return HeuristicToolSchemaTokenCounter.count(text)
        return counted

    @classmethod
    def _tool_name(cls, tool: object) -> str:
        """Return a bounded, safe identifier for ``tool``.

        Bounded because ``tool_name`` is persisted and served over HTTP, and a
        composed tool's name ultimately comes from a registry this runtime does
        not own.
        """

        try:
            name = str(getattr(tool, "name", ""))
        except Exception:  # noqa: BLE001 — an unreadable name is an empty name
            return ""
        return name[:200]


__all__ = (
    "HeuristicToolSchemaTokenCounter",
    "ToolSchemaFootprint",
    "ToolSchemaLedger",
    "ToolSchemaTokenCounter",
)
