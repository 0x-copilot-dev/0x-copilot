"""Post-run worker job that proposes memory items / routines / atlas-cron
schedules from a completed conversation (team-memory-cmdk-prd §9).

Scope:

- Runs **off** the request handler. The orchestrator (post-run lifecycle)
  decides when to enqueue a job; this module owns only the extraction
  pass.
- Every LLM call routes through the canonical
  :func:`agent_runtime.execution.deep_agent_builder.build_chat_model` —
  TU-1's static guard (``tools/check_llm_provider_imports.py``) refuses
  any direct provider SDK import here.
- The job constructs a :class:`UsageAttributionContext` with
  ``purpose=Purpose.MEMORY_EXTRACTION`` and hands the resulting
  :class:`RuntimeModelCallUsageRecord` to the injected :class:`UsageRecorder`.
- Returned proposals are tuples of typed records; the caller decides
  where to persist them (the worker's run-completed lifecycle hook owns
  the wiring to the memory / routine / atlas-cron stores). Minimal
  coupling — this module is a pure extractor, not a store writer.

PII / content discipline:

- Proposal bodies are content — never logged at INFO or above. The job
  only logs structural counts (``candidates``, ``persisted``) and run
  identifiers.

Cost cap:

- A per-run budget (default $0.001 ≈ 1k input tokens at small-model
  prices, sub-PRD §9) is checked **before** the LLM call. When a
  caller-supplied estimator says the call would exceed the cap we skip
  the extractor for this run with a single info log. The cap is a
  configurable parameter at construction time.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.observability.attribution import Purpose, UsageAttributionContext
from agent_runtime.observability.usage_recorder import UsageRecorder
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord


_LOGGER = logging.getLogger(__name__)


class _Defaults:
    """Class-namespaced constants — single source for the extractor's caps."""

    # Cap how many messages of the transcript we feed to the model.
    # 1k input tokens budget means we can keep at most ~30 short messages.
    TRANSCRIPT_MESSAGE_LIMIT = 30

    # Per-kind cap on what we return. Defends against a wedged model
    # returning hundreds of items and flooding the user's pending list.
    MAX_PROPOSALS_PER_KIND = 5

    # Per-run dollar budget (sub-PRD §9 — $0.001 per run with a small
    # model at ≈ 1k input tokens). Configurable at construction time.
    COST_CAP_USD_PER_RUN = 0.001

    # Rough price per 1k input tokens for the small model. Used by the
    # default estimator to decide whether the call fits the budget.
    DEFAULT_INPUT_USD_PER_1K_TOKENS = 0.001

    # Max length of any single proposed_text we'll return. We truncate
    # rather than reject so a long suggestion still yields a usable row.
    MAX_PROPOSED_TEXT_LEN = 2000

    # Max chars in the rendered transcript before we trim from the head.
    # Bound on the input-token estimator's worst case.
    MAX_TRANSCRIPT_CHARS = 6000


class _ExtractionPrompt:
    """System + user prompt template for the proposal extractor LLM call.

    The JSON contract is intentionally narrow:

    .. code-block:: text

       {
         "memories": [
           {"kind": "fact"|"preference"|"skill",
            "title": "<short>",
            "body": "<body>",
            "confidence": 0.0..1.0}
         ],
         "routines": [
           {"title": "<imperative>",
            "trigger_hint": "<schedule>"|null,
            "confidence": 0.0..1.0}
         ],
         "atlas_crons": [
           {"title": "<imperative>",
            "cadence_hint": "<weekly>"|null,
            "confidence": 0.0..1.0}
         ]
       }

    Anything else (extra fields, free-form prose around the JSON, no JSON
    at all) is treated as "no candidates".
    """

    SYSTEM = (
        "You scan a chat transcript for: (1) durable memories the user "
        "expressed (facts, preferences, skills), (2) repeatable workflows "
        "that could be saved as a routine, and (3) cadenced tasks that "
        "could be scheduled. Return ONLY a JSON object with keys "
        "`memories`, `routines`, `atlas_crons`. Each item carries "
        "`title`, optional kind-specific fields, and `confidence` "
        "(0.0-1.0). Use `[]` for any kind with no candidates. Do not "
        "invent items; only surface what the transcript explicitly "
        "supports."
    )

    USER_PREFIX = "Transcript (oldest first):\n\n"

    @classmethod
    def build_user_content(cls, transcript_lines: Sequence[str]) -> str:
        """Render the transcript portion of the user message, head-trimmed."""
        body = "\n".join(transcript_lines)
        if len(body) > _Defaults.MAX_TRANSCRIPT_CHARS:
            # Trim from the head — recent exchanges carry the strongest
            # signal for memory / routine extraction.
            body = body[-_Defaults.MAX_TRANSCRIPT_CHARS :]
        return cls.USER_PREFIX + body


# ---------------------------------------------------------------------------
# Typed proposal records — returned to the caller; the caller decides
# which store(s) to write them into.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryProposal:
    """A durable memory the user expressed (fact / preference / skill)."""

    title: str
    body: str
    kind: str  # "fact" | "preference" | "skill"
    confidence: float


@dataclass(frozen=True)
class RoutineProposal:
    """A repeatable workflow the conversation suggests we could save."""

    title: str
    trigger_hint: str | None
    confidence: float


@dataclass(frozen=True)
class AtlasCronSuggestion:
    """A cadenced task suitable for the Routines scheduler (Q9)."""

    title: str
    cadence_hint: str | None
    confidence: float


@dataclass(frozen=True)
class ProposalExtractionResult:
    """Outcome of one extraction pass.

    Empty tuples whenever the model returned nothing usable OR the
    cost-cap caused the extractor to skip. ``skipped_reason`` records
    why we bailed; ``None`` means we actually ran the LLM call.
    """

    run_id: str
    memories: tuple[MemoryProposal, ...] = ()
    routines: tuple[RoutineProposal, ...] = ()
    atlas_crons: tuple[AtlasCronSuggestion, ...] = ()
    skipped_reason: str | None = None

    @property
    def total_count(self) -> int:
        return len(self.memories) + len(self.routines) + len(self.atlas_crons)


# ---------------------------------------------------------------------------
# Ports — the extractor only needs to read messages. The persistence /
# store-writing concern is the caller's; this module is a pure extractor.
# ---------------------------------------------------------------------------


@runtime_checkable
class _MessageReader(Protocol):
    """Minimal protocol so tests can hand in a fake directly."""

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[object]: ...


class CostEstimator(Protocol):
    """Cheap pre-call estimator. Returns the projected USD cost of
    invoking the model on the rendered prompt.

    Production wires a deterministic estimator based on the model price
    sheet; the default below uses a flat ``$0.001 / 1k input tokens``
    rate which matches the small-model assumption in sub-PRD §9.
    """

    def estimate_usd(self, *, prompt_chars: int) -> float: ...


class _FlatRateCostEstimator:
    """Default — assume 1 token ≈ 4 chars and a flat per-1k input rate."""

    def __init__(self, *, usd_per_1k_input_tokens: float) -> None:
        self._rate = max(0.0, float(usd_per_1k_input_tokens))

    def estimate_usd(self, *, prompt_chars: int) -> float:
        approx_tokens = max(0, prompt_chars) / 4.0
        return self._rate * (approx_tokens / 1000.0)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class ProposalExtractor:
    """Run-scoped proposal extractor. Construct once per call site (cheap)."""

    def __init__(
        self,
        *,
        persistence: _MessageReader,
        usage_recorder: UsageRecorder,
        model_config: ModelConfig,
        cost_cap_usd_per_run: float = _Defaults.COST_CAP_USD_PER_RUN,
        cost_estimator: CostEstimator | None = None,
        clock: Any = None,
    ) -> None:
        self._persistence = persistence
        self._usage_recorder = usage_recorder
        self._model_config = model_config
        self._cost_cap_usd = max(0.0, float(cost_cap_usd_per_run))
        self._cost_estimator = cost_estimator or _FlatRateCostEstimator(
            usd_per_1k_input_tokens=_Defaults.DEFAULT_INPUT_USD_PER_1K_TOKENS
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def cost_cap_usd_per_run(self) -> float:
        """Configured per-run dollar budget; tests rely on this attribute."""
        return self._cost_cap_usd

    async def extract(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        run_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> ProposalExtractionResult:
        """Run one extraction pass.

        Never raises on model / JSON failure — returns an empty result
        with ``skipped_reason`` set when the extractor bails out.
        """

        transcript_lines = await self._read_transcript(
            org_id=org_id, conversation_id=conversation_id
        )
        if not transcript_lines:
            return ProposalExtractionResult(
                run_id=run_id, skipped_reason="empty_transcript"
            )

        prompt_user = _ExtractionPrompt.build_user_content(transcript_lines)
        prompt_chars = len(prompt_user) + len(_ExtractionPrompt.SYSTEM)
        projected_cost = self._cost_estimator.estimate_usd(prompt_chars=prompt_chars)
        if projected_cost > self._cost_cap_usd:
            _LOGGER.info(
                "proposal_extraction_budget_exceeded",
                extra={
                    "metadata": {
                        "run_id": run_id,
                        "projected_usd": round(projected_cost, 6),
                        "cap_usd": round(self._cost_cap_usd, 6),
                    }
                },
            )
            return ProposalExtractionResult(
                run_id=run_id, skipped_reason="cost_cap_exceeded"
            )

        started = self._clock()
        started_perf = time.perf_counter()
        try:
            response = await self._invoke_model(prompt_user)
        except Exception:
            # Provider / network failure — the run already completed; we
            # do not fail it because the proposal pass tripped. Log
            # without content and bail clean.
            _LOGGER.warning(
                "proposal_extraction_llm_failed",
                extra={"metadata": {"run_id": run_id}},
                exc_info=True,
            )
            return ProposalExtractionResult(run_id=run_id, skipped_reason="llm_failed")
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

        memories, routines, crons = self._parse_response(response.content)
        _LOGGER.info(
            "proposal_extraction_persisted",
            extra={
                "metadata": {
                    "run_id": run_id,
                    "memory_count": len(memories),
                    "routine_count": len(routines),
                    "cron_count": len(crons),
                }
            },
        )
        return ProposalExtractionResult(
            run_id=run_id,
            memories=memories,
            routines=routines,
            atlas_crons=crons,
        )

    # -- internals ----------------------------------------------------------

    async def _read_transcript(
        self, *, org_id: str, conversation_id: str
    ) -> tuple[str, ...]:
        """Read messages, project to ``role: text`` lines."""
        messages = await self._persistence.list_messages(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=_Defaults.TRANSCRIPT_MESSAGE_LIMIT,
        )
        lines: list[str] = []
        for message in messages:
            role = getattr(message, "role", None)
            text = getattr(message, "content_text", "") or ""
            if not text.strip():
                continue
            role_value = getattr(role, "value", role) or "user"
            lines.append(f"{role_value}: {text}")
        return tuple(lines)

    async def _invoke_model(self, user_content: str) -> object:
        """Build the chat model and run one ainvoke."""
        model = build_chat_model(self._model_config)
        return await model.ainvoke(
            [
                SystemMessage(content=_ExtractionPrompt.SYSTEM),
                HumanMessage(content=user_content),
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
        """Build attribution context, the call record, hand to recorder."""

        attribution = UsageAttributionContext(
            org_id=org_id,
            user_id=owner_user_id,
            run_id=run_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            purpose=Purpose.MEMORY_EXTRACTION,
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
        # ``started`` referenced so the linter doesn't flag it; ``completed``
        # is the pricing anchor (not ``started``) per UsageRecorder contract.
        _ = started

    def _parse_response(
        self, content: object
    ) -> tuple[
        tuple[MemoryProposal, ...],
        tuple[RoutineProposal, ...],
        tuple[AtlasCronSuggestion, ...],
    ]:
        """Parse the model's content into three lists of typed proposals.

        Tolerant: malformed JSON / non-object payload / missing keys all
        coalesce to empty tuples without raising.
        """
        text = self._extract_text(content)
        if not text:
            return ((), (), ())
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ((), (), ())
        if not isinstance(parsed, dict):
            return ((), (), ())

        memories = self._parse_memories(parsed.get("memories"))
        routines = self._parse_routines(parsed.get("routines"))
        crons = self._parse_atlas_crons(parsed.get("atlas_crons"))
        return memories, routines, crons

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

    @classmethod
    def _parse_memories(cls, raw: object) -> tuple[MemoryProposal, ...]:
        if not isinstance(raw, list):
            return ()
        out: list[MemoryProposal] = []
        for item in raw[: _Defaults.MAX_PROPOSALS_PER_KIND]:
            if not isinstance(item, dict):
                continue
            title = cls._coerce_text(item.get("title"))
            body = cls._coerce_text(item.get("body"))
            kind_raw = item.get("kind")
            kind = (
                kind_raw.strip().lower()
                if isinstance(kind_raw, str) and kind_raw.strip()
                else "fact"
            )
            if kind not in {"fact", "preference", "skill"}:
                kind = "fact"
            if not title:
                continue
            out.append(
                MemoryProposal(
                    title=title,
                    body=body,
                    kind=kind,
                    confidence=cls._coerce_confidence(item.get("confidence")),
                )
            )
        return tuple(out)

    @classmethod
    def _parse_routines(cls, raw: object) -> tuple[RoutineProposal, ...]:
        if not isinstance(raw, list):
            return ()
        out: list[RoutineProposal] = []
        for item in raw[: _Defaults.MAX_PROPOSALS_PER_KIND]:
            if not isinstance(item, dict):
                continue
            title = cls._coerce_text(item.get("title"))
            if not title:
                continue
            trigger_raw = item.get("trigger_hint")
            trigger = (
                trigger_raw.strip()
                if isinstance(trigger_raw, str) and trigger_raw.strip()
                else None
            )
            out.append(
                RoutineProposal(
                    title=title,
                    trigger_hint=trigger,
                    confidence=cls._coerce_confidence(item.get("confidence")),
                )
            )
        return tuple(out)

    @classmethod
    def _parse_atlas_crons(cls, raw: object) -> tuple[AtlasCronSuggestion, ...]:
        if not isinstance(raw, list):
            return ()
        out: list[AtlasCronSuggestion] = []
        for item in raw[: _Defaults.MAX_PROPOSALS_PER_KIND]:
            if not isinstance(item, dict):
                continue
            title = cls._coerce_text(item.get("title"))
            if not title:
                continue
            cad_raw = item.get("cadence_hint")
            cadence = (
                cad_raw.strip()
                if isinstance(cad_raw, str) and cad_raw.strip()
                else None
            )
            out.append(
                AtlasCronSuggestion(
                    title=title,
                    cadence_hint=cadence,
                    confidence=cls._coerce_confidence(item.get("confidence")),
                )
            )
        return tuple(out)

    @staticmethod
    def _coerce_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        trimmed = value.strip()
        if not trimmed:
            return ""
        if len(trimmed) > _Defaults.MAX_PROPOSED_TEXT_LEN:
            return trimmed[: _Defaults.MAX_PROPOSED_TEXT_LEN]
        return trimmed

    @staticmethod
    def _coerce_confidence(value: object) -> float:
        try:
            score = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Usage helpers — mirror todo_extractor._UsageExtractor.
# ---------------------------------------------------------------------------


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
        usage = getattr(message, "usage_metadata", None) or {}
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


# Silence "unused" warning on the dataclass-only field decorator import path.
_ = field
