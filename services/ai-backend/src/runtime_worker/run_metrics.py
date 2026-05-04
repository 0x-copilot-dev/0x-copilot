"""Assistant response metrics collected during runtime execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from agent_runtime.execution.contracts import JsonObject
from agent_runtime.persistence.records import RuntimeRunUsageRecord
from runtime_api.schemas import (
    AssistantPerformanceMetrics,
    AssistantUsageMetrics,
    RunRecord,
)


class TokenUsageExtractor:
    """Extract token usage from LangChain AIMessage objects and raw mappings.

    Prefers the native ``usage_metadata`` attribute on AIMessage (LangChain >=0.2)
    before falling back to ``response_metadata`` and common mapping shapes.
    """

    class _Fields:
        USAGE_METADATA = "usage_metadata"
        RESPONSE_METADATA = "response_metadata"
        USAGE = "usage"
        TOKEN_USAGE = "token_usage"
        INPUT_TOKENS = "input_tokens"
        OUTPUT_TOKENS = "output_tokens"
        TOTAL_TOKENS = "total_tokens"
        PROMPT_TOKENS = "prompt_tokens"
        COMPLETION_TOKENS = "completion_tokens"
        PROMPT_TOKEN_COUNT = "prompt_token_count"
        COMPLETION_TOKEN_COUNT = "completion_token_count"
        TOTAL_TOKEN_COUNT = "total_token_count"
        INPUT_TOKEN_DETAILS = "input_token_details"
        PROMPT_TOKENS_DETAILS = "prompt_tokens_details"
        CACHE_READ = "cache_read"
        CACHED_TOKENS = "cached_tokens"

    _USAGE_KEYS = frozenset(
        {
            _Fields.INPUT_TOKENS,
            _Fields.OUTPUT_TOKENS,
            _Fields.TOTAL_TOKENS,
            _Fields.PROMPT_TOKENS,
            _Fields.COMPLETION_TOKENS,
            _Fields.PROMPT_TOKEN_COUNT,
            _Fields.COMPLETION_TOKEN_COUNT,
            _Fields.TOTAL_TOKEN_COUNT,
        }
    )

    @classmethod
    def extract(cls, value: object) -> tuple[Mapping[str, object], ...]:
        """Return token-usage mappings found on *value*.

        Uses ``usage_metadata`` directly when available (the LangChain-native
        path), then falls back to ``response_metadata`` and common dict shapes.
        Walks one level into mapping values and sequence items to find usage on
        nested objects (e.g. stream chunk envelopes wrapping AIMessages).
        """
        candidates: list[Mapping[str, object]] = []
        cls._extract_from_object(value, candidates)
        if candidates:
            return tuple(candidates)

        if isinstance(value, Mapping):
            for item in value.values():
                cls._extract_from_object(item, candidates)
                if isinstance(item, Sequence) and not isinstance(
                    item, (str, bytes, bytearray)
                ):
                    for sub in item:
                        cls._extract_from_object(sub, candidates)
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                cls._extract_from_object(item, candidates)

        return tuple(candidates)

    @classmethod
    def _extract_from_object(
        cls,
        value: object,
        candidates: list[Mapping[str, object]],
    ) -> None:
        usage_meta = getattr(value, cls._Fields.USAGE_METADATA, None)
        if usage_meta is None and isinstance(value, Mapping):
            usage_meta = value.get(cls._Fields.USAGE_METADATA)
        if isinstance(usage_meta, Mapping) and cls._looks_like_usage(usage_meta):
            candidates.append({str(k): v for k, v in usage_meta.items()})
            return

        response_meta = getattr(value, cls._Fields.RESPONSE_METADATA, None)
        if response_meta is None and isinstance(value, Mapping):
            response_meta = value.get(cls._Fields.RESPONSE_METADATA)
        if isinstance(response_meta, Mapping):
            normalized = {str(k): v for k, v in response_meta.items()}
            cls._append_if_usage(normalized.get(cls._Fields.TOKEN_USAGE), candidates)
            cls._append_if_usage(normalized.get(cls._Fields.USAGE), candidates)
            cls._append_if_usage(normalized, candidates)

        for attr in (cls._Fields.USAGE, cls._Fields.TOKEN_USAGE):
            sub = getattr(value, attr, None)
            if sub is None and isinstance(value, Mapping):
                sub = value.get(attr)
            cls._append_if_usage(sub, candidates)

        if isinstance(value, Mapping):
            normalized = {str(k): v for k, v in value.items()}
            if cls._looks_like_usage(normalized):
                candidates.append(normalized)

    @classmethod
    def _append_if_usage(
        cls,
        value: object,
        candidates: list[Mapping[str, object]],
    ) -> None:
        if not isinstance(value, Mapping):
            return
        normalized = {str(k): v for k, v in value.items()}
        if cls._looks_like_usage(normalized):
            candidates.append(normalized)

    @classmethod
    def _looks_like_usage(cls, value: Mapping[str, object]) -> bool:
        return any(key in value for key in cls._USAGE_KEYS)


class AssistantRunMetrics:
    """Accumulate timing, chunk, and exact provider usage for one assistant run."""

    PERFORMANCE_KEY = "performance_metrics"

    class _Fields:
        INPUT_TOKENS = "input_tokens"
        OUTPUT_TOKENS = "output_tokens"
        TOTAL_TOKENS = "total_tokens"
        PROMPT_TOKENS = "prompt_tokens"
        COMPLETION_TOKENS = "completion_tokens"
        PROMPT_TOKEN_COUNT = "prompt_token_count"
        COMPLETION_TOKEN_COUNT = "completion_token_count"
        TOTAL_TOKEN_COUNT = "total_token_count"
        INPUT_TOKEN_DETAILS = "input_token_details"
        PROMPT_TOKENS_DETAILS = "prompt_tokens_details"
        CACHE_READ = "cache_read"
        CACHED_TOKENS = "cached_tokens"

    def __init__(self, *, started_at: datetime) -> None:
        self.started_at = started_at
        self.first_token_at: datetime | None = None
        self.chunk_count = 0
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.total_tokens: int | None = None
        self.cached_input_tokens: int | None = None

    @classmethod
    def from_run(cls, run: RunRecord) -> "AssistantRunMetrics":
        """Create metrics from the persisted run start timestamp."""

        return cls(started_at=run.started_at or datetime.now(timezone.utc))

    def record_model_delta(self, delta: str) -> None:
        """Record a non-empty top-level model text chunk."""

        if delta == "":
            return
        now = datetime.now(timezone.utc)
        self.chunk_count += 1
        if self.first_token_at is None:
            self.first_token_at = now

    def record_usage_from(self, value: object) -> None:
        """Capture exact provider token usage when present in stream objects."""

        for usage in TokenUsageExtractor.extract(value):
            self._merge_usage(usage)

    def to_payload(self, *, completed_at: datetime | None = None) -> JsonObject:
        """Return the public JSON metrics payload."""

        end = completed_at or datetime.now(timezone.utc)
        duration_ms = self._duration_ms(self.started_at, end)
        first_token_ms = (
            self._duration_ms(self.started_at, self.first_token_at)
            if self.first_token_at is not None
            else None
        )
        output_per_second = self._tokens_per_second(
            output_tokens=self.output_tokens,
            duration_ms=duration_ms,
        )
        usage = self._usage_payload(output_per_second=output_per_second)
        return AssistantPerformanceMetrics(
            started_at=self.started_at,
            completed_at=end,
            duration_ms=duration_ms,
            chunk_count=self.chunk_count,
            first_chunk_at=self.first_token_at,
            first_chunk_ms=first_token_ms,
            usage=usage,
        ).model_dump(mode="json", exclude_none=True)

    @classmethod
    def metadata(cls, metrics: JsonObject) -> JsonObject:
        """Return the assistant message/event metadata wrapper."""

        return {cls.PERFORMANCE_KEY: metrics}

    @classmethod
    def with_payload(cls, payload: JsonObject, metrics: JsonObject) -> JsonObject:
        """Attach metrics to an existing event payload."""

        return {**payload, cls.PERFORMANCE_KEY: metrics}

    def to_usage_record(
        self,
        run: RunRecord,
        *,
        completed_at: datetime,
        status: str,
    ) -> RuntimeRunUsageRecord:
        """Build the per-run usage row at ``RUN_COMPLETED`` time (B1).

        Reads from the same accumulator that backs ``to_payload`` so the
        denormalized row and the event payload always agree. Token fields
        fall back to 0 when the provider didn't report usage (the row is
        still useful for ``runs_count`` / latency aggregates).
        """

        duration_ms = self._duration_ms(self.started_at, completed_at)
        first_token_ms = (
            self._duration_ms(self.started_at, self.first_token_at)
            if self.first_token_at is not None
            else None
        )
        return RuntimeRunUsageRecord(
            id=run.run_id,
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            assistant_id=getattr(run.runtime_context, "assistant_id", None),
            model_provider=run.model_provider,
            model_name=run.model_name,
            input_tokens=self.input_tokens or 0,
            output_tokens=self.output_tokens or 0,
            cached_input_tokens=self.cached_input_tokens or 0,
            total_tokens=self.total_tokens
            or (self.input_tokens or 0) + (self.output_tokens or 0),
            chunk_count=self.chunk_count,
            first_token_ms=first_token_ms,
            duration_ms=duration_ms,
            started_at=self.started_at,
            completed_at=completed_at,
            status=status,
            created_at=completed_at,
        )

    def _usage_payload(
        self,
        *,
        output_per_second: float | None,
    ) -> AssistantUsageMetrics | None:
        if (
            self.input_tokens is None
            and self.output_tokens is None
            and self.total_tokens is None
            and self.cached_input_tokens is None
            and output_per_second is None
        ):
            return None
        return AssistantUsageMetrics(
            input=self.input_tokens,
            output=self.output_tokens,
            total=self.total_tokens,
            cached_input=self.cached_input_tokens,
            output_per_second=output_per_second,
        )

    def _merge_usage(self, usage: Mapping[str, object]) -> None:
        input_tokens = self._token_value(
            usage,
            self._Fields.INPUT_TOKENS,
            self._Fields.PROMPT_TOKENS,
            self._Fields.PROMPT_TOKEN_COUNT,
        )
        output_tokens = self._token_value(
            usage,
            self._Fields.OUTPUT_TOKENS,
            self._Fields.COMPLETION_TOKENS,
            self._Fields.COMPLETION_TOKEN_COUNT,
        )
        total_tokens = self._token_value(
            usage,
            self._Fields.TOTAL_TOKENS,
            self._Fields.TOTAL_TOKEN_COUNT,
        )
        cached_input_tokens = self._cached_input_tokens(usage)

        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens
        if total_tokens is not None:
            self.total_tokens = total_tokens
        elif input_tokens is not None and output_tokens is not None:
            self.total_tokens = input_tokens + output_tokens
        if cached_input_tokens is not None:
            self.cached_input_tokens = cached_input_tokens

    @classmethod
    def _token_value(
        cls,
        value: Mapping[str, object],
        *keys: str,
    ) -> int | None:
        for key in keys:
            token_count = cls._non_negative_int(value.get(key))
            if token_count is not None:
                return token_count
        return None

    @classmethod
    def _cached_input_tokens(cls, value: Mapping[str, object]) -> int | None:
        for key in (
            cls._Fields.INPUT_TOKEN_DETAILS,
            cls._Fields.PROMPT_TOKENS_DETAILS,
        ):
            details = value.get(key)
            if not isinstance(details, Mapping):
                continue
            normalized = {str(item_key): item for item_key, item in details.items()}
            cached = cls._token_value(
                normalized,
                cls._Fields.CACHE_READ,
                cls._Fields.CACHED_TOKENS,
            )
            if cached is not None:
                return cached
        return None

    @staticmethod
    def _non_negative_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value >= 0 and value.is_integer():
            return int(value)
        return None

    @staticmethod
    def _duration_ms(started_at: datetime, completed_at: datetime | None) -> int:
        if completed_at is None:
            return 0
        return max(0, round((completed_at - started_at).total_seconds() * 1000))

    @staticmethod
    def _tokens_per_second(
        *,
        output_tokens: int | None,
        duration_ms: int,
    ) -> float | None:
        if output_tokens is None or duration_ms <= 0:
            return None
        return round(output_tokens / (duration_ms / 1000), 2)
