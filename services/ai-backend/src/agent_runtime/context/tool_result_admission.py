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
from hashlib import sha256
import json
from threading import Lock
from typing import Annotated

from pydantic import Field, PositiveInt, model_validator
from pydantic_core import to_jsonable_python

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

    @model_validator(mode="after")
    def _validate_admission_invariants(self) -> "ToolResultAdmission":
        if len(self.model_content) > self.model_content_limit_chars:
            raise ValueError("model_content exceeds its admission limit")
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
    ) -> ToolResultAdmission:
        """Return a representation that contains no oversized raw result.

        The writer is invoked synchronously before this method returns.  A
        writer failure is intentionally propagated: falling back to returning
        the unbounded result would violate the model-admission contract.

        ``projection_key`` is the opaque run id shared with the worker stream
        projector.  When supplied, the adapter retains only this bounded
        admission value until the matching ToolMessage is projected.  Raw tool
        output is never retained.
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
            return self._retain_for_projection(
                ToolResultAdmission(
                    strategy=ContextCompressionStrategy.INLINE,
                    model_content="",
                    model_content_limit_chars=1,
                    source_digest=source_digest,
                    event=event,
                ),
                projection_key=projection_key,
            )

        managed = ContextPayloadManager.prepare_tool_output(
            content=content,
            policy=self._policy,
            trace_id=trace_id,
            offload_writer=self._offload_writer,
        )
        if managed.strategy is ContextCompressionStrategy.INLINE:
            return self._retain_for_projection(
                ToolResultAdmission(
                    strategy=managed.strategy,
                    model_content=managed.content or "",
                    model_content_limit_chars=max(
                        self.inline_token_budget
                        * TokenBudgetEvaluator.CHARS_PER_TOKEN_ESTIMATE,
                        1,
                    ),
                    source_digest=source_digest,
                    event=managed.event,
                ),
                projection_key=projection_key,
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
        return self._retain_for_projection(
            ToolResultAdmission(
                strategy=managed.strategy,
                model_content=model_content,
                model_content_limit_chars=self._offloaded_model_content_limit_chars,
                source_digest=source_digest,
                output_ref=reference,
                preview=preview,
                event=managed.event,
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


__all__ = ("ToolResultAdmission", "ToolResultAdmissionAdapter")
