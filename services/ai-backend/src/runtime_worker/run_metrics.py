"""Assistant response metrics collected during runtime execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from agent_runtime.execution.contracts import JsonObject
from runtime_api.schemas import (
    AssistantPerformanceMetrics,
    AssistantUsageMetrics,
    RunRecord,
)


class AssistantRunMetrics:
    """Accumulate timing, chunk, and exact provider usage for one assistant run."""

    PERFORMANCE_KEY = "performance_metrics"

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

        return cls(started_at=run.started_at or datetime.now(UTC))

    def record_model_delta(self, delta: str) -> None:
        """Record a non-empty top-level model text chunk."""

        if delta == "":
            return
        now = datetime.now(UTC)
        self.chunk_count += 1
        if self.first_token_at is None:
            self.first_token_at = now

    def record_usage_from(self, value: object) -> None:
        """Capture exact provider token usage when present in stream objects."""

        for usage in self._usage_candidates(value):
            self._merge_usage(usage)

    def to_payload(self, *, completed_at: datetime | None = None) -> JsonObject:
        """Return the public JSON metrics payload."""

        end = completed_at or datetime.now(UTC)
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
            "input_tokens",
            "prompt_tokens",
            "prompt_token_count",
        )
        output_tokens = self._token_value(
            usage,
            "output_tokens",
            "completion_tokens",
            "completion_token_count",
        )
        total_tokens = self._token_value(
            usage,
            "total_tokens",
            "total_token_count",
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
    def _usage_candidates(cls, value: object) -> tuple[Mapping[str, object], ...]:
        candidates: list[Mapping[str, object]] = []
        cls._collect_usage_candidates(value, candidates, seen=set())
        return tuple(candidates)

    @classmethod
    def _collect_usage_candidates(
        cls,
        value: object,
        candidates: list[Mapping[str, object]],
        seen: set[int],
    ) -> None:
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)

        if isinstance(value, Mapping):
            cls._collect_usage_from_mapping(value, candidates, seen)
            return

        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for item in value:
                cls._collect_usage_candidates(item, candidates, seen)
            return

        cls._collect_usage_from_object(value, candidates, seen)

    @classmethod
    def _collect_usage_from_mapping(
        cls,
        value: Mapping[object, object],
        candidates: list[Mapping[str, object]],
        seen: set[int],
    ) -> None:
        normalized = {str(key): item for key, item in value.items()}
        cls._append_usage_mapping(normalized.get("usage_metadata"), candidates)
        response_metadata = normalized.get("response_metadata")
        if isinstance(response_metadata, Mapping):
            response = {str(key): item for key, item in response_metadata.items()}
            cls._append_usage_mapping(response.get("token_usage"), candidates)
            cls._append_usage_mapping(response.get("usage"), candidates)
            cls._append_usage_mapping(response, candidates)
        cls._append_usage_mapping(normalized.get("usage"), candidates)
        cls._append_usage_mapping(normalized.get("token_usage"), candidates)
        if cls._looks_like_usage(normalized):
            candidates.append(normalized)
        for item in normalized.values():
            cls._collect_usage_candidates(item, candidates, seen)

    @classmethod
    def _collect_usage_from_object(
        cls,
        value: object,
        candidates: list[Mapping[str, object]],
        seen: set[int],
    ) -> None:
        for attr in ("usage_metadata", "response_metadata", "usage", "token_usage"):
            if not hasattr(value, attr):
                continue
            item = getattr(value, attr)
            cls._append_usage_mapping(item, candidates)
            cls._collect_usage_candidates(item, candidates, seen)

    @classmethod
    def _append_usage_mapping(
        cls,
        value: object,
        candidates: list[Mapping[str, object]],
    ) -> None:
        if not isinstance(value, Mapping):
            return
        normalized = {str(key): item for key, item in value.items()}
        if cls._looks_like_usage(normalized):
            candidates.append(normalized)

    @classmethod
    def _looks_like_usage(cls, value: Mapping[str, object]) -> bool:
        return any(
            key in value
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "prompt_token_count",
                "completion_token_count",
                "total_token_count",
            )
        )

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
        for key in ("input_token_details", "prompt_tokens_details"):
            details = value.get(key)
            if not isinstance(details, Mapping):
                continue
            normalized = {str(item_key): item for item_key, item in details.items()}
            cached = cls._token_value(
                normalized,
                "cache_read",
                "cached_tokens",
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
