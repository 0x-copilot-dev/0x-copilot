"""Post-run worker job that proposes todos from a completed conversation.

Scope (todos-prd §2.1 / §3.7):

- The job runs **off** the request handler. The orchestrator (P3-A1) decides
  when to enqueue a job (today: post-run, once per terminal run). This module
  owns only the extraction pass and its persistence.
- Every LLM call routes through the canonical
  :func:`agent_runtime.execution.deep_agent_builder.build_chat_model` — TU-1's
  static guard (``tools/check_llm_provider_imports.py``) refuses any direct
  provider SDK import here.
- The job constructs a :class:`UsageAttributionContext` with
  ``purpose=Purpose.TODO_EXTRACTION`` and hands the resulting
  :class:`RuntimeModelCallUsageRecord` to the injected :class:`UsageRecorder`.
  No second tracker is introduced — the existing recorder is the single
  source of truth for token-usage rows.
- Proposals are durable: they land in the ``todo_extractions`` table the
  moment the LLM call returns. A crash between the LLM response and the
  insert leaves no orphan rows; a crash after the insert leaves real rows
  the user can still accept/reject.

The model is asked to return a strict JSON list — see :class:`_ExtractionPrompt`
for the contract. Malformed output is treated as "0 candidates" rather than
raised: extraction is best-effort, not load-bearing for the run lifecycle.

PII / content discipline:

- ``proposed_text`` is content — never logged at INFO or above. The job only
  logs structural counts (``candidates``, ``persisted``) and run identifiers.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from agent_runtime.api.ports import PersistencePort
from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.observability.attribution import Purpose, UsageAttributionContext
from agent_runtime.observability.usage_recorder import UsageRecorder
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    TodoExtractionRecord,
    TodoExtractionState,
)


_LOGGER = logging.getLogger(__name__)

# Cap how many messages of the transcript we feed to the model. Extraction
# is action-item focused, not summarization — the last few exchanges carry
# almost all the usable signal and the input cost stays bounded.
_TRANSCRIPT_MESSAGE_LIMIT = 40

# Hard cap on candidates we'll persist from one run. Defends against a
# wedged model returning hundreds of items and flooding the user's
# pending-proposal list.
_MAX_CANDIDATES_PER_RUN = 10

# Max length of any single proposed_text we'll persist. Mirrors the
# pydantic constraint on TodoExtractionRecord; we truncate at the worker
# boundary so a long suggestion gives a usable row instead of a hard fail.
_MAX_PROPOSED_TEXT_LEN = 2000


class _ExtractionPrompt:
    """System + user prompt template for the extractor LLM call.

    Kept inline (constant strings only) per the staff-engineer guidance —
    a 10-line prompt does not warrant a separate package. The JSON
    contract is intentionally narrow:

    .. code-block:: text

       [
         {
           "text": "<imperative action item, ~80 chars>",
           "due": "YYYY-MM-DD" | null,
           "confidence": 0.0 .. 1.0
         },
         ...
       ]

    Anything else (extra fields, free-form prose around the JSON, no JSON
    at all) is treated as "no candidates".
    """

    SYSTEM = (
        "You scan a chat transcript for concrete action items the user or "
        "their teammate committed to. Return ONLY a JSON array. Each entry "
        "has `text` (imperative, ~80 chars), optional `due` "
        "(YYYY-MM-DD or null), and `confidence` (0.0-1.0). Return [] when "
        "nothing actionable is present. Do not invent tasks; only surface "
        "what the transcript explicitly proposes."
    )

    USER_PREFIX = "Transcript (oldest first):\n\n"

    @classmethod
    def build_user_content(cls, transcript_lines: Sequence[str]) -> str:
        """Render the transcript portion of the user message."""
        return cls.USER_PREFIX + "\n".join(transcript_lines)


@runtime_checkable
class _MessageReader(Protocol):
    """Minimal protocol so tests can hand in a list directly."""

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[object]: ...


@dataclass(frozen=True)
class TodoExtractionResult:
    """Outcome of one extraction pass."""

    run_id: str
    candidate_count: int
    persisted: tuple[TodoExtractionRecord, ...]

    @property
    def persisted_count(self) -> int:
        return len(self.persisted)


class TodoExtractor:
    """Run-scoped extractor. Construct once per call site (cheap)."""

    def __init__(
        self,
        *,
        persistence: PersistencePort | _MessageReader,
        extraction_store: object,
        usage_recorder: UsageRecorder,
        model_config: ModelConfig,
        clock: callable | None = None,
    ) -> None:
        # ``persistence`` only needs ``list_messages``; we accept either the
        # full PersistencePort or a narrowed reader so the test surface is
        # tiny and isolated.
        self._persistence = persistence
        self._extraction_store = extraction_store
        self._usage_recorder = usage_recorder
        self._model_config = model_config
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def extract(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        run_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> TodoExtractionResult:
        """Run one extraction pass and persist any proposals.

        Returns a typed :class:`TodoExtractionResult`. Never raises on
        model / JSON failure — empty result with ``candidate_count=0``.
        """

        transcript_lines, source_message_id = await self._read_transcript(
            org_id=org_id, conversation_id=conversation_id
        )
        if not transcript_lines:
            return TodoExtractionResult(run_id=run_id, candidate_count=0, persisted=())

        started = self._clock()
        started_perf = time.perf_counter()
        try:
            response = await self._invoke_model(transcript_lines)
        except Exception:
            # Provider / network failure — the run already completed; we
            # do not fail it because the proposal pass tripped. Log
            # without content and bail clean.
            _LOGGER.warning(
                "todo_extraction_llm_failed",
                extra={"metadata": {"run_id": run_id}},
                exc_info=True,
            )
            return TodoExtractionResult(run_id=run_id, candidate_count=0, persisted=())
        completed = self._clock()

        await self._record_usage(
            org_id=org_id,
            owner_user_id=owner_user_id,
            run_id=run_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            response=response,
            started=started,
            completed=completed,
            duration_ms=int((time.perf_counter() - started_perf) * 1000),
        )

        candidates = self._parse_candidates(response.content)
        if not candidates:
            return TodoExtractionResult(run_id=run_id, candidate_count=0, persisted=())

        records = self._build_records(
            org_id=org_id,
            owner_user_id=owner_user_id,
            run_id=run_id,
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            candidates=candidates,
            created_at=completed,
        )
        persisted = await self._extraction_store.insert_many(records)
        _LOGGER.info(
            "todo_extraction_persisted",
            extra={
                "metadata": {
                    "run_id": run_id,
                    "candidates": len(candidates),
                    "persisted": len(persisted),
                }
            },
        )
        return TodoExtractionResult(
            run_id=run_id,
            candidate_count=len(candidates),
            persisted=tuple(persisted),
        )

    # -- internals ----------------------------------------------------------

    async def _read_transcript(
        self, *, org_id: str, conversation_id: str
    ) -> tuple[tuple[str, ...], str | None]:
        """Read messages and project them to ``role: text`` lines.

        Returns ``(lines, last_assistant_message_id_or_None)``. The last
        assistant message id is preserved so persisted proposals can
        deep-link back to the excerpt they came from.
        """
        messages = await self._persistence.list_messages(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=_TRANSCRIPT_MESSAGE_LIMIT,
        )
        lines: list[str] = []
        last_assistant_id: str | None = None
        for message in messages:
            role = getattr(message, "role", None)
            text = getattr(message, "content_text", "") or ""
            if not text.strip():
                continue
            role_value = getattr(role, "value", role) or "user"
            lines.append(f"{role_value}: {text}")
            if str(role_value) == "assistant":
                last_assistant_id = getattr(message, "message_id", None)
        return tuple(lines), last_assistant_id

    async def _invoke_model(self, transcript_lines: Sequence[str]) -> object:
        """Build the chat model and run one ainvoke. Returns the LangChain message."""
        model = build_chat_model(self._model_config)
        return await model.ainvoke(
            [
                SystemMessage(content=_ExtractionPrompt.SYSTEM),
                HumanMessage(
                    content=_ExtractionPrompt.build_user_content(transcript_lines)
                ),
            ]
        )

    async def _record_usage(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        run_id: str,
        conversation_id: str,
        trace_id: str,
        response: object,
        started: datetime,
        completed: datetime,
        duration_ms: int,
    ) -> None:
        """Build the attribution context, the call record, hand to recorder."""

        # Build a Purpose.TODO_EXTRACTION attribution context. The existing
        # invariants permit this combination: subagent_slug/task_id are
        # only required for SUBAGENT_WORK; originating_tool_* only for
        # TOOL_INTERPRETATION.
        attribution = UsageAttributionContext(
            org_id=org_id,
            user_id=owner_user_id,
            run_id=run_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            purpose=Purpose.TODO_EXTRACTION,
        )
        usage = _UsageExtractor.from_message(response)
        record = RuntimeModelCallUsageRecord(
            id=uuid4().hex,
            org_id=attribution.org_id,
            run_id=attribution.run_id,
            conversation_id=attribution.conversation_id,
            trace_id=attribution.trace_id,
            model_provider=self._model_config.provider,
            model_name=self._model_config.model_name,
            purpose=attribution.purpose.value,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            total_tokens=usage.total_tokens,
            duration_ms=duration_ms,
        )
        await self._usage_recorder.record_call(record, pricing_at=completed)
        # ``started`` is referenced here so the linter doesn't flag it; it
        # also serves as a self-documenting invariant: ``completed`` is
        # the pricing anchor (not ``started``) per UsageRecorder contract.
        _ = started

    def _parse_candidates(self, content: object) -> tuple[dict[str, object], ...]:
        """Parse the model's content into a list of dict candidates.

        Accepts:

        - ``content`` is a string starting at ``[`` (canonical case).
        - ``content`` is a list of content parts (LangChain providers) —
          we concatenate their ``text`` fields.
        Anything else, or any JSON parse error, returns ``()``.
        """
        text = self._extract_text(content)
        if not text:
            return ()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, list):
            return ()
        candidates: list[dict[str, object]] = []
        for item in parsed[:_MAX_CANDIDATES_PER_RUN]:
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if not isinstance(text_value, str) or not text_value.strip():
                continue
            candidates.append(item)
        return tuple(candidates)

    @staticmethod
    def _extract_text(content: object) -> str:
        """Reduce LangChain content variations to a single string."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "".join(parts).strip()
        return ""

    def _build_records(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        run_id: str,
        conversation_id: str,
        source_message_id: str | None,
        candidates: Sequence[dict[str, object]],
        created_at: datetime,
    ) -> tuple[TodoExtractionRecord, ...]:
        """Translate candidate dicts into validated persistence records.

        Per-candidate failures (bad confidence, bad due, oversized text)
        are tolerated by truncation/coercion where safe; rejection only
        happens when ``text`` itself is empty.
        """
        records: list[TodoExtractionRecord] = []
        for candidate in candidates:
            text = str(candidate.get("text", "")).strip()
            if not text:
                continue
            if len(text) > _MAX_PROPOSED_TEXT_LEN:
                text = text[:_MAX_PROPOSED_TEXT_LEN]
            due_raw = candidate.get("due")
            due = due_raw if isinstance(due_raw, str) and due_raw.strip() else None
            confidence_raw = candidate.get("confidence", 0.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            try:
                record = TodoExtractionRecord(
                    org_id=org_id,
                    owner_user_id=owner_user_id,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    proposed_text=text,
                    suggested_due=due,
                    source_message_id=source_message_id,
                    confidence_score=confidence,
                    state=TodoExtractionState.PENDING,
                    created_at=created_at,
                )
            except ValueError:
                # Validation failure (most commonly: bad due-date shape).
                # Skip this candidate rather than failing the batch.
                continue
            records.append(record)
        return tuple(records)


@dataclass(frozen=True)
class _NormalizedCallUsage:
    """Token-kind subset used by the extractor's usage record."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class _UsageExtractor:
    """Pull token counts off a LangChain AIMessage in a tolerant way."""

    @classmethod
    def from_message(cls, message: object) -> _NormalizedCallUsage:
        """Read usage from ``usage_metadata`` / ``response_metadata``; default to zeros."""
        usage = getattr(message, "usage_metadata", None) or {}
        # LangChain's normalized usage_metadata uses ``input_tokens`` /
        # ``output_tokens``. Providers that miss this still expose totals
        # under ``response_metadata['token_usage']``; we read both.
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_input_tokens = 0
        input_details = usage.get("input_token_details") or {}
        if isinstance(input_details, dict):
            cached_input_tokens = int(input_details.get("cache_read", 0) or 0)
        if input_tokens == 0 and output_tokens == 0:
            fallback = getattr(message, "response_metadata", {}) or {}
            token_usage = fallback.get("token_usage") or {}
            if isinstance(token_usage, dict):
                input_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
                output_tokens = int(token_usage.get("completion_tokens", 0) or 0)
        return _NormalizedCallUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
