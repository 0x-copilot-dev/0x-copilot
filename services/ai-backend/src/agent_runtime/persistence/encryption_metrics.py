"""OpenTelemetry metrics for C7 field encryption.

No-op fallback when OTel isn't installed/configured, mirroring
``backend.token_vault_metrics`` so tests don't have to spin up a meter
provider.
"""

from __future__ import annotations

from typing import Any


_METER_NAME = "ai_backend.field_encryption"


class _NoopRecorder:
    def record_op(
        self,
        *,
        op: str,
        table: str,
        outcome: str,
    ) -> None:
        return

    def record_kms_call(self, *, op: str, outcome: str) -> None:
        return

    def record_cache(self, *, outcome: str) -> None:
        return

    def record_backfill_rows(self, *, table: str, count: int) -> None:
        return


class _OtelRecorder:
    def __init__(
        self,
        *,
        op_counter: Any,
        kms_counter: Any,
        cache_counter: Any,
        backfill_counter: Any,
    ) -> None:
        self._op_counter = op_counter
        self._kms_counter = kms_counter
        self._cache_counter = cache_counter
        self._backfill_counter = backfill_counter

    def record_op(self, *, op: str, table: str, outcome: str) -> None:
        self._op_counter.add(1, {"op": op, "table": table, "outcome": outcome})

    def record_kms_call(self, *, op: str, outcome: str) -> None:
        self._kms_counter.add(1, {"op": op, "outcome": outcome})

    def record_cache(self, *, outcome: str) -> None:
        self._cache_counter.add(1, {"outcome": outcome})

    def record_backfill_rows(self, *, table: str, count: int) -> None:
        self._backfill_counter.add(count, {"table": table})


class FieldEncryptionMetrics:
    _recorder: Any = None

    @classmethod
    def recorder(cls) -> Any:
        if cls._recorder is not None:
            return cls._recorder
        cls._recorder = cls._build()
        return cls._recorder

    @classmethod
    def reset_for_testing(cls) -> None:
        cls._recorder = None

    @classmethod
    def _build(cls) -> Any:
        try:
            from opentelemetry import metrics as otel_metrics
        except ImportError:  # pragma: no cover
            return _NoopRecorder()
        try:
            meter = otel_metrics.get_meter(_METER_NAME)
            ops = meter.create_counter(
                "field_encryption_op_total",
                description="Field encrypt/decrypt operations.",
            )
            kms = meter.create_counter(
                "field_encryption_kms_calls_total",
                description="KMS wrap/unwrap calls.",
            )
            cache = meter.create_counter(
                "field_encryption_dek_cache_total",
                description="DEK cache hit/miss counter.",
            )
            backfill = meter.create_counter(
                "field_encryption_backfill_rows_total",
                description="Rows backfilled to v1 by the backfill job.",
            )
        except Exception:  # pragma: no cover
            return _NoopRecorder()
        return _OtelRecorder(
            op_counter=ops,
            kms_counter=kms,
            cache_counter=cache,
            backfill_counter=backfill,
        )
