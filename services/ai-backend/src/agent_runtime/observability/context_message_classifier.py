"""Structural classification of the ``messages`` block (PRD-07, design §4.6).

Messages are the largest occupancy bucket (audit items J–S) and the one class
where the contributor is not always a code path: nobody *declares* a user turn.
Classification is therefore **structural** — derived from the shape of the
materialized message — but it resolves to the **same declared origins** as every
other contributor, not to a parallel taxonomy. :class:`MessageContextOrigins`
holds those declarations, one per row of the §4.6 table, so the report reads in
one vocabulary end to end.

**Why this is a splitter and not a labeller.** Two of the audit's findings are
invisible at message granularity: the citation pointer note (~20–30 tokens on
*every* tool result, item L) and the tool-budget note (~30 tokens on results near
the cap, item M) are appended to a result's tail by wrappers that the model sees
as part of the result. Labelling the whole ``ToolMessage`` ``tool_result`` would
bury both inside a number nobody can act on. So a ``ToolMessage`` is split: the
note suffixes are peeled off the tail into their own parts and the remainder
keeps the conversation label. The split is **exact rather than regex-guessy**
because both note formats are single-sourced constants —
:attr:`~agent_runtime.capabilities.citation_capturing_tool.CitationHint.NOTE_PREFIX`
and
:attr:`~agent_runtime.capabilities.tool_budget_middleware.ToolBudgetUsage.NOTE_PREFIX`
— read here from the same attributes their producers render from. A peeled part
carries its leading separator, so the parts of one message sum byte-for-byte
back to the message.

**Where the split deliberately stops.** ``ToolResultNote`` appends onto whatever
part of a result the model reads, and for a *dict-shaped* result (an MCP
``CallToolResult`` envelope, or a generic dict) that is a new ``content`` block
or a new top-level key — not a trailing string. Once such a result is serialized
into a ``ToolMessage`` the note sits inside the JSON body, without the separator,
with its own characters JSON-escaped. Peeling it would mean re-deriving the
producer's escaping and reassembling a non-contiguous remainder: a pattern guess
over untrusted text, which is exactly what the single-sourced constants exist to
avoid. So those note bytes stay attributed to ``tool_result``. The mis-attribution
is bounded and lifecycle-safe — ``tool_result`` and both note origins are all
``PER_RESULT``, so the lifecycle rollup a reader acts on is unchanged, and only
the note-versus-body split within it under-reports. ``test_context_message_
classifier`` pins this so the boundary cannot move silently.

**Untrusted input, and what that costs.** Message content is model output, tool
output, and user text: all untrusted (see the service's untrusted-inputs rule).
Two consequences are designed for rather than assumed away. First, nothing here
raises — an unreadable message degrades to one ``UNDECLARED`` part, because
occupancy is best-effort observability and must never take a run down (§6.4).
Second, the marker checks are ranked by how forgeable they are: metadata markers
that a producer stamps (``lc_source``, ``lc_evicted_to``, a
:class:`~agent_runtime.context.tool_result_admission.ToolResultAdmission`
artifact) are authoritative, while content-prefix markers are a documented
fallback that a hostile message can spoof. The blast radius of a spoof is a
mislabelled row in an observability report — never an authorization,
truncation, or routing decision — and the alternative (missing a real summary or
a real state-file stub) understates exactly the occupancy this ledger exists to
expose.

**No silent catch-all.** A shape matching no rule is labelled
``UNDECLARED`` and counts into ``undeclared_tokens`` (§4.4), the same as an
undeclared tool. A ``SystemMessage`` found in the message list lands there
deliberately: it belongs to the ``system`` segment class, and its appearance
here is precisely the kind of drift the field exists to surface.

This module does **not** count tokens. It returns the exact text of each part
and lets the caller run it through
:class:`~agent_runtime.observability.context_token_counter.ContextTokenCounter`,
because counting is memoized per digest per model (§3.4) and that cache belongs
to the measurement pass, not to the classifier.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import logging
from typing import Annotated, ClassVar, Final, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import Field, NonNegativeInt, model_validator

from agent_runtime.capabilities.citation_capturing_tool import CitationHint
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetUsage
from agent_runtime.capabilities.tool_result_notes import ToolResultNote
from agent_runtime.context.tool_result_admission import (
    ToolResultAdmission,
    ToolResultAdmissionAdapter,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.context_origin import (
    MAX_LABEL_LENGTH as MAX_CONTEXT_LABEL_LENGTH,
)
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
)
from agent_runtime.observability.redactor import Sensitive, SensitiveCategory


_LOGGER = logging.getLogger(__name__)


class MessageContextOrigins:
    """The declarations for everything that lands in the ``messages`` block.

    §4.6's table, transcribed. Each entry is the declaration a message
    contributor would make for itself if it were a composed tool — the runtime
    makes it on behalf of conversation content, which has no code-path owner,
    and on behalf of the three wrappers whose text rides on a result.

    ``lifecycle`` is the field that makes each row actionable, and the values
    here are not decoration:

    - ``PER_RESULT`` on the two notes and on ``tool_result`` says the cost is a
      *multiplier on tool-call count*. Trimming a 30-token note is worth 30
      tokens × every result the run produces.
    - ``PER_TURN`` on conversation content and on the summary says the bytes are
      re-sent as the conversation advances; they are fixed by compaction, not by
      editing a string.
    - ``ON_DEMAND`` on the binary and subagent-trace origins says the bytes are
      only there because the model pulled them in — the fix is a prompt or a
      tool-surface change, not a trim.

    ``cache_eligibility`` is deliberately left unset on every message origin.
    The field carries a *declared* intent taken from ``PromptFragment``
    metadata; message history has no such declaration, and the snapshot's
    ``cached_input_tokens`` already carries the provider's cache truth (§6.6).
    Asserting an eligibility here would be a guess dressed as a declaration.
    """

    WORKSPACE_BINARY_B64: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities.workspace",
        name="binary_b64",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.ON_DEMAND,
    )
    SUMMARY: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.context.memory",
        name="summary",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_TURN,
    )
    OFFLOAD_STUB: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.context",
        name="offload_stub",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_RESULT,
    )
    CITATION_POINTER_NOTE: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities",
        name="citation_pointer_note",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_RESULT,
    )
    TOOL_BUDGET_NOTE: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.capabilities",
        name="tool_budget_note",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_RESULT,
    )
    SUBAGENT_TRACE: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.context.memory",
        name="subagent_trace",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.ON_DEMAND,
    )
    TOOL_RESULT: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.conversation",
        name="tool_result",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_RESULT,
    )
    USER: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.conversation",
        name="user",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_TURN,
    )
    ASSISTANT_TEXT: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.conversation",
        name="assistant_text",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_TURN,
    )
    ASSISTANT_TOOL_CALLS: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.conversation",
        name="assistant_tool_calls",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_TURN,
    )
    ASSISTANT_THINKING: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.conversation",
        name="assistant_thinking",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_TURN,
    )
    STATE_FILE: Final[ContextOrigin] = ContextOrigin(
        owner="agent_runtime.execution",
        name="state_file",
        segment_class=ContextSegmentClass.MESSAGES,
        lifecycle=ContextLifecycle.PER_TURN,
    )

    ALL: Final[tuple[ContextOrigin, ...]] = (
        WORKSPACE_BINARY_B64,
        CITATION_POINTER_NOTE,
        TOOL_BUDGET_NOTE,
        SUBAGENT_TRACE,
        SUMMARY,
        OFFLOAD_STUB,
        ASSISTANT_TEXT,
        ASSISTANT_THINKING,
        ASSISTANT_TOOL_CALLS,
        TOOL_RESULT,
        USER,
        STATE_FILE,
    )
    """Label-sorted inventory of the message declarations, for PRD-02's gate.

    Ordered by ``label`` rather than by attribute name, so the tuple is directly
    comparable with the sorted inventory the conformance gate produces, and
    pinned for the same reason ``test_llm_seam_gate`` pins its call sites: a new
    message origin should not be able to appear without someone consciously
    adding a line, which is the moment they accept the context cost.
    """


class ClassifiedMessagePart(RuntimeContract):
    """One attributable slice of one message, measured but not yet counted.

    The join with a token count happens in the measurement pass, so this carries
    ``text`` — the exact material of the part — rather than a token number. That
    makes it the **one contract in this family that holds content**, and it is
    an in-process intermediate only: it is never persisted, never serialized to
    an event, and never logged. ``text`` is tagged
    :class:`~agent_runtime.observability.redactor.Sensitive` so that
    ``SafeLogDumper`` elides it if a future log line ever dumps one, and the
    persisted record it feeds
    (:class:`~agent_runtime.observability.context_occupancy.ContextSegment`)
    keeps counts and identifiers only (§6.5).

    ``origin`` is carried alongside the flattened declaration fields so the
    measurement site can hand it straight to ``ContextSegment.measure`` without
    re-deriving a label, and ``None`` marks the ``UNDECLARED`` case where
    ``ContextSegment.measure_undeclared`` is the right constructor. The
    validator below makes the two halves incapable of disagreeing: a part whose
    ``label`` says one thing and whose ``origin`` says another would reintroduce
    exactly the drift the declaration seam exists to prevent.

    ``byte_count`` is UTF-8 bytes of ``text``, validated rather than trusted,
    because the note split's correctness claim *is* that the parts sum back to
    the message. A hand-set ``byte_count`` would make that claim unfalsifiable.
    """

    MAX_DETAIL_LENGTH: ClassVar[int] = 200
    MAX_LABEL_LENGTH: ClassVar[int] = MAX_CONTEXT_LABEL_LENGTH
    """Label bound, imported from the contract that defines what a label is.

    A part exists to become a segment, so the two bounds must agree: a part that
    validated under a wider bound than the record it feeds would push the
    failure one step downstream, into the pass that can no longer fall back to
    an ``UNDECLARED`` row for it.

    This used to be a restated literal, on the reasoning that importing it would
    drag the token counter — and litellm — into the import graph of an object
    that only slices strings. The reasoning was sound but aimed at the wrong
    module: the bound belongs to ``context_origin``, which imports neither. The
    restated copy then drifted from what a ``ContextOrigin`` can actually spell,
    which is precisely the failure mode all three copies were meant to avoid.
    """

    ENCODING: ClassVar[str] = "utf-8"
    ENCODING_FALLBACK_ERRORS: ClassVar[str] = "surrogatepass"

    UNDECLARED_LIFECYCLE: ClassVar[ContextLifecycle] = ContextLifecycle.PER_TURN
    """Structural lifecycle for bytes no declaration covered.

    A message the ledger cannot attribute is still, structurally, a message: it
    is re-sent as the conversation advances. Defaulting to ``PER_TURN`` keeps a
    missing declaration a *labelling* gap rather than also corrupting the
    lifecycle breakdown the report is read by — the same choice
    ``ToolSchemaLedger`` makes with ``RESIDENT`` for an undeclared tool.
    """

    label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL_LENGTH)]
    segment_class: Literal[ContextSegmentClass.MESSAGES] = ContextSegmentClass.MESSAGES
    lifecycle: ContextLifecycle
    third_party: bool = False
    detail: str | None = Field(default=None, max_length=MAX_DETAIL_LENGTH)
    byte_count: NonNegativeInt
    item_count: NonNegativeInt = 1
    text: Annotated[str, Sensitive(SensitiveCategory.MODEL_OUTPUT)] = ""
    origin: ContextOrigin | None = None

    @model_validator(mode="after")
    def _validate_declaration_agrees_with_measurement(self) -> ClassifiedMessagePart:
        """Reject a part whose declaration, label, or size contradict each other.

        Three cheap invariants, all of them load-bearing downstream:

        1. ``byte_count`` describes ``text`` — the note split's sum property.
        2. A declared part's flattened fields mirror its ``origin`` exactly.
        3. An undeclared part carries the one reserved sentinel label, so
           ``undeclared_tokens`` cannot be diluted by a free-text bucket.

        A violation is a programming error at a construction site, not a
        runtime condition; the classifier's own guard converts it into an
        ``UNDECLARED`` part rather than letting it reach the model call.
        """

        if self.byte_count != self.utf8_byte_count(self.text):
            msg = "byte_count must be the UTF-8 length of text"
            raise ValueError(msg)
        if self.origin is None:
            if self.label != UNDECLARED_CONTEXT_LABEL:
                msg = "a part without an origin must carry the UNDECLARED label"
                raise ValueError(msg)
            return self
        if self.origin.segment_class is not ContextSegmentClass.MESSAGES:
            msg = "a message part must declare the messages segment class"
            raise ValueError(msg)
        if (
            self.label != self.origin.label
            or self.lifecycle is not self.origin.lifecycle
            or self.third_party != self.origin.third_party
        ):
            msg = "label, lifecycle, and third_party must mirror the origin"
            raise ValueError(msg)
        return self

    @property
    def is_undeclared(self) -> bool:
        """Whether these bytes matched no declaration (§4.4 — expected never)."""

        return self.origin is None

    @classmethod
    def utf8_byte_count(cls, text: str) -> int:
        """UTF-8 length of ``text``, tolerating the lone surrogates JSON allows.

        ``"\\ud800"`` is a legal JSON string escape, so a provider response or an
        MCP server can hand this runtime a Python ``str`` holding an unpaired
        surrogate. A plain ``.encode("utf-8")`` raises on one, and every
        construction site here is inside the classifier's fail-open guard — so
        without this fallback an ordinary tool result carrying one stray escape
        would be recorded as a zero-byte ``UNDECLARED`` part.

        That failure mode is worse than it looks, and it is why this is a
        correctness fix rather than a nicety. ``undeclared_tokens`` is defined
        (§4.4) as *measured bytes matching no declaration*, expected to be **0**,
        with any non-zero value actionable as a contract bug — and §9 pins that
        with a hermetic run asserting it stays 0. Letting untrusted content
        decide whether that alarm fires would make the one field that flags real
        declaration breaches fire on text nobody declared wrong.

        ``surrogatepass`` is the honest width: it encodes the surrogate in the
        three bytes its UTF-8 form occupies, which is what the byte count is for.
        """

        try:
            return len(text.encode(cls.ENCODING))
        except UnicodeEncodeError:
            return len(text.encode(cls.ENCODING, cls.ENCODING_FALLBACK_ERRORS))

    @classmethod
    def declared(
        cls,
        origin: ContextOrigin,
        *,
        text: str,
        detail: str | None = None,
        item_count: NonNegativeInt = 1,
    ) -> ClassifiedMessagePart:
        """Build a part attributed to ``origin``.

        Every interpretive field is taken from the declaration rather than
        re-supplied by the caller, so a rule cannot quietly disagree with the
        origin it claims to be classifying as.
        """

        return cls(
            label=origin.label,
            lifecycle=origin.lifecycle,
            third_party=origin.third_party,
            detail=detail,
            byte_count=cls.utf8_byte_count(text),
            item_count=item_count,
            text=text,
            origin=origin,
        )

    @classmethod
    def undeclared(
        cls,
        *,
        text: str,
        detail: str | None = None,
        item_count: NonNegativeInt = 1,
    ) -> ClassifiedMessagePart:
        """Build a part for bytes no rule could attribute (§4.4).

        Named explicitly rather than reachable by passing ``None`` for an
        origin, because recording undeclared occupancy is a decision and should
        read like one at the call site.
        """

        return cls(
            label=UNDECLARED_CONTEXT_LABEL,
            lifecycle=cls.UNDECLARED_LIFECYCLE,
            third_party=False,
            detail=detail,
            byte_count=cls.utf8_byte_count(text),
            item_count=item_count,
            text=text,
            origin=None,
        )


@dataclass(frozen=True, slots=True)
class MaterializedMessage:
    """One untrusted message rendered to text **once**, for the whole rule chain.

    Rendering a message is the expensive half of classification: a content block
    that is not plain text is serialized to JSON, and a request can carry
    hundreds of them. The rules, though, are a first-match-wins chain in which
    several rules must look at the same text before one of them claims it — the
    summary probe, the offload-stub probe, and the tool-result split each need
    the whole body. Letting each rule materialize for itself made the cost a
    multiple of the request size rather than the request size, against a §3.4
    budget of p95 < 15 ms *including* the tokenizer. So the render happens here,
    before the chain starts, and every rule reads the result.

    Rendering once buys a correctness property too, not just speed. ``message``
    is a library object this runtime does not own, and reading it is not
    guaranteed to be pure — a lazily-built or streaming content property can
    answer differently on a second read. One render means every rule sees the
    same bytes, so the note split's "the parts sum back to the message" claim is
    about a fixed string rather than about whatever the object last returned.

    ``block_texts`` is positionally aligned with ``blocks`` so a rule that keeps
    a subset of blocks (rule 5 separating thinking from answer text) recovers
    its text by selection instead of by re-rendering. ``text`` is the whole
    message: the raw string for string content, and the ordered join of
    ``block_texts`` for block content.
    """

    message: object
    detail: str
    blocks: tuple[object, ...]
    block_texts: tuple[str, ...]
    text: str

    def text_where(self, keep: Callable[[object], bool]) -> str:
        """Join the already-rendered text of the blocks ``keep`` selects."""

        return "".join(
            rendered
            for block, rendered in zip(self.blocks, self.block_texts, strict=True)
            if keep(block)
        )


class ContextMessageClassifier:
    """Resolve a materialized message list into declared, measurable parts.

    One entry point, :meth:`classify`. Everything else is a rule, and the rules
    are applied in the order §4.6 fixes, first match wins:

    1. base64 / ``encoding == "base64"`` content → workspace binary origin
    2. a summarization output marker → summary origin
    3. a ``ToolMessage`` carrying a ``ToolResultAdmission`` stub → offload stub
    4. a ``ToolMessage`` → note suffixes split off the tail, remainder
       ``tool_result``; a result derived from a ``/subagents/`` read →
       subagent trace
    5. an ``AIMessage`` → thinking blocks, then tool calls, then text
    6. Deep Agents state injection → state file
    7. a ``HumanMessage`` → user

    Two consequences of "first match wins" are worth stating plainly rather than
    leaving for a reader to discover:

    - A message carrying base64 content is *entirely* the binary origin, even
      when it also carries a caption. The payload dominates the byte count by
      orders of magnitude, and splitting a mime-type string off an image block
      would name no action.
    - An offloaded stub (rule 3) is not note-split (rule 4). A citation note
      appended *after* admission therefore reads as stub bytes. The stub is
      bounded to 4 KiB by
      ``ToolResultAdmissionAdapter.DEFAULT_OFFLOADED_MODEL_CONTENT_LIMIT_CHARS``,
      so the mis-attribution is bounded and lands on a label that is already
      about compression.

    Every rule returns a possibly-empty tuple, so :meth:`classify`'s chain reads
    as the numbered list above. Every message yields **at least one** part, even
    an empty one: a missing row reads as "this message is free", while a
    zero-byte row reads as "this message carried nothing".
    """

    class Keys:
        """Every attribute and mapping key the rules read by name.

        Collected here rather than inlined so the complete set of shape
        assumptions this classifier makes about LangChain and ``deepagents``
        message payloads is one list, and a library bump that renames one is a
        one-line change with a failing test pointing at it.
        """

        ADDITIONAL_KWARGS: Final[str] = "additional_kwargs"
        ARGS: Final[str] = "args"
        ARTIFACT: Final[str] = "artifact"
        BASE64: Final[str] = "base64"
        CONTENT: Final[str] = "content"
        ENCODING: Final[str] = "encoding"
        ID: Final[str] = "id"
        NAME: Final[str] = "name"
        SOURCE: Final[str] = "source"
        TEXT: Final[str] = "text"
        TOOL_CALLS: Final[str] = "tool_calls"
        TOOL_CALL_ID: Final[str] = "tool_call_id"
        TYPE: Final[str] = "type"

        # ``deepagents`` message metadata. ``lc_source`` tags the synthetic
        # summary ``HumanMessage`` its summarization middleware injects;
        # ``lc_evicted_to`` tags a human message whose body was moved to the
        # virtual filesystem; ``read_file_path`` rides on a binary read result.
        LC_EVICTED_TO: Final[str] = "lc_evicted_to"
        LC_SOURCE: Final[str] = "lc_source"
        READ_FILE_PATH: Final[str] = "read_file_path"

    class Markers:
        """Literal markers the rules match, and where each one comes from.

        The two note prefixes and the offload header are **read from their
        producers** (see :attr:`ContextMessageClassifier.NOTE_MARKERS` and
        :attr:`ContextMessageClassifier.OFFLOAD_STUB_HEADER`) so the split is
        exact. The values below cannot be imported without cost or do not exist
        as constants at all, and each carries the reason it is duplicated.
        """

        BASE64_ENCODING: Final[str] = "base64"

        # ``deepagents.middleware.summarization`` writes this literal into
        # ``additional_kwargs`` and matches on it in ``_is_summary_message``; it
        # exposes no constant to import.
        SUMMARIZATION_SOURCE: Final[str] = "summarization"

        # Content fallbacks for the same middleware's two summary templates,
        # used only when the metadata marker is absent (a checkpoint round-trip
        # that dropped ``additional_kwargs``, or a replayed transcript).
        SUMMARY_CONTENT_PREFIXES: Final[tuple[str, ...]] = (
            "You are in the middle of a conversation that has been summarized.",
            "Here is a summary of the conversation to date:",
        )

        # Head of ``deepagents.middleware.filesystem.TOO_LARGE_HUMAN_MSG``.
        STATE_FILE_CONTENT_PREFIX: Final[str] = (
            "Message content too large and was saved to the filesystem at:"
        )

        # Mirrors ``agent_runtime.context.memory.subagent_trace._PATH_PREFIX``.
        # Duplicated rather than imported: that module pulls
        # ``runtime_api.schemas`` and ``deepagents.backends`` into the import
        # graph, and an observability read must not drag the API layer into
        # every process that measures a message.
        SUBAGENT_TRACE_PATH_PREFIX: Final[str] = "/subagents/"

        # Content-block types that carry reasoning rather than answer text:
        # Anthropic extended thinking (plus its redacted form) and the
        # LangChain-standard / OpenAI Responses reasoning block.
        REASONING_BLOCK_TYPES: Final[frozenset[str]] = frozenset(
            {"thinking", "redacted_thinking", "reasoning"}
        )
        TEXT_BLOCK_TYPE: Final[str] = "text"

    DETAIL_TEMPLATE: Final[str] = "msg[{ordinal}]"
    """Bounded identifier for a part: the message's index in the request.

    An ordinal and nothing else. ``detail`` is served over an HTTP read API
    (§6.5), so it carries position — never a tool name's payload, never a
    content excerpt. Parts of the same message are told apart by their labels;
    a caller aggregating across messages composes an ordinal *range* from these.
    """

    NOTE_MARKERS: Final[tuple[tuple[str, ContextOrigin], ...]] = (
        (
            ToolResultNote.SEPARATOR + CitationHint.NOTE_PREFIX,
            MessageContextOrigins.CITATION_POINTER_NOTE,
        ),
        (
            ToolResultNote.SEPARATOR + ToolBudgetUsage.NOTE_PREFIX,
            MessageContextOrigins.TOOL_BUDGET_NOTE,
        ),
    )
    """The appended-note needles, composed from the producers' own constants.

    ``ToolResultNote.SEPARATOR`` is the exact string both wrappers put between a
    result and its note, so a needle here is byte-identical to what the producer
    wrote. Including the separator in the needle — and therefore in the peeled
    part — is what makes the parts of a split message sum back to the message.
    """

    # The stub prefix ``ToolResultAdmissionAdapter`` writes ahead of an offload
    # reference. Read from the adapter (private though the attribute is) rather
    # than copied: a duplicated header would drift on the first wording change
    # and silently start reporting stubs as ordinary tool results.
    OFFLOAD_STUB_HEADER: Final[str] = ToolResultAdmissionAdapter._OFFLOAD_HEADER  # noqa: SLF001

    NOTE_TERMINATOR: Final[str] = "]"
    MAX_NOTE_CHARS: Final[int] = 512
    """Upper bound on a peeled note, guarding against a forged prefix.

    Both rendered notes are well under 200 characters even with a long MCP tool
    name. Tool output is untrusted and may itself contain ``"\\n\\n[Tool call #"``;
    without a bound, such a string would hand every byte after it to a note
    label. With one, the candidate is rejected and the bytes stay attributed to
    the tool result — an under-count of a small note rather than a wild
    over-count of a large result.
    """

    MAX_NOTES_PER_RESULT: Final[int] = 4
    """Bound on the peel loop: two note kinds today, with headroom, never open."""

    @classmethod
    def classify(cls, messages: Sequence[object]) -> tuple[ClassifiedMessagePart, ...]:
        """Return the attributed parts of ``messages``, in message order.

        Order is the request's own order rather than a canonical sort: the
        caller feeds these into ``ContextSegment``, which canonicalizes on
        construction, and until then "message 12 came after message 11" is the
        information a reader of the split wants.

        Never raises. A message list that is not a sequence, a message whose
        attributes explode on access, and a part that fails validation all
        degrade to recorded rows.
        """

        try:
            ordered = tuple(messages)
        except Exception:  # noqa: BLE001 — an unreadable request measures as empty
            _LOGGER.warning(
                "Could not iterate the message list for occupancy measurement; "
                "reporting no message segments.",
                exc_info=True,
            )
            return ()
        subagent_trace_call_ids = cls._subagent_trace_call_ids(ordered)
        parts: list[ClassifiedMessagePart] = []
        for ordinal, message in enumerate(ordered):
            parts.extend(
                cls._classify_message(
                    message,
                    ordinal=ordinal,
                    subagent_trace_call_ids=subagent_trace_call_ids,
                )
            )
        return tuple(parts)

    # --- dispatch ------------------------------------------------------------

    @classmethod
    def _classify_message(
        cls,
        message: object,
        *,
        ordinal: int,
        subagent_trace_call_ids: frozenset[str],
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Apply §4.6's resolution order to one message, first match wins."""

        detail = cls.DETAIL_TEMPLATE.format(ordinal=ordinal)
        try:
            materialized = cls._materialize(message, detail=detail)
            return (
                cls._binary_parts(materialized)
                or cls._summary_parts(materialized)
                or cls._offload_stub_parts(materialized)
                or cls._tool_result_parts(
                    materialized,
                    subagent_trace_call_ids=subagent_trace_call_ids,
                )
                or cls._assistant_parts(materialized)
                or cls._state_file_parts(materialized)
                or cls._user_parts(materialized)
                or cls._undeclared_parts(materialized)
            )
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _LOGGER.warning(
                "Could not classify message %s for occupancy measurement; "
                "recording an empty UNDECLARED part.",
                detail,
                exc_info=True,
            )
            return (ClassifiedMessagePart.undeclared(text="", detail=detail),)

    # --- rules ---------------------------------------------------------------

    @classmethod
    def _binary_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 1 — base64 payloads, wherever in the request they landed.

        Audit item R: a binary workspace read is handed to the model as base64,
        which is ~1.37× the file's bytes and tokenizes terribly. It is checked
        first because a message carrying one is dominated by it regardless of
        what else the message is.
        """

        encoded = tuple(
            block for block in materialized.blocks if cls._is_base64_block(block)
        )
        if not encoded and not cls._declares_base64_encoding(materialized.message):
            return ()
        return (
            ClassifiedMessagePart.declared(
                MessageContextOrigins.WORKSPACE_BINARY_B64,
                text=materialized.text,
                detail=materialized.detail,
                item_count=max(len(encoded), 1),
            ),
        )

    @classmethod
    def _summary_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 2 — a compaction summary standing in for evicted history.

        Audit item O: summarization is already metered as a model call, but the
        *residency* of its output is not. A summary that keeps growing turn over
        turn is a compaction bug, and it is invisible until this row exists.
        """

        if not cls._is_summary_message(materialized):
            return ()
        return (
            ClassifiedMessagePart.declared(
                MessageContextOrigins.SUMMARY,
                text=materialized.text,
                detail=materialized.detail,
            ),
        )

    @classmethod
    def _offload_stub_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 3 — the bounded preview an oversized result was replaced by.

        Audit item N: compression already happens; what is missing is the
        report. A stub row says "this result was compressed, and here is what
        the compressed form still costs".
        """

        if not cls._is_tool_message(materialized.message):
            return ()
        if not cls._carries_offload_stub(materialized):
            return ()
        return (
            ClassifiedMessagePart.declared(
                MessageContextOrigins.OFFLOAD_STUB,
                text=materialized.text,
                detail=materialized.detail,
            ),
        )

    @classmethod
    def _tool_result_parts(
        cls,
        materialized: MaterializedMessage,
        *,
        subagent_trace_call_ids: frozenset[str],
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 4 — a tool result, with its appended notes split off the tail.

        The split is the point (audit items L and M). The remainder keeps the
        conversation label, or the subagent-trace label when the result came
        back from a ``/subagents/`` read — the same bytes, but ``ON_DEMAND``
        rather than ``PER_RESULT``, because the model chose to pull them in.

        The remainder part is emitted even when it is empty, unless notes were
        peeled: a result that is *only* notes should not report a phantom
        zero-byte result row, but a genuinely empty result should still appear.
        """

        if not cls._is_tool_message(materialized.message):
            return ()
        detail = materialized.detail
        remainder, notes = cls._split_notes(materialized.text)
        origin = (
            MessageContextOrigins.SUBAGENT_TRACE
            if cls._is_subagent_trace_result(
                materialized.message,
                subagent_trace_call_ids=subagent_trace_call_ids,
            )
            else MessageContextOrigins.TOOL_RESULT
        )
        parts: list[ClassifiedMessagePart] = []
        if remainder or not notes:
            parts.append(
                ClassifiedMessagePart.declared(origin, text=remainder, detail=detail)
            )
        parts.extend(
            ClassifiedMessagePart.declared(note_origin, text=note_text, detail=detail)
            for note_origin, note_text in notes
        )
        return tuple(parts)

    @classmethod
    def _assistant_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 5 — thinking, then tool calls, then answer text.

        Three parts rather than one because they behave differently and are
        fixed differently. Thinking blocks are audit item S: they are counted
        once as ``reasoning_tokens`` when emitted, then re-sent as *input* on
        every subsequent call of the turn, which is silent growth nobody is
        billed for visibly. Tool-call arguments are the ``write_file`` payloads
        that compaction truncates first. Answer text is the part a reader
        expects to dominate and usually does not.
        """

        message = materialized.message
        if not isinstance(message, AIMessage):
            return ()
        detail = materialized.detail
        blocks = materialized.blocks
        reasoning_count = sum(1 for block in blocks if cls._is_reasoning_block(block))
        tool_calls = cls._tool_calls(message)
        parts: list[ClassifiedMessagePart] = []
        if reasoning_count:
            parts.append(
                ClassifiedMessagePart.declared(
                    MessageContextOrigins.ASSISTANT_THINKING,
                    text=materialized.text_where(cls._is_reasoning_block),
                    detail=detail,
                    item_count=reasoning_count,
                )
            )
        if tool_calls:
            parts.append(
                ClassifiedMessagePart.declared(
                    MessageContextOrigins.ASSISTANT_TOOL_CALLS,
                    text=cls._tool_calls_text(tool_calls),
                    detail=detail,
                    item_count=len(tool_calls),
                )
            )
        text = (
            materialized.text
            if not blocks
            else materialized.text_where(
                lambda block: not cls._is_reasoning_block(block)
            )
        )
        if text or not parts:
            parts.append(
                ClassifiedMessagePart.declared(
                    MessageContextOrigins.ASSISTANT_TEXT,
                    text=text,
                    detail=detail,
                )
            )
        return tuple(parts)

    @classmethod
    def _state_file_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 6 — Deep Agents state standing in for an evicted message body.

        Audit item P: the framework moves an oversized human message to the
        virtual filesystem and leaves a pointer plus a preview in its place. The
        pointer is small, which is the finding — the same conversation now has a
        file the model can re-read at will, and this row is where a reader sees
        the trade happened at all.
        """

        if not cls._is_state_file_message(materialized):
            return ()
        return (
            ClassifiedMessagePart.declared(
                MessageContextOrigins.STATE_FILE,
                text=materialized.text,
                detail=materialized.detail,
            ),
        )

    @classmethod
    def _user_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """Rule 7 — conversation input, declared by the runtime on its behalf."""

        if not isinstance(materialized.message, HumanMessage):
            return ()
        return (
            ClassifiedMessagePart.declared(
                MessageContextOrigins.USER,
                text=materialized.text,
                detail=materialized.detail,
            ),
        )

    @classmethod
    def _undeclared_parts(
        cls,
        materialized: MaterializedMessage,
    ) -> tuple[ClassifiedMessagePart, ...]:
        """No rule matched — record it as ``UNDECLARED`` rather than absorbing it.

        Messages get no silent catch-all bucket for the same reason tools do
        not: a bucket named after the ledger would make a contract breach look
        like normal operation. A ``SystemMessage`` in the message list, a shape
        a library bump introduced, and a plain dict all land here, visibly, in
        ``undeclared_tokens``.
        """

        return (
            ClassifiedMessagePart.undeclared(
                text=materialized.text,
                detail=materialized.detail,
            ),
        )

    # --- note splitting ------------------------------------------------------

    @classmethod
    def _split_notes(
        cls,
        text: str,
    ) -> tuple[str, tuple[tuple[ContextOrigin, str], ...]]:
        """Peel appended notes off the tail of ``text``, innermost last.

        Peeling from the tail rather than scanning forward is what keeps the
        split exact under the real append order: the budget note is appended by
        the tool-budget guard before admission and the citation pointer by the
        citation wrapper after it, so the last-appended note is the true tail
        and each earlier one becomes the tail once its successor is removed.

        Returns the remainder and the notes in *text* order, so the caller can
        emit parts that reassemble the original by concatenation.
        """

        remainder = text
        peeled: list[tuple[ContextOrigin, str]] = []
        for _ in range(cls.MAX_NOTES_PER_RESULT):
            trailing = cls._trailing_note(remainder)
            if trailing is None:
                break
            index, origin = trailing
            peeled.append((origin, remainder[index:]))
            remainder = remainder[:index]
        peeled.reverse()
        return remainder, tuple(peeled)

    @classmethod
    def _trailing_note(cls, text: str) -> tuple[int, ContextOrigin] | None:
        """Return where the trailing note starts and what declared it, or ``None``.

        A candidate must be the *last* occurrence of a producer's exact needle,
        must close with the bracket every rendered note ends on, and must be
        shorter than :attr:`MAX_NOTE_CHARS`. All three together are what make
        this a structural split rather than a pattern guess over untrusted text.
        """

        best_index = -1
        best_origin: ContextOrigin | None = None
        for needle, origin in cls.NOTE_MARKERS:
            index = text.rfind(needle)
            if index < 0 or index <= best_index:
                continue
            candidate = text[index:]
            if len(candidate) > cls.MAX_NOTE_CHARS:
                continue
            if not candidate.rstrip().endswith(cls.NOTE_TERMINATOR):
                continue
            best_index, best_origin = index, origin
        if best_origin is None:
            return None
        return best_index, best_origin

    # --- shape probes --------------------------------------------------------

    @classmethod
    def _is_tool_message(cls, message: object) -> bool:
        """Whether ``message`` is a tool result the model will read."""

        return isinstance(message, ToolMessage)

    @classmethod
    def _is_base64_block(cls, block: object) -> bool:
        """Whether one content block carries a base64 payload.

        Four shapes, all of them real: the LangChain-standard file/image block
        (``{"base64": ...}``), the ``deepagents`` backend's ``FileData``
        (``{"encoding": "base64"}``), a bare source-typed block, and Anthropic's
        nested ``{"source": {"type": "base64"}}``.
        """

        if not isinstance(block, Mapping):
            return False
        if isinstance(block.get(cls.Keys.BASE64), str):
            return True
        if block.get(cls.Keys.ENCODING) == cls.Markers.BASE64_ENCODING:
            return True
        if block.get(cls.Keys.TYPE) == cls.Markers.BASE64_ENCODING:
            return True
        source = block.get(cls.Keys.SOURCE)
        return (
            isinstance(source, Mapping)
            and source.get(cls.Keys.TYPE) == cls.Markers.BASE64_ENCODING
        )

    @classmethod
    def _declares_base64_encoding(cls, message: object) -> bool:
        """Whether the message's own metadata declares base64 content."""

        metadata = cls._additional_kwargs(message)
        return metadata.get(cls.Keys.ENCODING) == cls.Markers.BASE64_ENCODING

    @classmethod
    def _is_reasoning_block(cls, block: object) -> bool:
        """Whether one content block is thinking rather than answer text."""

        return (
            isinstance(block, Mapping)
            and block.get(cls.Keys.TYPE) in cls.Markers.REASONING_BLOCK_TYPES
        )

    @classmethod
    def _is_summary_message(cls, materialized: MaterializedMessage) -> bool:
        """Whether the message is compaction output rather than conversation.

        The metadata marker is authoritative — it is the same field the
        summarization middleware itself matches on. The content prefixes are the
        forgeable fallback described in the module docstring, and are checked
        only when the metadata is absent.
        """

        metadata = cls._additional_kwargs(materialized.message)
        if metadata.get(cls.Keys.LC_SOURCE) == cls.Markers.SUMMARIZATION_SOURCE:
            return True
        return any(
            materialized.text.startswith(prefix)
            for prefix in cls.Markers.SUMMARY_CONTENT_PREFIXES
        )

    @classmethod
    def _is_state_file_message(cls, materialized: MaterializedMessage) -> bool:
        """Whether the message is a pointer to a body evicted into state."""

        metadata = cls._additional_kwargs(materialized.message)
        if isinstance(metadata.get(cls.Keys.LC_EVICTED_TO), str):
            return True
        return materialized.text.startswith(cls.Markers.STATE_FILE_CONTENT_PREFIX)

    @classmethod
    def _carries_offload_stub(cls, materialized: MaterializedMessage) -> bool:
        """Whether a tool result is an admission stub rather than the result.

        The typed artifact is checked first because it is a fact the admission
        adapter stamped; the header prefix covers the ordinary path where only
        the bounded ``model_content`` string survives into the message.
        """

        artifact = cls._attribute(materialized.message, cls.Keys.ARTIFACT)
        if isinstance(artifact, ToolResultAdmission):
            return True
        return materialized.text.startswith(cls.OFFLOAD_STUB_HEADER)

    @classmethod
    def _is_subagent_trace_result(
        cls,
        message: object,
        *,
        subagent_trace_call_ids: frozenset[str],
    ) -> bool:
        """Whether this result came back from a ``/subagents/`` read.

        Resolved by matching the result's ``tool_call_id`` against the calls the
        assistant actually made, which is exact, rather than by sniffing the
        projected markdown, which would be a guess over untrusted text. The
        binary-read metadata key is checked too because a base64 trace read
        carries its path on the message itself.
        """

        call_id = cls._attribute(message, cls.Keys.TOOL_CALL_ID)
        if isinstance(call_id, str) and call_id in subagent_trace_call_ids:
            return True
        path = cls._additional_kwargs(message).get(cls.Keys.READ_FILE_PATH)
        return isinstance(path, str) and path.startswith(
            cls.Markers.SUBAGENT_TRACE_PATH_PREFIX
        )

    @classmethod
    def _subagent_trace_call_ids(cls, messages: Sequence[object]) -> frozenset[str]:
        """Collect the ids of tool calls that read a ``/subagents/`` path.

        Built once per classification rather than per message: the index is what
        lets rule 4 attribute a result to the trace projection without the
        result itself carrying any evidence of where it came from.
        """

        call_ids: set[str] = set()
        for message in messages:
            try:
                call_ids.update(cls._subagent_trace_calls_of(message))
            except Exception:  # noqa: BLE001 — one bad turn must not blind the index
                _LOGGER.debug(
                    "Could not read tool calls while indexing subagent-trace "
                    "reads; results of that turn measure as ordinary results.",
                    exc_info=True,
                )
        return frozenset(call_ids)

    @classmethod
    def _subagent_trace_calls_of(cls, message: object) -> tuple[str, ...]:
        """Return this turn's call ids whose arguments name a trace path."""

        matched: list[str] = []
        for call in cls._tool_calls(message):
            call_id = call.get(cls.Keys.ID)
            arguments = call.get(cls.Keys.ARGS)
            if not isinstance(call_id, str) or not isinstance(arguments, Mapping):
                continue
            if any(
                isinstance(value, str)
                and value.startswith(cls.Markers.SUBAGENT_TRACE_PATH_PREFIX)
                for value in arguments.values()
            ):
                matched.append(call_id)
        return tuple(matched)

    @classmethod
    def _tool_calls(cls, message: object) -> tuple[Mapping[str, object], ...]:
        """Return the message's tool calls, tolerating any other shape."""

        calls = cls._attribute(message, cls.Keys.TOOL_CALLS)
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
            return ()
        return tuple(call for call in calls if isinstance(call, Mapping))

    @classmethod
    def _additional_kwargs(cls, message: object) -> Mapping[str, object]:
        """Return the message's metadata mapping, or an empty one."""

        metadata = cls._attribute(message, cls.Keys.ADDITIONAL_KWARGS)
        return metadata if isinstance(metadata, Mapping) else {}

    @classmethod
    def _attribute(cls, message: object, name: str) -> object:
        """Read one attribute off an untrusted message, or ``None``.

        Every attribute this classifier reads goes through here. A message
        object is a library type the runtime does not own, and a property that
        raises on access is a real shape — ``getattr`` with a default does not
        cover it, because the default only applies to a *missing* attribute.
        Absent and unreadable are the same answer for measurement: ``None``.
        """

        try:
            return getattr(message, name, None)
        except Exception:  # noqa: BLE001 — unreadable is measured as absent
            _LOGGER.debug(
                "Could not read %r off a message during occupancy measurement; "
                "treating it as absent.",
                name,
                exc_info=True,
            )
            return None

    # --- materialization -----------------------------------------------------

    @classmethod
    def _materialize(cls, message: object, *, detail: str) -> MaterializedMessage:
        """Render ``message`` once into the view every rule reads (§3.4).

        The single place a message's bytes are produced. Blocks are rendered
        individually and then joined, rather than the whole content being
        rendered as one value, so rule 5 can separate thinking from answer text
        by *selecting* already-rendered pieces instead of rendering again.
        """

        content = cls._attribute(message, cls.Keys.CONTENT)
        blocks = cls._blocks_of(content)
        if isinstance(content, str):
            return MaterializedMessage(
                message=message,
                detail=detail,
                blocks=blocks,
                block_texts=(),
                text=content,
            )
        if content is None:
            return MaterializedMessage(
                message=message,
                detail=detail,
                blocks=blocks,
                block_texts=(),
                text="",
            )
        if cls._is_block_container(content):
            block_texts = tuple(cls._block_text(block) for block in blocks)
            return MaterializedMessage(
                message=message,
                detail=detail,
                blocks=blocks,
                block_texts=block_texts,
                text="".join(block_texts),
            )
        return MaterializedMessage(
            message=message,
            detail=detail,
            blocks=blocks,
            block_texts=(),
            text=cls._safe_json(content),
        )

    @classmethod
    def _blocks_of(cls, content: object) -> tuple[object, ...]:
        """Return ``content`` as a block tuple, or empty when it is not blocks.

        Takes the already-read content rather than the message, so
        :meth:`_materialize` touches the ``content`` attribute exactly once. A
        message is a library object whose content may be a computed property;
        reading it twice risks two different answers and buys nothing.

        An empty result means "this message's content is not a block list", not
        "this message is empty" — the rules that care about blocks fall through
        to the whole-message text when it is.
        """

        if isinstance(content, Mapping):
            return (content,)
        if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
            return tuple(content)
        return ()

    @classmethod
    def _is_block_container(cls, content: object) -> bool:
        """Whether ``content`` is a block list or a single block mapping.

        Deciding this is what lets :meth:`_materialize` treat an empty block
        container as nothing rather than as its JSON rendering: a message with
        no blocks occupies no context, and reporting two bytes for ``[]`` would
        make every empty message look like it charged a little rent.
        """

        if isinstance(content, Mapping):
            return True
        return isinstance(content, Sequence) and not isinstance(content, (str, bytes))

    @classmethod
    def _block_text(cls, block: object) -> str:
        """Materialize one content block.

        A text block contributes its text; anything else contributes its
        canonical JSON, which is the closest honest stand-in for what the
        provider is sent and keeps a base64 payload's bytes in the count.
        """

        if isinstance(block, str):
            return block
        if isinstance(block, Mapping):
            if block.get(cls.Keys.TYPE) == cls.Markers.TEXT_BLOCK_TYPE:
                text = block.get(cls.Keys.TEXT)
                if isinstance(text, str):
                    return text
            return cls._safe_json(block)
        return cls._safe_json(block)

    @classmethod
    def _tool_calls_text(cls, tool_calls: Sequence[Mapping[str, object]]) -> str:
        """Materialize the tool-call block as the provider receives it.

        Only ``id``, ``name`` and ``args`` — the three fields that cross the
        wire. LangChain's ``type`` discriminator and any adapter bookkeeping are
        local to the object and would inflate the count with bytes the provider
        never sees.
        """

        return cls._safe_json(
            [
                {
                    cls.Keys.ID: call.get(cls.Keys.ID),
                    cls.Keys.NAME: call.get(cls.Keys.NAME),
                    cls.Keys.ARGS: call.get(cls.Keys.ARGS),
                }
                for call in tool_calls
            ]
        )

    JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")

    @classmethod
    def _safe_json(cls, value: object) -> str:
        """Render ``value`` deterministically, degrading rather than raising.

        Keys are sorted and separators are tight so two identical requests
        materialize to identical bytes and the §3.4 digest memoization can hit,
        and ``ensure_ascii`` is off so a non-ASCII character measures as the
        UTF-8 bytes a provider actually receives rather than as a ``\\uXXXX``
        escape roughly twice its width.

        Deliberately **not** ``surfaces_v2.canonical_json``, despite that being
        the repository's other deterministic renderer. That one enforces a
        cross-language digest contract — it *raises* on tuples, bytes,
        non-finite floats, arbitrary objects, and integers outside JavaScript's
        exact range. Message content is untrusted and genuinely carries all of
        those, so every such block would pay a full pure-Python walk only to
        raise and fall back to ``repr``, which is neither faster nor closer to
        what crosses the wire. Nothing here is compared against a TypeScript
        rendering, so that contract buys this module nothing and costs it ~8× per
        block. ``default=str`` keeps the unmodelled types rendering as JSON
        instead of aborting the whole message.

        The ``str`` fallback still covers what ``json`` itself refuses — a cycle,
        or mixed-type dict keys that cannot be sorted — and a value that refuses
        that too measures as empty: a worse number, never a failed run.
        """

        try:
            return json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                separators=cls.JSON_SEPARATORS,
                default=str,
            )
        except Exception:  # noqa: BLE001 — the fallback is the error handling
            _LOGGER.debug(
                "Could not render message content as JSON for occupancy "
                "measurement; falling back to its string form.",
                exc_info=True,
            )
        try:
            return str(value)
        except Exception:  # noqa: BLE001 — an unreadable value measures as empty
            return ""


__all__ = (
    "ClassifiedMessagePart",
    "ContextMessageClassifier",
    "MaterializedMessage",
    "MessageContextOrigins",
)
