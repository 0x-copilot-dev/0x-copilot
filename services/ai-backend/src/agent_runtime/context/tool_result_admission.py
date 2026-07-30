"""Bound tool results before they are admitted to model context.

The worker's stream projector is too late to protect a model invocation:
LangGraph has already converted a tool return into a ``ToolMessage`` by the
time stream events are observed.  This module provides the reusable, runtime
owned boundary that a tool wrapper can call immediately after execution and
before returning to LangChain.

The adapter deliberately does not retain the raw serialized result.  Large
content is handed to the existing ``OffloadWriter`` and the returned object
contains only a bounded preview, an opaque read-back reference, and redacted
compression metrics.  The same object can also drive the worker event
projection without running a second, potentially divergent offload decision.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from hashlib import sha256
import json
from threading import Lock
from typing import Annotated, ClassVar, Self

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator
from pydantic_core import to_jsonable_python

from agent_runtime.context.context_contracts import (
    ContextCandidateKind,
    ContextInclusionReason,
    ContextLossiness,
    ContextPriorityClass,
    ContextRepresentationMode,
)
from agent_runtime.context.memory.contracts import (
    ContextCompressionEvent,
    ContextCompressionStrategy,
    TokenBudgetPolicy,
)
from agent_runtime.context.memory.summarization import (
    ContextPayloadManager,
    OffloadWriter,
)
from agent_runtime.context.memory.token_budget import TokenBudgetEvaluator
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.redactor import Sensitive, SensitiveCategory


class ToolResultAdmissionRejected(ValueError):
    """An admission fact did not state what admitting a result must state."""

    class Messages:
        """Safe public messages for admission-fact refusals."""

        LIMIT_EXCEEDED: ClassVar[str] = (
            "admitted model content exceeds its own admission limit"
        )
        MODE_NOT_THE_STRATEGY: ClassVar[str] = (
            "the representation mode must be the one this strategy produces"
        )
        LOSSINESS_NOT_ADMITTED: ClassVar[str] = (
            "this representation mode does not admit the declared lossiness"
        )
        REASON_NOT_THE_STRATEGY: ClassVar[str] = (
            "the inclusion reason must be the one this strategy produces"
        )
        OFFLOAD_REQUIRES_REF: ClassVar[str] = (
            "an offloaded result must carry the reference it defers to"
        )
        INLINE_CANNOT_REFER: ClassVar[str] = (
            "an inline result cannot carry an offload reference"
        )
        INLINE_MUST_BE_THE_SOURCE: ClassVar[str] = (
            "an inline result must digest to its own source"
        )
        OFFLOAD_MUST_BE_DIFFERENT: ClassVar[str] = (
            "an offloaded result cannot digest to its whole source"
        )
        PRIORITY_PROMOTED: ClassVar[str] = (
            "a tool observation cannot claim that priority class"
        )
        NAIVE_TIMESTAMP: ClassVar[str] = "admission timestamps must be timezone-aware"


class ToolResultAdmissionFact(RuntimeContract):
    """The one raw-free record of what admitting a tool result decided.

    This is the fact the model path and the event path both *read* instead of
    each deriving its own.  Two layers observing the same tool return used to
    reach the offload decision twice -- once to bound the value handed to the
    model, once to project a persisted tool event -- and two derivations of one
    decision are two decisions that can disagree.  Recording the decision once,
    keyed by the call it was made for, makes the event path a lookup rather than
    a second, potentially divergent budgeting pass.

    Nothing in it is content.  Every field is a lowercase SHA-256 digest, a
    closed vocabulary member from :mod:`agent_runtime.context.context_contracts`,
    a count, a bounded whitespace-free reference, or a timezone-aware timestamp.
    There is no field through which a tool result, a preview, or a serialized
    payload could enter, and the reference is length-bounded rather than trusted,
    so a store that issued a content-shaped ref cannot smuggle a body through the
    one field that names bytes the runtime kept.  The property is enforced by the
    accepted shapes rather than by review, and
    :mod:`tests.unit.agent_runtime.context.test_tool_result_admission_gate`
    proves it by seeding a secret into the admitted result rather than by reading
    field names.

    The F5.1 vocabularies are consumed, never restated.  An inline admission is
    the whole source (``full``/``none``); an offloaded one hands the model a
    bounded, explicitly non-authoritative preview plus the reference that reads
    the exact bytes back (``reference``/``elided``).  The candidate kind is fixed
    at :attr:`ContextCandidateKind.TOOL_OBSERVATION` and the priority class is
    *derived* from that kind's ceiling, so a tool result can never acquire the
    tier that holds immutable safety and protocol context by being recorded.
    """

    MAX_REF_CHARS: ClassVar[int] = 256
    MAX_IDENTIFIER_CHARS: ClassVar[int] = 128

    tool_call_id: str = Field(min_length=1, max_length=MAX_IDENTIFIER_CHARS)
    tool_name: str = Field(min_length=1, max_length=MAX_IDENTIFIER_CHARS)
    candidate_kind: ContextCandidateKind = ContextCandidateKind.TOOL_OBSERVATION
    priority_class: ContextPriorityClass
    strategy: ContextCompressionStrategy
    representation_mode: ContextRepresentationMode
    lossiness: ContextLossiness
    inclusion_reason: ContextInclusionReason
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_chars: NonNegativeInt
    source_tokens: NonNegativeInt
    model_content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_content_chars: NonNegativeInt
    model_content_limit_chars: PositiveInt
    model_content_tokens: NonNegativeInt
    output_ref: str | None = Field(default=None, max_length=MAX_REF_CHARS)
    admitted_at: datetime

    @classmethod
    def representation_for(
        cls,
        strategy: ContextCompressionStrategy,
    ) -> tuple[
        ContextRepresentationMode,
        ContextLossiness,
        ContextInclusionReason,
    ]:
        """Return the one F5.1 reading each admission strategy produces.

        Derived here rather than chosen by a caller so the mapping is an
        equality check on parse instead of three fields a construction site
        could fill in inconsistently.  ``recency_window`` is the honest
        inclusion reason for an inline result -- a just-executed tool return is
        the current turn's observation, not material that won a relevance
        contest -- and ``retrievable_reference`` is the honest one for a result
        the model was handed a reference to.
        """

        if strategy is ContextCompressionStrategy.OFFLOAD:
            return (
                ContextRepresentationMode.REFERENCE,
                ContextLossiness.ELIDED,
                ContextInclusionReason.RETRIEVABLE_REFERENCE,
            )
        return (
            ContextRepresentationMode.FULL,
            ContextLossiness.NONE,
            ContextInclusionReason.RECENCY_WINDOW,
        )

    @classmethod
    def bounded_identifier(cls, value: str, *, limit: int | None = None) -> str:
        """Return ``value`` if it is a bounded single token, else its digest.

        Total on purpose.  A name or reference this bound cannot hold is
        replaced by a digest of itself rather than rejected, because refusing to
        record the fact would leave the admission unrecorded -- and an
        unrecorded admission is exactly the hole this lane closes.  The bound is
        what makes the field structurally unable to hold prose.
        """

        ceiling = cls.MAX_IDENTIFIER_CHARS if limit is None else limit
        candidate = value.strip()
        if (
            candidate
            and len(candidate) <= ceiling
            and not any(
                character.isspace() or ord(character) < 0x20 for character in candidate
            )
        ):
            return candidate
        return "sha256:" + sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def for_admitted_content(
        cls,
        *,
        tool_name: str,
        tool_call_id: str,
        strategy: ContextCompressionStrategy,
        model_content: str,
        model_content_limit_chars: int,
        source_digest: str,
        source_chars: int,
        source_tokens: int,
        output_ref: str | None,
        admitted_at: datetime | None = None,
    ) -> Self:
        """Record one admission decision without retaining what it decided about."""

        mode, lossiness, reason = cls.representation_for(strategy)
        kind = ContextCandidateKind.TOOL_OBSERVATION
        return cls(
            tool_call_id=cls.bounded_identifier(tool_call_id),
            tool_name=cls.bounded_identifier(tool_name),
            candidate_kind=kind,
            priority_class=kind.max_priority_class(),
            strategy=strategy,
            representation_mode=mode,
            lossiness=lossiness,
            inclusion_reason=reason,
            source_digest=source_digest,
            source_chars=source_chars,
            source_tokens=source_tokens,
            model_content_digest=sha256(model_content.encode("utf-8")).hexdigest(),
            model_content_chars=len(model_content),
            model_content_limit_chars=max(model_content_limit_chars, 1),
            model_content_tokens=TokenBudgetEvaluator.estimate_tokens(model_content),
            output_ref=(
                None
                if output_ref is None
                else cls.bounded_identifier(output_ref, limit=cls.MAX_REF_CHARS)
            ),
            admitted_at=admitted_at or datetime.now(UTC),
        )

    @model_validator(mode="after")
    def _fact_states_one_consistent_decision(self) -> Self:
        if self.model_content_chars > self.model_content_limit_chars:
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.LIMIT_EXCEEDED
            )
        mode, lossiness, reason = self.representation_for(self.strategy)
        if self.representation_mode is not mode:
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.MODE_NOT_THE_STRATEGY
            )
        if not self.representation_mode.admits_lossiness(self.lossiness):
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.LOSSINESS_NOT_ADMITTED
            )
        if self.lossiness is not lossiness or self.inclusion_reason is not reason:
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.REASON_NOT_THE_STRATEGY
            )
        if not self.priority_class.no_more_protected_than(
            self.candidate_kind.max_priority_class()
        ):
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.PRIORITY_PROMOTED
            )
        if self.admitted_at.tzinfo is None or self.admitted_at.utcoffset() is None:
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.NAIVE_TIMESTAMP
            )
        self._check_reference_matches_strategy()
        return self

    def _check_reference_matches_strategy(self) -> None:
        if self.strategy is ContextCompressionStrategy.OFFLOAD:
            if self.output_ref is None:
                raise ToolResultAdmissionRejected(
                    ToolResultAdmissionRejected.Messages.OFFLOAD_REQUIRES_REF
                )
            if self.model_content_digest == self.source_digest:
                raise ToolResultAdmissionRejected(
                    ToolResultAdmissionRejected.Messages.OFFLOAD_MUST_BE_DIFFERENT
                )
            return
        if self.output_ref is not None:
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.INLINE_CANNOT_REFER
            )
        if self.model_content_digest != self.source_digest:
            raise ToolResultAdmissionRejected(
                ToolResultAdmissionRejected.Messages.INLINE_MUST_BE_THE_SOURCE
            )


class ToolResultAdmission(RuntimeContract):
    """A raw-free representation safe to hand to the model boundary.

    ``model_content_limit_chars`` is carried with the value, rather than
    remaining implicit in the adapter, so consumers can assert the admission
    invariant without access to configuration.  ``source_digest`` identifies
    the offloaded/inline bytes for diagnostics without retaining them.
    """

    strategy: ContextCompressionStrategy
    model_content: Annotated[
        str,
        Sensitive(SensitiveCategory.MODEL_OUTPUT),
    ]
    model_content_limit_chars: PositiveInt
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_ref: str | None = None
    preview: Annotated[
        str | None,
        Sensitive(SensitiveCategory.MODEL_OUTPUT),
    ] = None
    event: ContextCompressionEvent
    fact: ToolResultAdmissionFact | None = None

    @model_validator(mode="after")
    def _validate_admission_invariants(self) -> "ToolResultAdmission":
        if len(self.model_content) > self.model_content_limit_chars:
            raise ValueError("model_content exceeds its admission limit")
        if self.fact is not None and (
            self.fact.strategy is not self.strategy
            or self.fact.source_digest != self.source_digest
            or self.fact.model_content_digest
            != sha256(self.model_content.encode("utf-8")).hexdigest()
        ):
            raise ValueError("admission fact does not describe this admission")
        if self.event.strategy is not self.strategy:
            raise ValueError("event strategy must match admission strategy")
        if self.strategy is ContextCompressionStrategy.OFFLOAD:
            if self.output_ref is None:
                raise ValueError("offloaded tool results require output_ref")
            if self.preview is None:
                raise ValueError("offloaded tool results require a bounded preview")
        elif self.output_ref is not None or self.preview is not None:
            raise ValueError("inline tool results cannot carry offload fields")
        return self

    @property
    def event_content(self) -> str:
        """Return the compact content suitable for a persisted tool event."""

        if self.strategy is ContextCompressionStrategy.OFFLOAD:
            return self.preview or ""
        return self.model_content


class ToolResultAdmissionAdapter:
    """Serialize, budget, and offload one tool return before model admission."""

    DEFAULT_INLINE_TOKEN_BUDGET = 8_000
    DEFAULT_OFFLOADED_MODEL_CONTENT_LIMIT_CHARS = 4_096
    _RECENT_CONTEXT_RATIO = 0.25
    _OFFLOAD_HEADER = (
        "Oversized tool result offloaded before model admission.\n"
        "Read the reference for exact content; the preview is non-authoritative.\n"
    )

    def __init__(
        self,
        offload_writer: OffloadWriter,
        *,
        policy: TokenBudgetPolicy | None = None,
        offloaded_model_content_limit_chars: int = (
            DEFAULT_OFFLOADED_MODEL_CONTENT_LIMIT_CHARS
        ),
    ) -> None:
        if offloaded_model_content_limit_chars < 1_024:
            raise ValueError(
                "offloaded model content limit must be at least 1024 chars"
            )
        self._offload_writer = offload_writer
        self._policy = policy or TokenBudgetPolicy(
            max_input_tokens=int(
                self.DEFAULT_INLINE_TOKEN_BUDGET / self._RECENT_CONTEXT_RATIO
            ),
            recent_context_ratio=self._RECENT_CONTEXT_RATIO,
        )
        self._offloaded_model_content_limit_chars = offloaded_model_content_limit_chars
        # A model-bound result and its streamed ToolMessage are observed at two
        # different runtime layers.  Keep only the already-bounded admission
        # value between those layers so the worker can project the exact
        # decision into its durable event without serializing, budgeting, or
        # offloading the result a second time.
        self._pending_projections: dict[tuple[str, str], deque[ToolResultAdmission]] = (
            defaultdict(deque)
        )
        self._projection_lock = Lock()

    def admit(
        self,
        output: object,
        *,
        trace_id: str,
        projection_key: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> ToolResultAdmission:
        """Return a representation that contains no oversized raw result.

        The writer is invoked synchronously before this method returns.  A
        writer failure is intentionally propagated: falling back to returning
        the unbounded result would violate the model-admission contract.

        ``projection_key`` is the opaque run id shared with the worker stream
        projector.  When supplied, the adapter retains only this bounded
        admission value until the matching ToolMessage is projected.  Raw tool
        output is never retained.

        ``tool_name`` and ``tool_call_id`` identify the call this decision was
        made for.  When the call is identified, the returned admission carries
        the one raw-free :class:`ToolResultAdmissionFact` describing it, so the
        event path can read the decision instead of budgeting the result a
        second time.  They are optional because the decision itself does not
        depend on them: an unidentified call is bounded exactly the same way,
        it simply has no fact to file.
        """

        content = self.serialize(output)
        source_digest = sha256(content.encode("utf-8")).hexdigest()
        if not content:
            event = ContextCompressionEvent(
                before_tokens=0,
                after_tokens=0,
                strategy=ContextCompressionStrategy.INLINE,
                trace_id=trace_id,
                metadata={"mode": ContextCompressionStrategy.INLINE.value},
            )
            return self._admitted(
                strategy=ContextCompressionStrategy.INLINE,
                model_content="",
                model_content_limit_chars=1,
                source_digest=source_digest,
                source_chars=0,
                source_tokens=0,
                event=event,
                projection_key=projection_key,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )

        managed = ContextPayloadManager.prepare_tool_output(
            content=content,
            policy=self._policy,
            trace_id=trace_id,
            offload_writer=self._offload_writer,
        )
        if managed.strategy is ContextCompressionStrategy.INLINE:
            return self._admitted(
                strategy=managed.strategy,
                model_content=managed.content or "",
                model_content_limit_chars=max(
                    self.inline_token_budget
                    * TokenBudgetEvaluator.CHARS_PER_TOKEN_ESTIMATE,
                    1,
                ),
                source_digest=source_digest,
                source_chars=len(content),
                source_tokens=managed.event.before_tokens,
                event=managed.event,
                projection_key=projection_key,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        if managed.strategy is not ContextCompressionStrategy.OFFLOAD:
            raise RuntimeError(
                "tool result admission requires inline or offload representation"
            )

        reference = managed.reference or ""
        prefix = f"{self._OFFLOAD_HEADER}Reference: {reference}\nPreview:\n"
        preview_limit = max(
            self._offloaded_model_content_limit_chars - len(prefix),
            0,
        )
        preview = (managed.preview or "")[:preview_limit]
        model_content = f"{prefix}{preview}"
        return self._admitted(
            strategy=managed.strategy,
            model_content=model_content,
            model_content_limit_chars=self._offloaded_model_content_limit_chars,
            source_digest=source_digest,
            source_chars=len(content),
            source_tokens=managed.event.before_tokens,
            output_ref=reference,
            preview=preview,
            event=managed.event,
            projection_key=projection_key,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )

    def _admitted(
        self,
        *,
        strategy: ContextCompressionStrategy,
        model_content: str,
        model_content_limit_chars: int,
        source_digest: str,
        source_chars: int,
        source_tokens: int,
        event: ContextCompressionEvent,
        projection_key: str | None,
        tool_name: str | None,
        tool_call_id: str | None,
        output_ref: str | None = None,
        preview: str | None = None,
    ) -> ToolResultAdmission:
        """Build one admission and, when the call is identified, its one fact.

        Every branch of :meth:`admit` returns through here, so the fact cannot
        exist for some admission paths and be missing from others -- which is
        the shape the "covers only some tools" bug takes when the recording is
        written per call site instead of at the single boundary.
        """

        fact = (
            None
            if tool_call_id is None
            else ToolResultAdmissionFact.for_admitted_content(
                tool_name=tool_name or "unknown_tool",
                tool_call_id=tool_call_id,
                strategy=strategy,
                model_content=model_content,
                model_content_limit_chars=model_content_limit_chars,
                source_digest=source_digest,
                source_chars=source_chars,
                source_tokens=source_tokens,
                output_ref=output_ref,
            )
        )
        return self._retain_for_projection(
            ToolResultAdmission(
                strategy=strategy,
                model_content=model_content,
                model_content_limit_chars=model_content_limit_chars,
                source_digest=source_digest,
                output_ref=output_ref,
                preview=preview,
                event=event,
                fact=fact,
            ),
            projection_key=projection_key,
        )

    def consume_projection(
        self,
        output: object,
        *,
        projection_key: str,
    ) -> ToolResultAdmission | None:
        """Consume the model-bound admission matching one streamed ToolMessage.

        Parallel tools may legitimately produce byte-identical results, so each
        digest owns a FIFO rather than a single value.  The run key prevents
        identical output from concurrent runs sharing a worker from colliding.
        """

        model_content = self.serialize(output)
        digest = sha256(model_content.encode("utf-8")).hexdigest()
        key = (projection_key, digest)
        with self._projection_lock:
            pending = self._pending_projections.get(key)
            if not pending:
                return None
            admission = pending.popleft()
            if not pending:
                self._pending_projections.pop(key, None)
            return admission

    def discard_projections(self, *, projection_key: str) -> None:
        """Discard bounded, unprojected values when a run exits early."""

        with self._projection_lock:
            stale = [
                key for key in self._pending_projections if key[0] == projection_key
            ]
            for key in stale:
                self._pending_projections.pop(key, None)

    def _retain_for_projection(
        self,
        admission: ToolResultAdmission,
        *,
        projection_key: str | None,
    ) -> ToolResultAdmission:
        if projection_key is None:
            return admission
        digest = sha256(admission.model_content.encode("utf-8")).hexdigest()
        with self._projection_lock:
            self._pending_projections[(projection_key, digest)].append(admission)
        return admission

    @staticmethod
    def serialize(output: object) -> str:
        """Serialize a tool return deterministically for budgeting and storage."""

        if isinstance(output, str):
            return output
        json_safe = to_jsonable_python(
            output,
            bytes_mode="base64",
            serialize_unknown=True,
            fallback=str,
        )
        return json.dumps(
            json_safe,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def inline_token_budget(self) -> int:
        """Expose the effective inline threshold for diagnostics and tests."""

        return TokenBudgetEvaluator.snapshot(
            policy=self._policy,
            current_tokens=0,
        ).recent_context_tokens


__all__ = (
    "ToolResultAdmission",
    "ToolResultAdmissionAdapter",
    "ToolResultAdmissionFact",
    "ToolResultAdmissionRejected",
)
