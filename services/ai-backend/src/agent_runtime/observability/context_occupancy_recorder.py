"""The capture seam of the Context Occupancy Ledger (PRD-05, design §3.1).

Everything before this module produced *contracts*: a declaration seam
(:mod:`~agent_runtime.observability.context_origin`), a counting chain
(:mod:`~agent_runtime.observability.context_token_counter`), a tool-block ledger,
a message classifier, a third-party adapter, and the snapshot record itself.
None of them ever looked at a real provider request. This module is where they
meet one, and where a *declaration* stops being a claim and becomes a
reconciled measurement (§4.4).

**Why the materialized request, and only the materialized request.** §3.1 fixes
measurement at ``ModelInvocationMiddleware.awrap_model_call`` because that is the
one boundary where every contribution has already landed: our fragments, the
``deepagents`` prompts and tool descriptions we never wrote, the profile
exclusions that make web and desktop differ for the same conversation, and the
middleware decorations applied on the way down. Measuring assembly *inputs*
instead would report the prompt we intended to send. This reports the one that
was sent.

**Three attribution paths, one vocabulary.** The system block is matched against
the typed :class:`~agent_runtime.prompts.assembly.PromptAssemblyPlan` by content
digest, so each fragment's own ``source_owner`` labels its span; whatever the
plan does not explain is offered to the pinned third-party adapter (§4.3), and
what survives that is ``UNDECLARED`` when a plan existed or the build-time
system prompt when none did. The tool block goes through
:class:`~agent_runtime.observability.context_tool_ledger.ToolSchemaLedger`, which
reads the declaration each tool was stamped with at composition and, for the
middleware tools the library installs behind our back, falls back to
:class:`~agent_runtime.observability.context_third_party.ThirdPartyToolOrigins`.
Messages go
through
:class:`~agent_runtime.observability.context_message_classifier.ContextMessageClassifier`,
whose structural rules resolve to the same declared origins. Three very
different mechanisms, one ``owner:name`` vocabulary in the report — that is the
whole point of §3.2's inversion.

**Nothing here may fail a run (§6.4).** Every public method is total: it returns
a worse answer rather than raising. The caller in ``runtime.py`` wraps the whole
capture-and-persist in a second guard anyway, because two independent guards is
what "must never raise into the model call" actually costs. A tokenizer that
explodes, a message shape a library bump introduced, a plan that does not match
the system prompt, a store that is down — each degrades to a partial snapshot or
a dropped one, never to a failed model call.

**Nothing here is a second billing meter (§6.1).** ``provider_input_tokens`` is
*copied* from the same :class:`~agent_runtime.observability.token_usage.NormalizedTokenUsage`
the ``UsageMeter`` consumes. This module writes no usage row, extends no
``Purpose``, and changes nothing about what the model was sent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from pydantic import NonNegativeInt, PositiveInt

from agent_runtime.observability.context_message_classifier import (
    ClassifiedMessagePart,
    ContextMessageClassifier,
)
from agent_runtime.observability.context_occupancy import (
    ContextOccupancySnapshot,
    ContextSegment,
    GraphScope,
    SnapshotBuilder,
)
from agent_runtime.observability.context_origin import (
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    ContextTextWidth,
)
from agent_runtime.observability.context_third_party import (
    ThirdPartyContextOrigins,
    ThirdPartyToolOrigins,
)
from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    TokenCounterSource,
)
from agent_runtime.observability.context_tool_ledger import (
    ToolSchemaFootprint,
    ToolSchemaLedger,
)
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from agent_runtime.prompts.assembly import (
    PromptAssemblyPlan,
    PromptFragment,
    PromptFragmentTier,
)


if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain.agents.middleware.types import ModelRequest


_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class ContextOccupancySink(Protocol):
    """The one durable write the capture seam performs.

    Structurally identical to
    :meth:`agent_runtime.api.ports.ContextOccupancyStorePort.append_context_occupancy`
    and satisfied by every adapter that implements it. Declared narrowly here
    rather than imported from ``api.ports`` for a layering reason: that module
    is the service's whole persistence surface and pulls the API schema lane
    into the import graph, which an observability read on the model-call path
    must not carry. A one-method structural protocol costs nothing and keeps
    this module a leaf.
    """

    async def append_context_occupancy(
        self,
        record: RuntimeContextOccupancyRecord,
    ) -> bool: ...


class RuntimeContextOrigins:
    """Declarations the runtime makes for request parts nobody else owns.

    Every other segment class has a contributor that can declare for itself: a
    tool is stamped where it is composed, a system fragment carries its
    ``source_owner``, and message content is declared by
    :class:`~agent_runtime.observability.context_message_classifier.MessageContextOrigins`.
    The structured-output schema has no such owner — it is a property of the
    call the graph is making, not of a code path that contributed text — so the
    runtime declares it here, exactly as it declares conversation content on the
    message side.

    ``RESIDENT`` because a structured call re-sends the same schema on every
    turn until the surface itself changes, which is the definition the lifecycle
    field carries (audit item T). ``cache_eligibility`` is left unset: the field
    records a *declared* intent taken from ``PromptFragment`` metadata, and the
    response format has no such declaration — asserting one would be a guess
    dressed as a fact.
    """

    RESPONSE_FORMAT: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.execution.model_invocation",
        name="response_format",
        segment_class=ContextSegmentClass.RESPONSE_FORMAT,
        lifecycle=ContextLifecycle.RESIDENT,
    )

    BUILD_TIME_SYSTEM_PROMPT: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.execution.factory",
        name="system_prompt",
        segment_class=ContextSegmentClass.SYSTEM,
        lifecycle=ContextLifecycle.RESIDENT,
    )
    """The system prompt handed to ``create_deep_agent`` when F2 assembled none.

    Per-fragment attribution needs a :class:`PromptAssemblyPlan`, and there is
    no plan unless the F2 prompt runtime is bound — which it is not on the
    desktop, where ``RunControlContext.current()`` yields no control binding.
    Every occupancy row a packaged run wrote carried ``assembly_record_id:
    None`` and, consequently, a single 4,798-token ``UNDECLARED`` span covering
    the whole system block. That is the largest number in the report, it is
    constant on every call, and it was being announced to users through the
    composer's context meter as a first-party contract defect.

    It is not one. Without a plan the system block has exactly one first-party
    contributor — the ``system_prompt`` argument the factory builds and passes
    to the builder — so naming it is a *more* accurate report than leaving it
    undeclared, not a papering-over. What is genuinely lost is the per-fragment
    breakdown, and that loss is carried in the segment's ``detail``
    (``system[unassembled]``) rather than smuggled into ``undeclared_tokens``,
    whose contract is about broken declarations and not about which prompt path
    a deployment runs.

    Applied **only** when no plan exists at all. A residue left over *after* a
    plan was matched is a real attribution gap — the plan and the prompt
    disagree — and stays ``UNDECLARED``, which is the case that field is for.

    ``cache_eligibility`` is unset for the same reason it is on the joiner: the
    field records a declared intent read from ``PromptFragment`` metadata, and
    an unassembled block carries no such metadata to read.
    """

    ASSEMBLY_JOINER: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.prompts",
        name="assembly_joiner",
        segment_class=ContextSegmentClass.SYSTEM,
        lifecycle=ContextLifecycle.RESIDENT,
    )
    """The whitespace ``PromptAssembler`` renders between fragments.

    Two bytes per join, and the reason it needs a declaration at all is
    ``undeclared_tokens``. That field is defined (§4.4) as bytes matching no
    declaration, is expected to be **0**, and any non-zero value is meant to be
    read as a contributor having broken the contract. A system prompt with
    eleven fragments has ten joiners; leaving them unattributed would light that
    alarm permanently on a perfectly healthy system, which destroys the only
    signal the field carries.

    Every joiner in one request rolls up into a single segment with
    ``item_count`` set to how many there were, rather than one segment each.
    That is not cosmetic: the token counter wraps each segment in a synthetic
    message and inherits the provider's per-message envelope, so ten separate
    two-byte segments would report roughly ten envelopes' worth of tokens for
    twenty bytes of text. One rolled-up segment pays the envelope once.

    ``cache_eligibility`` is deliberately unset. A joiner between two stable
    fragments rides in the cacheable prefix and one before a volatile fragment
    does not, so a single answer here would be wrong half the time — and §6.6's
    value depends on that field meaning something.
    """


@dataclass(frozen=True, slots=True)
class MaterializedProviderRequest:
    """The four parts of a provider request, read defensively exactly once.

    The middleware hands over a ``ModelRequest`` — a library object this
    runtime does not own, whose attributes are not guaranteed to be plain
    fields. Reading each part once, here, buys the same two properties
    :class:`~agent_runtime.observability.context_message_classifier.MaterializedMessage`
    buys per message: a lazily-computed property cannot answer differently to
    two different measurement passes, and a property that raises degrades to
    "absent" instead of taking the capture down.

    The parts are exactly §2's inventory of what can occupy a window — system,
    tools, messages, response format — which is closed by the wire format
    rather than by our choices, and is why
    :class:`~agent_runtime.observability.context_origin.ContextSegmentClass` is
    safe to model as an enum.
    """

    system_text: str
    tools: tuple[object, ...]
    messages: tuple[object, ...]
    response_format: object | None

    @classmethod
    def of(cls, request: object) -> MaterializedProviderRequest:
        """Read ``request`` into its four measurable parts, never raising."""

        return cls(
            system_text=cls._system_text(cls._attribute(request, "system_message")),
            tools=cls._sequence(cls._attribute(request, "tools")),
            messages=cls._sequence(cls._attribute(request, "messages")),
            response_format=cls._attribute(request, "response_format"),
        )

    @staticmethod
    def _attribute(target: object, name: str) -> object:
        """Read one attribute off an untrusted object, or ``None``.

        ``getattr`` with a default covers a *missing* attribute but not a
        property that raises on access, and both are the same answer for
        measurement.
        """

        try:
            return getattr(target, name, None)
        except Exception:  # noqa: BLE001 — unreadable is measured as absent
            _LOGGER.debug(
                "Could not read %r off the model request during occupancy "
                "measurement; treating it as absent.",
                name,
                exc_info=True,
            )
            return None

    @classmethod
    def _system_text(cls, message: object) -> str:
        """Render the system message's bytes, or ``""`` when there are none."""

        if message is None:
            return ""
        content = cls._attribute(message, "content")
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        return ContextOccupancyRecorder.render_json(content)

    @staticmethod
    def _sequence(value: object) -> tuple[object, ...]:
        """Materialize a request list, tolerating any other shape as empty."""

        if value is None:
            return ()
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return ()
        try:
            return tuple(value)
        except Exception:  # noqa: BLE001 — an unreadable list measures as empty
            _LOGGER.debug(
                "Could not iterate a model-request list during occupancy "
                "measurement; measuring it as empty.",
                exc_info=True,
            )
            return ()


@dataclass(frozen=True, slots=True)
class SystemSpan:
    """One contiguous slice of the system block and what explains it.

    ``origin`` is ``None`` for a slice no declaration covered, which becomes an
    ``UNDECLARED`` segment rather than being folded into a neighbour. Keeping
    the unexplained bytes as their own span is what makes ``undeclared_tokens``
    a number a reader can act on instead of a rounding difference hidden inside
    somebody else's fragment.

    ``digest`` is carried when the system already holds one for exactly these
    bytes — a fragment's ``content_digest`` — so counting takes the §3.4
    memoized path without re-hashing text the assembler already hashed.

    ``item_count`` is above one only for a rolled-up span: several occurrences
    of the same declared contributor measured as one segment, which is how the
    per-fragment joiners avoid paying a per-message tokenizer envelope each.
    """

    text: str
    origin: ContextOrigin | None
    detail: str
    digest: str | None = None
    item_count: int = 1


class SystemBlockAttributor:
    """Explain the materialized system prompt span by span (§3.2).

    The system block is the hardest of the four classes and the only one whose
    attribution is *reconstructive*. Tools and messages arrive as discrete
    objects that can be classified one at a time; the system prompt arrives as
    one string that several owners contributed to and that middleware may have
    decorated on the way down. So this walks it:

    1. Fragments of the typed :class:`PromptAssemblyPlan`, in plan order, are
       located in the string and verified by ``content_digest``. A verified span
       is labelled from the fragment's own ``source_owner`` — the declaration
       already exists in all but name (§4.1), so nothing new has to be
       maintained for the eleven typed sources.
    2. What is left over is offered to the pinned ``deepagents`` adapter (§4.3),
       which is how audit items B and D — the library's system prompts and the
       per-model harness suffix — stop being invisible.
    3. Anything still unexplained is ``UNDECLARED``, visibly, in the field whose
       whole job is to make a broken declaration contract look like a defect.

    Matching is by digest rather than by identity for a specific reason: the
    string reaching the provider is not guaranteed to be the plan's
    ``rendered_prompt``. Cache decoration, a framework suffix, and the
    fallback's undecorated retry all produce a system message that *contains*
    the fragments without being equal to their join. A digest-verified substring
    search survives all three; an equality check would attribute nothing on
    exactly the calls that matter most.
    """

    # Tiers whose bytes are rent rather than turn cost. ``SYSTEM_POLICY`` and
    # ``STABLE`` are, by the assembler's own definition, the fragments eligible
    # to form the cacheable stable prefix — they are re-sent unchanged until the
    # surface changes, which is what ``RESIDENT`` means. Everything at or below
    # ``CONTEXTUAL`` varies with conversation state and is re-rendered per turn.
    _RESIDENT_TIERS: Final[frozenset[PromptFragmentTier]] = frozenset(
        {PromptFragmentTier.SYSTEM_POLICY, PromptFragmentTier.STABLE}
    )

    _UNDECLARED_DETAIL: Final[str] = "system[unattributed]"
    _UNASSEMBLED_DETAIL: Final[str] = "system[unassembled]"
    _JOINER_DETAIL: Final[str] = "system[joiners]"

    def __init__(self, *, third_party: ThirdPartyPromptIndex) -> None:
        self._third_party = third_party

    def spans(
        self,
        system_text: str,
        *,
        plan: PromptAssemblyPlan | None,
    ) -> tuple[SystemSpan, ...]:
        """Return the ordered spans of ``system_text`` and their declarations.

        Total: a plan that does not match, a fragment whose owner is not a legal
        declaration, and a third-party sweep that found nothing all reduce the
        result toward one span covering the whole block. That is the honest
        degradation — the bytes are still reported, they are simply reported at
        the coarsest granularity the available evidence supports.

        ``plan is None`` and ``plan matched nothing`` are deliberately *not* the
        same outcome. With no plan there was no per-fragment assembly to fail,
        so the block is the build-time system prompt and is declared as such.
        With a plan that matched nothing, the plan and the prompt disagree —
        a real attribution defect — and the residue stays ``UNDECLARED``.
        """

        if not system_text:
            return ()
        assembled = plan is not None
        try:
            matched = self._plan_spans(system_text, plan=plan)
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _LOGGER.warning(
                "Could not attribute the system block to the assembly plan; "
                "recording it as undeclared occupancy.",
                exc_info=True,
            )
            matched = ()
        return self._fill_gaps(system_text, matched=matched, assembled=assembled)

    def _plan_spans(
        self,
        system_text: str,
        *,
        plan: PromptAssemblyPlan | None,
    ) -> tuple[tuple[int, int, SystemSpan], ...]:
        """Locate every plan fragment inside ``system_text``, in plan order.

        A forward-only cursor rather than an independent search per fragment:
        the assembler renders fragments in a fixed order, so a later fragment
        can only start after an earlier one ends, and scanning forward makes two
        fragments with identical bytes land on two different spans instead of
        both claiming the first occurrence.
        """

        if plan is None:
            return ()
        located: list[tuple[int, int, SystemSpan]] = []
        cursor = 0
        for fragment in plan.fragments:
            content = fragment.content
            if not content:
                continue
            start = system_text.find(content, cursor)
            if start < 0:
                continue
            end = start + len(content)
            if not self._digest_matches(system_text[start:end], fragment):
                continue
            located.append((start, end, self._fragment_span(fragment)))
            cursor = end
        return tuple(located)

    def _fill_gaps(
        self,
        system_text: str,
        *,
        matched: tuple[tuple[int, int, SystemSpan], ...],
        assembled: bool,
    ) -> tuple[SystemSpan, ...]:
        """Interleave the matched spans with what sits between and after them.

        Every byte of ``system_text`` lands in exactly one span. That total
        coverage is the property the whole reconciliation rests on: a walk that
        quietly dropped the bytes it could not explain would report a smaller
        system block than was sent and hide the difference inside
        ``unattributed_delta``, where it would be indistinguishable from
        tokenizer drift.
        """

        spans: list[SystemSpan] = []
        joiners: list[str] = []
        cursor = 0
        for start, end, span in matched:
            spans.extend(
                self._unmatched_spans(
                    system_text[cursor:start], joiners=joiners, assembled=assembled
                )
            )
            spans.append(span)
            cursor = end
        spans.extend(
            self._unmatched_spans(
                system_text[cursor:], joiners=joiners, assembled=assembled
            )
        )
        if joiners:
            spans.append(self._joiner_span(joiners))
        return tuple(spans)

    def _unmatched_spans(
        self,
        text: str,
        *,
        joiners: list[str],
        assembled: bool,
    ) -> tuple[SystemSpan, ...]:
        """Attribute leftover system bytes, collecting assembly joiners aside.

        A gap that is nothing but whitespace is the assembler's own ``"\\n\\n"``
        and is accumulated into ``joiners`` for a single rolled-up segment; see
        :attr:`RuntimeContextOrigins.ASSEMBLY_JOINER` for why that is a
        declaration rather than an ``UNDECLARED`` row. Anything else is real
        text somebody contributed, and is offered to the third-party adapter
        before anything else is decided about it.

        The library's own constants are peeled off first either way — a
        ``deepagents`` prompt inside an unassembled block still belongs to
        ``deepagents``, and sweeping it into a first-party bucket would trade
        one misattribution for another. Only the residue of that split is
        affected by ``assembled``.
        """

        if not text:
            return ()
        if not text.strip():
            joiners.append(text)
            return ()
        split = self._third_party.split(text, undeclared_detail=self._UNDECLARED_DETAIL)
        if assembled:
            return split
        return tuple(self._as_build_time_prompt(span) for span in split)

    @classmethod
    def _as_build_time_prompt(cls, span: SystemSpan) -> SystemSpan:
        """Declare an unassembled residue against the build-time system prompt.

        Only an undeclared span is rewritten. A span the third-party index
        already claimed keeps its library owner, because that attribution is
        evidence-based — the bytes matched a pinned constant — and is strictly
        better than the coarse first-party bucket.
        """

        if span.origin is not None:
            return span
        return replace(
            span,
            origin=RuntimeContextOrigins.BUILD_TIME_SYSTEM_PROMPT,
            detail=cls._UNASSEMBLED_DETAIL,
        )

    @classmethod
    def _joiner_span(cls, joiners: Sequence[str]) -> SystemSpan:
        """Roll every assembly joiner in one request into a single span."""

        text = "".join(joiners)
        return SystemSpan(
            text=text,
            origin=RuntimeContextOrigins.ASSEMBLY_JOINER,
            detail=cls._JOINER_DETAIL,
            digest=ContextOccupancyRecorder.digest_of(text),
            item_count=len(joiners),
        )

    @classmethod
    def _fragment_span(cls, fragment: PromptFragment) -> SystemSpan:
        """Turn one verified fragment into a span carrying its declaration."""

        return SystemSpan(
            text=fragment.content,
            origin=cls._fragment_origin(fragment),
            detail=fragment.fragment_id,
            digest=fragment.content_digest,
        )

    @classmethod
    def _fragment_origin(cls, fragment: PromptFragment) -> ContextOrigin | None:
        """Project a fragment's existing attribution onto a context origin.

        The nine-plus typed ``PromptSource`` values map 1:1 onto declarations
        owned by their existing ``source_owner`` strings (§4.1), so this is a
        projection and not a new registry. A ``source_owner`` that is not a
        dotted identifier cannot form a legal declaration; that span becomes
        ``UNDECLARED`` rather than raising, because a malformed label is a
        first-party defect and ``undeclared_tokens`` is precisely where such
        defects are supposed to surface.
        """

        try:
            return ContextOrigin(
                owner=fragment.source_owner,
                name=fragment.fragment_id,
                segment_class=ContextSegmentClass.SYSTEM,
                lifecycle=(
                    ContextLifecycle.RESIDENT
                    if fragment.tier in cls._RESIDENT_TIERS
                    else ContextLifecycle.PER_TURN
                ),
                cache_eligibility=fragment.cache_eligibility,
            )
        except Exception:  # noqa: BLE001 — an illegal label measures as undeclared
            _LOGGER.debug(
                "Prompt fragment %r does not form a legal context origin; "
                "measuring its span as UNDECLARED.",
                fragment.fragment_id,
                exc_info=True,
            )
            return None

    @staticmethod
    def _digest_matches(span: str, fragment: PromptFragment) -> bool:
        """Verify a located span really is the fragment it was matched against.

        The search is by content and the confirmation is by ``content_digest``,
        which is the digest the assembler already computed over exactly these
        bytes. It costs one hash of a span we already believe is equal, and it
        is what makes the claim "this fragment occupies these bytes" checkable
        rather than assumed.
        """

        return ContextOccupancyRecorder.digest_of(span) == fragment.content_digest


class ThirdPartyPromptIndex:
    """Substring index of the ``deepagents`` constants that occupy the window.

    :class:`~agent_runtime.observability.context_third_party.ThirdPartyContextOrigins`
    produces the *declarations* for library-owned text — labels, lifecycles, and
    a pinned per-constant inventory — but deliberately not the text itself: its
    fixture is about which constants exist and how big they are, not about
    holding a copy of somebody else's prompt. Attribution needs the bytes, so
    this resolves each inventory row back to the live constant and builds one
    process-lifetime ``text -> declaration`` index.

    Three properties make that safe on the model-call path:

    - **Built once, lazily.** The sweep imports every submodule of the
      dependency; nothing about it belongs inside a per-call measurement. The
      first call pays for it, every later call reads a dict.
    - **Longest match first.** Library prompts nest — a suffix can appear inside
      a larger constant — and claiming the shorter one first would leave a
      remainder that looks like drift. Ordering by descending length makes the
      split deterministic and maximal.
    - **Total.** A package that will not import, a constant that vanished, an
      attribute that raises: each drops one row. The residue then measures as
      ``UNDECLARED``, which is a fixture diff waiting to be reviewed rather than
      an exception on a live call.
    """

    _MAX_MATCHES_PER_SPAN: Final[int] = 32
    """Bound on the peel loop, so a pathological span cannot spin.

    A realistic system block carries a handful of library constants. The bound
    exists because the loop is driven by data this repository does not author,
    and an unbounded loop over third-party text on the model-call path is the
    kind of thing that turns an observability feature into an incident.
    """

    def __init__(self, *, origins: ThirdPartyContextOrigins | None = None) -> None:
        self._origins = origins
        self._index: tuple[tuple[str, ContextOrigin], ...] | None = None

    @classmethod
    def disabled(cls) -> ThirdPartyPromptIndex:
        """An index that attributes nothing, for tests and third-party-free hosts.

        Not the same object as an index whose sweep found nothing: this one
        never imports the dependency at all, which is what lets a unit test
        assert ``UNDECLARED`` behaviour without depending on whichever
        ``deepagents`` version happens to be installed.
        """

        return _EMPTY_THIRD_PARTY_INDEX

    def split(
        self,
        text: str,
        *,
        undeclared_detail: str,
    ) -> tuple[SystemSpan, ...]:
        """Split ``text`` into declared third-party constants and the residue.

        Returns spans in text order so the caller can concatenate them back into
        the original block. A span matching no constant keeps the reserved
        ``UNDECLARED`` label rather than a per-caller bucket, because §4.4's
        alarm only means something if exactly one thing can trip it.
        """

        index = self._entries()
        if not index:
            return (SystemSpan(text=text, origin=None, detail=undeclared_detail),)
        return self._split_with(
            text,
            index=index,
            undeclared_detail=undeclared_detail,
            depth=0,
        )

    def _split_with(
        self,
        text: str,
        *,
        index: tuple[tuple[str, ContextOrigin], ...],
        undeclared_detail: str,
        depth: int,
    ) -> tuple[SystemSpan, ...]:
        """Peel the first (longest) matching constant, then recurse on the rest."""

        if not text:
            return ()
        if depth >= self._MAX_MATCHES_PER_SPAN:
            return (SystemSpan(text=text, origin=None, detail=undeclared_detail),)
        for constant, origin in index:
            start = text.find(constant)
            if start < 0:
                continue
            end = start + len(constant)
            head = self._split_with(
                text[:start],
                index=index,
                undeclared_detail=undeclared_detail,
                depth=depth + 1,
            )
            tail = self._split_with(
                text[end:],
                index=index,
                undeclared_detail=undeclared_detail,
                depth=depth + 1,
            )
            middle = SystemSpan(
                text=constant,
                origin=origin,
                detail=origin.name,
                digest=ContextOccupancyRecorder.digest_of(constant),
            )
            return (*head, middle, *tail)
        return (SystemSpan(text=text, origin=None, detail=undeclared_detail),)

    def _entries(self) -> tuple[tuple[str, ContextOrigin], ...]:
        """Return the memoized ``(text, declaration)`` index, building it once."""

        if self._index is None:
            self._index = self._build()
        return self._index

    def _build(self) -> tuple[tuple[str, ContextOrigin], ...]:
        """Resolve every discovered constant back to its live text."""

        if self._origins is None:
            return ()
        entries: list[tuple[str, ContextOrigin]] = []
        try:
            constants = self._origins.discover()
        except Exception:  # noqa: BLE001 — a broken sweep declares nothing
            _LOGGER.warning(
                "Third-party prompt discovery failed; library-owned system text "
                "will measure as UNDECLARED.",
                exc_info=True,
            )
            return ()
        for constant in constants:
            text = self._resolve(constant.module, constant.attribute)
            if not text:
                continue
            try:
                entries.append((text, constant.to_origin()))
            except Exception:  # noqa: BLE001 — an illegal label is simply absent
                _LOGGER.debug(
                    "Third-party constant %s does not form a legal declaration; "
                    "its bytes will measure as UNDECLARED.",
                    constant.qualified_name,
                )
        entries.sort(key=lambda entry: (-len(entry[0]), entry[1].label))
        return tuple(entries)

    @staticmethod
    def _resolve(module_name: str, attribute: str) -> str | None:
        """Read one module-level constant defensively, or ``None``."""

        import importlib  # noqa: PLC0415 — imported only when the index is built

        try:
            module = importlib.import_module(module_name)
            value = getattr(module, attribute, None)
        except Exception:  # noqa: BLE001 — an unresolvable constant is absent
            return None
        return value if isinstance(value, str) and value else None


_EMPTY_THIRD_PARTY_INDEX: Final[ThirdPartyPromptIndex] = ThirdPartyPromptIndex()


class ContextOccupancyRecorder:
    """Measure one materialized provider request and reconcile it (§3.1, §4.4).

    Two public operations, deliberately split by *when the truth arrives*:

    :meth:`capture`
        Runs before dispatch, when the request exists and the provider's totals
        do not. It decomposes the window into segments and produces a snapshot
        whose ``provider_input_tokens`` is ``None`` — not ``0``. Zero would read
        as "the provider reported nothing was sent", which would make
        ``unattributed_delta`` a large negative number on every single call.

    :meth:`finalize`
        Runs after the attempt, when the provider has spoken. It copies the
        authoritative totals in and recomputes the two residuals against them.
        The segments are carried through untouched — §3.3 forbids scaling them
        toward the provider total, because across five provider families that
        manufactures precision this ledger does not have.

    Splitting them this way is also what makes retries honest (§6.3): a second
    attempt re-captures, because its context genuinely differs from the attempt
    that failed, and produces a second snapshot under a second
    ``attempt_ordinal`` rather than overwriting the first.

    The instance is stateless apart from injected collaborators, so one recorder
    is shared by every call in a process and the §3.4 digest cache behind the
    counter is what makes steady-state cost proportional to *new* content rather
    than to total context.
    """

    class Details:
        """Bounded identifiers a segment may carry (§6.5).

        Occupancy is served over an HTTP read API, so ``detail`` is position and
        identity only — a tool name, a ``fragment_id``, a message ordinal. Never
        an excerpt. Collected here so the complete set of strings this module
        can put in a persisted field is one list a reviewer can read.
        """

        RESPONSE_FORMAT: Final[str] = "response_format"
        MESSAGES: Final[str] = "messages"

    _JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")
    _RESPONSE_FORMAT_KIND_KEY: Final[str] = "kind"
    _RESPONSE_FORMAT_SCHEMA_KEY: Final[str] = "schema"

    def __init__(
        self,
        *,
        counter: ContextTokenCounter | None = None,
        third_party: ThirdPartyPromptIndex | None = None,
        third_party_tools: ThirdPartyToolOrigins | None = None,
        builder: SnapshotBuilder | None = None,
    ) -> None:
        self._counter = counter or ContextTokenCounter()
        self._third_party = third_party or ThirdPartyPromptIndex(
            origins=ThirdPartyContextOrigins()
        )
        # The tool-block counterpart of ``_third_party``. Separate collaborators
        # because they resolve different things by different means — one indexes
        # the dependency's prompt *text*, the other reads a tool's authoring
        # module — and a test that wants a third-party-free system block still
        # wants real tool attribution.
        self._third_party_tools = third_party_tools or ThirdPartyToolOrigins()
        self._builder = builder or SnapshotBuilder()
        self._system = SystemBlockAttributor(third_party=self._third_party)

    # --- public seam ---------------------------------------------------------

    def capture(
        self,
        request: "ModelRequest[Any] | object",
        *,
        identity: object,
        attempt_ordinal: PositiveInt,
        graph_scope: GraphScope,
        provider: str,
        model_family: str,
        context_window_tokens: NonNegativeInt | None,
        plan: PromptAssemblyPlan | None = None,
        assembly_record_id: str | None = None,
    ) -> ContextOccupancySnapshot | None:
        """Decompose one materialized request into a pre-dispatch snapshot.

        ``identity`` is the run's
        :class:`~agent_runtime.execution.call_identity.RuntimeModelCallIdentity`,
        read only for its ``model_call_id``; it is typed loosely so this module
        stays a leaf of the observability lane rather than importing the
        execution lane it is measured from.

        ``context_window_tokens`` is supplied by the caller, and ``None`` is a
        legitimate value with a specific meaning: the window denominator is
        unknown, so ``free_tokens`` will be ``None`` rather than a fabricated
        number (§4.5).

        ``model_family`` doubles as the tokenizer selector: it is the
        provider-native model name the counting chain routes on, so a segment is
        counted with the tokenizer of the model it was actually sent to rather
        than with a house default.

        Never raises. Every failure path yields a snapshot — possibly with no
        segments at all — because a missing row reads as "this call had no
        context", while an empty row reads as "we failed to measure this call".
        ``None`` is the last resort, for the one case where not even an empty
        snapshot can be assembled; see :meth:`_assembled`.
        """

        try:
            materialized = MaterializedProviderRequest.of(request)
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _LOGGER.warning(
                "Could not read the model request for occupancy measurement; "
                "recording an empty snapshot for this attempt.",
                exc_info=True,
            )
            segments: tuple[ContextSegment, ...] = ()
        else:
            segments = (
                *self._guarded(
                    ContextSegmentClass.SYSTEM,
                    lambda: self._system_segments(
                        materialized, plan=plan, model=model_family
                    ),
                ),
                *self._guarded(
                    ContextSegmentClass.TOOLS,
                    lambda: self._tool_segments(materialized, model=model_family),
                ),
                *self._guarded(
                    ContextSegmentClass.MESSAGES,
                    lambda: self._message_segments(materialized, model=model_family),
                ),
                *self._guarded(
                    ContextSegmentClass.RESPONSE_FORMAT,
                    lambda: self._response_format_segments(
                        materialized, model=model_family
                    ),
                ),
            )
        return self._assembled(
            model_call_id=self._model_call_id(identity),
            attempt_ordinal=attempt_ordinal,
            graph_scope=graph_scope,
            provider=provider,
            model_family=model_family,
            context_window_tokens=context_window_tokens,
            assembly_record_id=assembly_record_id,
            segments=segments,
        )

    def finalize(
        self,
        snapshot: ContextOccupancySnapshot,
        usage: NormalizedTokenUsage | None,
    ) -> ContextOccupancySnapshot:
        """Reconcile a captured snapshot against the provider's own totals.

        ``usage`` is ``None`` when the provider reported nothing — a failed
        attempt before the usage block, or a provider that omits one. That is
        deliberately *not* the same as a zero-token usage object: the former
        leaves ``provider_input_tokens`` unset and ``unattributed_delta`` at
        zero (no measurement to reconcile against), while the latter would
        assert the provider billed nothing and turn our whole estimate into a
        negative residual.

        The numbers are copied from the same ``NormalizedTokenUsage`` the
        ``UsageMeter`` consumes. This is read-side denormalization for
        reconciliation, never a second source of billing truth (§6.1).
        """

        try:
            return self._build(
                model_call_id=snapshot.model_call_id,
                attempt_ordinal=snapshot.attempt_ordinal,
                graph_scope=snapshot.graph_scope,
                provider=snapshot.provider,
                model_family=snapshot.model_family,
                context_window_tokens=snapshot.context_window_tokens,
                assembly_record_id=snapshot.assembly_record_id,
                segments=snapshot.segments,
                usage=usage,
            )
        except Exception:  # noqa: BLE001 — the captured snapshot is still true
            _LOGGER.warning(
                "Could not reconcile a context occupancy snapshot against "
                "provider usage; keeping the pre-dispatch measurement.",
                exc_info=True,
            )
            return snapshot

    def project(
        self,
        snapshot: ContextOccupancySnapshot,
        *,
        org_id: str,
        run_id: str,
        conversation_id: str,
    ) -> RuntimeContextOccupancyRecord:
        """Project a reconciled snapshot onto its durable row.

        The projection lives here because this is the one seam that owns both
        layers. ``persistence.records`` is a leaf that deliberately does not
        import the observability lane — reaching up from it would put an import
        cycle one edit away — so nothing in the type system connects the two
        contracts, and this keyword-for-keyword mapping is that connection.
        ``tests/unit/agent_runtime/persistence/test_context_occupancy_projection.py``
        sweeps both directions to prove no measured field is dropped on the way
        to a column.

        The three identity arguments are the columns the snapshot has no opinion
        about: *where* the measurement happened, supplied by the run the capture
        seam is already inside.

        The decomposition is bounded to what the row accepts (see
        :meth:`_persistable_segments`); every rollup total is passed through
        unchanged.
        """

        return RuntimeContextOccupancyRecord.from_measurement(
            org_id=org_id,
            run_id=run_id,
            conversation_id=conversation_id,
            model_call_id=snapshot.model_call_id,
            attempt_ordinal=snapshot.attempt_ordinal,
            assembly_record_id=snapshot.assembly_record_id,
            graph_scope=RuntimeContextGraphScope(snapshot.graph_scope.value),
            provider=snapshot.provider,
            model_family=snapshot.model_family,
            context_window_tokens=snapshot.context_window_tokens,
            estimated_input_tokens=snapshot.estimated_input_tokens,
            provider_input_tokens=snapshot.provider_input_tokens,
            cached_input_tokens=snapshot.cached_input_tokens,
            cache_creation_input_tokens=snapshot.cache_creation_input_tokens,
            undeclared_tokens=snapshot.undeclared_tokens,
            segments=self._persistable_segments(snapshot.segments),
        )

    @classmethod
    def _persistable_segments(
        cls,
        segments: Sequence[ContextSegment],
    ) -> tuple[dict[str, object], ...]:
        """Render the decomposition, bounded to what one row may carry.

        ``RuntimeContextOccupancyRecord.Limits.MAX_SEGMENTS`` was sized against
        the *tool* block — "~25–40 segments on a realistic call" — to stop a
        runaway tool surface writing an unbounded JSONB document. PRD-07 then
        made messages a segment source too, at one to three segments *per
        message*, so the producer outgrew the bound: a ~100-turn conversation
        measures past 512 segments, the row refuses to validate, and the
        fail-open guard in :meth:`persist` drops it. The result was that
        occupancy went dark on exactly the long conversations it exists to
        diagnose — a silent total loss dressed as fail-open.

        Bounding here rather than widening the column, because the producer is
        genuinely unbounded and a wider literal only moves the cliff. The
        segments kept are the largest by ``estimated_tokens``, which are the ones
        a reader acts on, then restored to canonical order so two rows still
        diff cleanly.

        Nothing is fabricated and no total moves: ``estimated_input_tokens``,
        ``undeclared_tokens`` and ``unattributed_delta`` are computed on the
        snapshot and stored as columns, so they stay exact whatever this list
        holds — which is also why a reader can *detect* an elision, as the gap
        between the segment sum and ``estimated_input_tokens``. That is the same
        posture the read API already takes for a segment it cannot parse
        (``unreadable_segment_count``): exact totals with an incomplete
        decomposition beats no answer at all.
        """

        limit = RuntimeContextOccupancyRecord.Limits.MAX_SEGMENTS
        bounded = tuple(segments)
        if len(bounded) > limit:
            _LOGGER.warning(
                "Context occupancy measured %d segments, above the %d a row may "
                "carry; persisting the largest %d. The snapshot totals remain "
                "exact.",
                len(bounded),
                limit,
                limit,
            )
            largest = sorted(
                bounded,
                key=lambda segment: (-segment.estimated_tokens, segment.sort_key),
            )[:limit]
            bounded = tuple(sorted(largest, key=lambda segment: segment.sort_key))
        return tuple(segment.model_dump(mode="json") for segment in bounded)

    async def persist(
        self,
        snapshot: ContextOccupancySnapshot,
        *,
        sink: ContextOccupancySink | None,
        org_id: str,
        run_id: str,
        conversation_id: str,
    ) -> bool:
        """Append one snapshot, returning whether a new row was written.

        Skips silently when no sink is wired, which is the state of every
        deployment until the read API in §7 needs the rows. Every failure —
        an unprojectable snapshot, a store that is down, an adapter that
        raises — is a *dropped snapshot* and nothing more: this is called from
        the model-call path, where §6.4 says measurement must never take a run
        down.
        """

        if sink is None:
            return False
        try:
            record = self.project(
                snapshot,
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation_id,
            )
            return await sink.append_context_occupancy(record)
        except Exception:  # noqa: BLE001 — a dropped snapshot is the failure mode
            _LOGGER.warning(
                "Could not persist a context occupancy snapshot for run %s; "
                "dropping the measurement.",
                run_id,
                exc_info=True,
            )
            return False

    # --- shared helpers ------------------------------------------------------

    @staticmethod
    def _guarded(
        segment_class: ContextSegmentClass,
        measure: Callable[[], tuple[ContextSegment, ...]],
    ) -> tuple[ContextSegment, ...]:
        """Measure one segment class, degrading that class alone on failure.

        The guard is per class rather than around the whole pass because the
        four classes fail for unrelated reasons — an untrusted MCP tool name, a
        message shape a library bump introduced, a plan that no longer matches
        the system prompt — and letting any one of them erase the other three
        would turn a small attribution gap into a snapshot that says the model
        was sent nothing. A class that degrades logs once and contributes no
        segments; the bytes it could not attribute then surface in
        ``unattributed_delta`` against the provider's own total, which is
        exactly the residual that field exists to hold.
        """

        try:
            return tuple(measure())
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _LOGGER.warning(
                "Could not measure the %s segment class for this model call; "
                "reporting no segments for it.",
                segment_class.value,
                exc_info=True,
            )
            return ()

    @classmethod
    def _safe_detail(cls, value: str | None) -> str | None:
        """Bound an identifier so an untrusted name cannot fail a segment (§6.5).

        ``ContextSegment`` fails closed on a ``detail`` that looks like content:
        too long, or carrying control characters. Most details this module
        produces are ours and could never trip that. Tool names cannot —
        they ultimately come from an MCP registry this runtime does not own —
        and a single hostile name would otherwise take the whole tool block's
        measurement down through the class guard above. Sanitizing is the right
        answer *here* and the wrong answer for a label: a clipped ``owner:name``
        no longer round-trips into its two halves, while a clipped tool name is
        still recognizably that tool.
        """

        if value is None:
            return None
        cleaned = "".join(
            character for character in value if character >= " " and character != "\x7f"
        )
        bounded = cleaned[: ContextSegment.MAX_DETAIL_LENGTH]
        return bounded or None

    @staticmethod
    def digest_of(text: str) -> str:
        """SHA-256 of ``text``'s UTF-8 bytes, for §3.4 memoized counting.

        The counter memoizes on ``(model, digest)``, so identical bytes are
        tokenized once per process however many calls resend them — which is the
        entire reason a 650-token tool description does not cost a tokenizer
        call on every turn of every run. Hashing is cheap next to tokenizing;
        where the system already holds a digest (a fragment's ``content_digest``)
        it is passed through instead of recomputed.

        The encoding comes from :class:`ContextTextWidth`, the one definition
        shared with ``ContextSegment.byte_count`` and the classifier's per-part
        width. Lone surrogates are tolerated because untrusted content can
        legally carry one and a raise here would turn ordinary tool output into
        an unmeasurable segment — the same reason the width tolerates them, which
        is precisely why the two must not be separate implementations.
        """

        return hashlib.sha256(ContextTextWidth.utf8_bytes(text)).hexdigest()

    @classmethod
    def render_json(cls, value: object) -> str:
        """Render ``value`` deterministically, degrading rather than raising.

        Sorted keys and tight separators so two identical requests materialize
        to identical bytes and the digest memoization can hit; ``ensure_ascii``
        off so a non-ASCII character measures as the UTF-8 bytes a provider
        actually receives rather than as a ``\\uXXXX`` escape twice its width.

        Deliberately not ``surfaces_v2.canonical_json``: that renderer enforces
        a cross-language digest contract and *raises* on tuples, bytes,
        non-finite floats, and arbitrary objects, all of which appear in request
        payloads this module does not control. Nothing here is compared against
        a TypeScript rendering, so that contract buys this module nothing and
        would cost it a guaranteed fallback on every unusual block.
        """

        try:
            return json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                separators=cls._JSON_SEPARATORS,
                default=str,
            )
        except Exception:  # noqa: BLE001 — the fallback is the error handling
            _LOGGER.debug(
                "Could not render a request payload as JSON for occupancy "
                "measurement; falling back to its string form.",
                exc_info=True,
            )
        try:
            return str(value)
        except Exception:  # noqa: BLE001 — an unreadable value measures as empty
            return ""

    # --- per-class measurement ----------------------------------------------

    def _system_segments(
        self,
        materialized: MaterializedProviderRequest,
        *,
        plan: PromptAssemblyPlan | None,
        model: str,
    ) -> tuple[ContextSegment, ...]:
        """Measure the system block, span by attributed span (§3.2)."""

        return tuple(
            self._segment_for_span(span, model=model)
            for span in self._system.spans(materialized.system_text, plan=plan)
        )

    def _segment_for_span(self, span: SystemSpan, *, model: str) -> ContextSegment:
        """Count one system span under its declaration, or as ``UNDECLARED``.

        An unattributed span is ``RESIDENT`` because that is structurally what
        the system block is: it is re-sent on every call until the surface
        changes. Defaulting to the truth of *where* the bytes sit keeps a
        missing declaration a labelling gap rather than also corrupting the
        lifecycle breakdown a reader acts on — the same choice
        ``ToolSchemaLedger`` makes for an undeclared tool.
        """

        detail = self._safe_detail(span.detail)
        if span.origin is None:
            return ContextSegment.measure_undeclared(
                span.text,
                counter=self._counter,
                model=model,
                segment_class=ContextSegmentClass.SYSTEM,
                lifecycle=ContextLifecycle.RESIDENT,
                detail=detail,
                item_count=span.item_count,
            )
        return ContextSegment.measure(
            span.text,
            counter=self._counter,
            model=model,
            origin=span.origin,
            detail=detail,
            item_count=span.item_count,
            digest=span.digest or self.digest_of(span.text),
        )

    def _tool_segments(
        self,
        materialized: MaterializedProviderRequest,
        *,
        model: str,
    ) -> tuple[ContextSegment, ...]:
        """Measure the tool block per tool, through the PRD-03 ledger.

        Per-tool rather than per-owner (design §10, decided): "this package
        costs 1,014 tokens" names no action, while "``publish_artifact`` costs
        650" names the description to trim or the tool to move behind the
        capability bridge.

        The ledger is the single serializer of the model-visible tool schema —
        the same one that produces ``tool_schema_revision`` — so measurement and
        prompt-cache identity cannot drift apart. Its counting is injected here
        so the numbers come from the real tokenizer chain rather than from the
        char/4 stand-in it defaults to.

        ``fallback_origin`` is §4.3 applied to the tool block, and it is what
        closes the gap that made ``undeclared_tokens`` permanently non-zero.
        ``materialized.tools`` is the *library's* final tool list, not ours:
        ``create_deep_agent`` installs the filesystem, todo and subagent
        middleware tools inside itself, so those nine reach the wire having
        never passed the one composition site that stamps a declaration. The
        stamp still wins wherever one exists — this only speaks for tools that
        have none, and only when their authoring module is outside this
        repository.
        """

        if not materialized.tools:
            return ()
        bridge = _ToolSchemaCounterBridge(counter=self._counter, model=model)
        footprints = ToolSchemaLedger.measure(
            materialized.tools,
            counter=bridge,
            fallback_origin=self._third_party_tools.origin_for,
        )
        sources = bridge.sources_for(footprints)
        return tuple(
            self._segment_for_footprint(footprint, source=source)
            for footprint, source in zip(footprints, sources, strict=True)
        )

    @classmethod
    def _segment_for_footprint(
        cls,
        footprint: ToolSchemaFootprint,
        *,
        source: TokenCounterSource,
    ) -> ContextSegment:
        """Turn one measured tool footprint into a segment.

        Built field-by-field rather than through ``ContextSegment.measure``
        because the ledger has already counted: re-measuring here would
        serialize every tool schema a second time on every model call, for a
        number that must agree with the one the ledger produced anyway.
        """

        return ContextSegment(
            segment_class=footprint.segment_class,
            label=footprint.label,
            lifecycle=footprint.lifecycle,
            third_party=footprint.third_party,
            detail=cls._safe_detail(footprint.tool_name),
            byte_count=footprint.byte_count,
            estimated_tokens=footprint.estimated_tokens,
            counter_source=source,
        )

    def _message_segments(
        self,
        materialized: MaterializedProviderRequest,
        *,
        model: str,
    ) -> tuple[ContextSegment, ...]:
        """Measure the message list through the PRD-07 structural classifier.

        The classifier splits rather than labels: the citation pointer note and
        the tool-budget note are peeled off a result's tail into their own
        origins, which is what makes audit items L and M visible at all. This
        method's only job is to join each returned part with a token count.
        """

        parts = ContextMessageClassifier.classify(materialized.messages)
        return tuple(self._segment_for_part(part, model=model) for part in parts)

    def _segment_for_part(
        self,
        part: ClassifiedMessagePart,
        *,
        model: str,
    ) -> ContextSegment:
        """Count one classified message part under its resolved declaration."""

        detail = self._safe_detail(part.detail) or self.Details.MESSAGES
        if part.origin is None:
            return ContextSegment.measure_undeclared(
                part.text,
                counter=self._counter,
                model=model,
                segment_class=ContextSegmentClass.MESSAGES,
                lifecycle=part.lifecycle,
                detail=detail,
                item_count=part.item_count,
            )
        return ContextSegment.measure(
            part.text,
            counter=self._counter,
            model=model,
            origin=part.origin,
            detail=detail,
            item_count=part.item_count,
            digest=self.digest_of(part.text),
        )

    def _response_format_segments(
        self,
        materialized: MaterializedProviderRequest,
        *,
        model: str,
    ) -> tuple[ContextSegment, ...]:
        """Measure the structured-output schema when the call carries one.

        Audit item T, and the answer to design §10's third open question:
        included. It is small and present only on structured calls, but leaving
        it out would put its bytes into ``unattributed_delta``, where they would
        be indistinguishable from tokenizer drift — the one thing that field
        must stay clean enough to mean.
        """

        if materialized.response_format is None:
            return ()
        text = self._response_format_text(materialized.response_format)
        return (
            ContextSegment.measure(
                text,
                counter=self._counter,
                model=model,
                origin=RuntimeContextOrigins.RESPONSE_FORMAT,
                detail=self.Details.RESPONSE_FORMAT,
                digest=self.digest_of(text),
            ),
        )

    @classmethod
    def _response_format_text(cls, response_format: object) -> str:
        """Render what a structured-output request actually puts on the wire.

        The provider is sent the JSON schema, so the schema is what is measured.
        This mirrors ``canonical_model_request_digest``'s own response-format
        identity — the same ``schema.model_json_schema()`` read, in the same
        shape — so the bytes attributed here are the bytes that call already
        treats as the request's structured-output identity.

        A response format whose schema cannot be resolved still measures: its
        type name is a small, honest stand-in, and a zero would claim a
        structured call costs nothing.
        """

        schema = getattr(response_format, cls._RESPONSE_FORMAT_SCHEMA_KEY, None)
        kind = (
            f"{type(response_format).__module__}.{type(response_format).__qualname__}"
        )
        rendered = getattr(schema, "model_json_schema", None)
        if isinstance(schema, type) and callable(rendered):
            try:
                return cls.render_json(
                    {
                        cls._RESPONSE_FORMAT_KIND_KEY: kind,
                        cls._RESPONSE_FORMAT_SCHEMA_KEY: rendered(),
                    }
                )
            except Exception:  # noqa: BLE001 — a schema that will not render
                _LOGGER.debug(
                    "Could not render a response-format schema for occupancy "
                    "measurement; measuring its type identity instead.",
                    exc_info=True,
                )
        return cls.render_json({cls._RESPONSE_FORMAT_KIND_KEY: kind})

    # --- assembly ------------------------------------------------------------

    def _assembled(
        self,
        *,
        model_call_id: str,
        attempt_ordinal: PositiveInt,
        graph_scope: GraphScope,
        provider: str,
        model_family: str,
        context_window_tokens: NonNegativeInt | None,
        assembly_record_id: str | None,
        segments: Sequence[ContextSegment],
    ) -> ContextOccupancySnapshot | None:
        """Assemble the captured snapshot, degrading to ``None`` on a raise.

        The one guard :meth:`capture` was missing. Every per-class measurement
        already runs inside :meth:`_guarded`, but the snapshot *record* is built
        after all four of them, outside every guard — and building it is Pydantic
        construction, which is exactly how the label-bound defect escaped: a
        validator on a field the measurement pass fills raised, on the
        model-call path, out of a method whose contract is "never raises"
        (§6.4).

        Two independent guards in the middleware do not excuse an unguarded
        construction here, because the middleware's own comment names the reason
        its guard exists — "the recorder is already total" — and a caller that
        believes a false claim is how the first fail-open hole reached
        production. The honest degradation is ``None``: not even an empty
        snapshot could be assembled, so there is nothing to reconcile, nothing
        to stream, and nothing to persist for this attempt.
        """

        try:
            return self._build(
                model_call_id=model_call_id,
                attempt_ordinal=attempt_ordinal,
                graph_scope=graph_scope,
                provider=provider,
                model_family=model_family,
                context_window_tokens=context_window_tokens,
                assembly_record_id=assembly_record_id,
                segments=segments,
                usage=None,
            )
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _LOGGER.warning(
                "Could not assemble a context occupancy snapshot for model call "
                "%s; dropping the measurement for this attempt.",
                model_call_id,
                exc_info=True,
            )
            return None

    def _build(
        self,
        *,
        model_call_id: str,
        attempt_ordinal: PositiveInt,
        graph_scope: GraphScope,
        provider: str,
        model_family: str,
        context_window_tokens: NonNegativeInt | None,
        assembly_record_id: str | None,
        segments: Sequence[ContextSegment],
        usage: NormalizedTokenUsage | None,
    ) -> ContextOccupancySnapshot:
        """Hand the segments and the provider's totals to the reconciling builder."""

        cached, cache_creation = self._cache_subsets(usage)
        return self._builder.build(
            model_call_id=model_call_id,
            graph_scope=graph_scope,
            provider=provider,
            model_family=model_family,
            segments=segments,
            assembly_record_id=assembly_record_id,
            attempt_ordinal=attempt_ordinal,
            context_window_tokens=context_window_tokens,
            provider_input_tokens=(None if usage is None else usage.input_tokens),
            cached_input_tokens=cached,
            cache_creation_input_tokens=cache_creation,
        )

    @staticmethod
    def _cache_subsets(usage: NormalizedTokenUsage | None) -> tuple[int, int]:
        """Copy the cache subsets, fitted to the total they are subsets *of*.

        ``NormalizedTokenUsage`` documents ``input_tokens`` as the **gross**
        figure and the two cache numbers as subsets of it, and
        :class:`~agent_runtime.persistence.records.RuntimeContextOccupancyRecord`
        enforces that at the durability boundary. Provider usage blocks are model
        output, which this service treats as untrusted, and they do not always
        honour it: a LangChain-normalized ``usage_metadata`` that carries
        ``input_token_details.cache_read`` while ``input_tokens`` is ``0`` makes
        ``OpenAIProviderTokenUsageExtractor`` emit ``cached > input``, and
        ``merge``'s field-wise max cannot repair it because the maximum of two
        zeros is zero.

        Copied verbatim, such a block *deleted the measurement*: the row refused
        to validate and ``persist``'s fail-open guard dropped it. So the one
        field §6.6 exists for — "large but cached" versus "large and re-billed" —
        was also the field that could take the whole snapshot away, on the
        provider this product routes by default.

        The anchor is left exactly as reported. ``provider_input_tokens`` is
        §6.1's read-side copy of the number the ``UsageMeter`` bills on, so
        raising it to make the arithmetic work would put two different provider
        totals for one call on two surfaces — a worse failure than an imprecise
        subset. The subsets are decorations on that anchor, so they are the half
        that yields: cache reads keep their value first (that is the number §6.6
        is read for) and cache creation takes what is left. Nothing is invented
        and no total moves; an inconsistent block simply reports less cache
        detail than it claimed, and says so in the log.
        """

        if usage is None:
            return 0, 0
        total = usage.input_tokens
        cached = min(usage.cached_input_tokens, total)
        cache_creation = min(usage.cache_creation_input_tokens, total - cached)
        if (
            cached != usage.cached_input_tokens
            or cache_creation != usage.cache_creation_input_tokens
        ):
            _LOGGER.warning(
                "Provider usage reported cache subsets (%d cached, %d created) "
                "larger than its own %d input tokens; recording %d and %d so the "
                "occupancy row still reconciles.",
                usage.cached_input_tokens,
                usage.cache_creation_input_tokens,
                total,
                cached,
                cache_creation,
            )
        return cached, cache_creation

    @staticmethod
    def _model_call_id(identity: object) -> str:
        """Read the call identity's ``model_call_id``, or a safe placeholder.

        The snapshot's identity is ``(model_call_id, attempt_ordinal)`` and the
        record requires a non-empty id, so an unreadable identity still has to
        produce something. It produces a constant that is obviously not a real
        call id, which lands in the store as a visible anomaly rather than as a
        row that silently claims to describe a different call.

        Wrapped rather than relying on ``getattr``'s default: the default only
        covers a *missing* attribute, and this runs outside the per-class guards
        — a raising identity here would be the one path in ``capture`` that
        could still throw.
        """

        try:
            value = getattr(identity, "model_call_id", None)
        except Exception:  # noqa: BLE001 — unreadable is measured as absent
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "model-call:unidentified"


class _ToolSchemaCounterBridge:
    """Adapt :class:`ContextTokenCounter` to the tool ledger's counter callable.

    The ledger's counter contract is deliberately a plain ``text -> int``: it
    needs nothing else, and every candidate implementation satisfies it without
    inheriting anything. The occupancy ledger, though, also has to persist
    *which tier* produced each count — ``counter_source`` on a segment is the
    §6.4 signal that distinguishes an authoritative tokenizer number from a
    degraded approximation — so this bridge records the tier alongside each
    count as the ledger walks the tools.

    **Alignment is reconstructed from the footprints, not assumed positional.**
    The ledger calls this exactly once per tool *unless* that tool's schema
    failed to serialize, in which case it records a zero footprint without
    counting at all. Padding a short read at the *end* of the list therefore
    shifted every tier past the failure by one: a tool that could not be
    measured inherited a later tool's ``TOKENIZER``, so its zero footprint read
    as an authoritative "this tool is free", while a tool the real tokenizer
    counted was labelled ``PROXY``. That inverts the one field §6.4 gives a
    reader to tell a trustworthy number from a degraded one.

    A failed footprint is identifiable without guessing: ``schema_entry`` always
    renders at least ``{"args_schema":null,"description":"","name":""}``, so a
    non-zero ``byte_count`` is exactly the set of tools that reached the counter.
    Zipping the recorded tiers onto those tools puts every tier back on the tool
    that produced it, and the tools that never reached the counter get ``PROXY``
    — which is what actually happened to them.
    """

    def __init__(self, *, counter: ContextTokenCounter, model: str) -> None:
        self._counter = counter
        self._model = model
        self._sources: list[TokenCounterSource] = []

    def __call__(self, text: str) -> int:
        """Count one tool's canonical schema text, memoized per §3.4."""

        tokens, source = self._counter.count_digested(
            text,
            model=self._model,
            digest=ContextOccupancyRecorder.digest_of(text),
        )
        self._sources.append(source)
        return tokens

    def sources_for(
        self,
        footprints: Sequence[ToolSchemaFootprint],
    ) -> tuple[TokenCounterSource, ...]:
        """Return one tier per footprint, realigned around unmeasured tools."""

        recorded = iter(self._sources)
        return tuple(
            next(recorded, TokenCounterSource.PROXY)
            if footprint.byte_count
            else TokenCounterSource.PROXY
            for footprint in footprints
        )


__all__ = (
    "ContextOccupancyRecorder",
    "ContextOccupancySink",
    "MaterializedProviderRequest",
    "RuntimeContextOrigins",
    "SystemBlockAttributor",
    "SystemSpan",
    "ThirdPartyPromptIndex",
)
