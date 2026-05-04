"""OpenTelemetry metrics for the C6 token vault.

The recorder is a no-op when OTel isn't installed/configured so unit tests
that exercise the vault don't have to spin up a meter provider.
"""

from __future__ import annotations

from typing import Any


_METER_NAME = "backend.token_vault"


class _NoopRecorder:
    def record_op(
        self,
        *,
        backend: str,
        op: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        return

    def record_cache(self, *, backend: str, outcome: str) -> None:
        return


class _OtelRecorder:
    def __init__(
        self,
        *,
        encrypt_counter: Any,
        decrypt_counter: Any,
        latency_histogram: Any,
        cache_counter: Any,
    ) -> None:
        self._encrypt_counter = encrypt_counter
        self._decrypt_counter = decrypt_counter
        self._latency_histogram = latency_histogram
        self._cache_counter = cache_counter

    def record_op(
        self,
        *,
        backend: str,
        op: str,
        outcome: str,
        duration_seconds: float,
    ) -> None:
        attrs = {"backend": backend, "result": outcome}
        if op == "encrypt":
            self._encrypt_counter.add(1, attrs)
        else:
            self._decrypt_counter.add(1, attrs)
        self._latency_histogram.record(duration_seconds, {"backend": backend, "op": op})

    def record_cache(self, *, backend: str, outcome: str) -> None:
        self._cache_counter.add(1, {"backend": backend, "outcome": outcome})


class TokenVaultMetrics:
    """Module-level recorder cache so adapters share one set of instruments."""

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
        except ImportError:  # pragma: no cover - optional dep
            return _NoopRecorder()
        try:
            meter = otel_metrics.get_meter(_METER_NAME)
            encrypt = meter.create_counter(
                "token_vault_encrypt_total",
                description="Token vault encrypt operations.",
            )
            decrypt = meter.create_counter(
                "token_vault_decrypt_total",
                description="Token vault decrypt operations.",
            )
            latency = meter.create_histogram(
                "token_vault_kms_latency_seconds",
                description="KMS encrypt/decrypt round-trip latency.",
                unit="s",
            )
            cache = meter.create_counter(
                "token_vault_cache_total",
                description="Decrypt cache hits/misses (use to compute hit ratio).",
            )
        except Exception:  # pragma: no cover - defensive
            return _NoopRecorder()
        return _OtelRecorder(
            encrypt_counter=encrypt,
            decrypt_counter=decrypt,
            latency_histogram=latency,
            cache_counter=cache,
        )
