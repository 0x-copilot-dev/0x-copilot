"""OTel meters for SurfaceSpec generation (generative-UI PRD-11).

Promotes the PRD-07 ``[surfaces.specgen]`` structured-log lines to counters,
published through the same ``opentelemetry.metrics.get_meter`` pipeline as the
other runtime meters (``approval_metrics``, ``file_store_metrics``, …) — the one
metrics seam, not a parallel backend. Gracefully no-ops when OTel is not
importable (dev/test without the SDK), so every call site is guard-free.

Signals:

* ``surfaces_specgen_total`` (counter, label ``verdict``) — one per generation
  attempt, keyed by the attempt's verdict (``ok`` / ``retry_ok`` /
  ``schema_invalid`` / ``lint_failed`` / ``model_error``).
* ``surfaces_specgen_tokens`` (counter, label ``direction`` = ``input`` |
  ``output``) — model token usage summed across attempts; a *value*, not a label.
* ``surfaces_render_fallback_total`` (counter, label ``tier``) — proxy for the
  frontend render-fallback rate: incremented once per **spec-less backend
  surface envelope** (a ladder miss that ships ``state.data`` only, so the FE
  renders the tier-3 generic view). Counted at the generation-scheduler seam
  (the sole non-pure hook a miss already crosses); when generation is disabled
  the projector stays pure and no proxy is emitted — a documented limitation,
  not a bug. FE telemetry is out of scope (PRD-11 non-goals).

**Label discipline (secret-safe):** every label is a bounded, low-cardinality
enum-ish token — never a server, tool, connector, run, user id, path, or any
byte of user content. Cardinality-unbounded values (token counts) are recorded
as metric *values*, never labels.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_METER_NAME = "agent_runtime.surfaces"

_SPECGEN_TOTAL = "surfaces_specgen_total"
_SPECGEN_TOKENS = "surfaces_specgen_tokens"
_RENDER_FALLBACK_TOTAL = "surfaces_render_fallback_total"


class SpecgenVerdict:
    """Bounded ``verdict`` label values for ``surfaces_specgen_total``.

    Mirrors the per-attempt verdict computed in
    :meth:`SurfaceSpecGenerator.generate` so the metric vocabulary and the
    metering-log vocabulary never drift.
    """

    OK = "ok"
    RETRY_OK = "retry_ok"
    SCHEMA_INVALID = "schema_invalid"
    LINT_FAILED = "lint_failed"
    MODEL_ERROR = "model_error"


class TokenDirection:
    """Bounded ``direction`` label values for ``surfaces_specgen_tokens``."""

    INPUT = "input"
    OUTPUT = "output"


class RenderFallbackTier:
    """Bounded ``tier`` label values for ``surfaces_render_fallback_total``."""

    TIER3 = "tier3"


class SurfaceSpecgenMetrics:
    """Per-process meter facade for surface-generation signals.

    Constructed once by :class:`SurfaceSpecGenerator` (and the generation
    scheduler for the fallback proxy). Every call site is best-effort: a metric
    failure is logged at DEBUG and never propagated to the generation path.
    """

    def __init__(self) -> None:
        self._meter = self._build_meter()
        self._specgen_total: Any | None = None
        self._specgen_tokens: Any | None = None
        self._render_fallback_total: Any | None = None

    @staticmethod
    def _build_meter() -> Any:
        try:
            from opentelemetry import metrics as otel_metrics
        except ImportError:  # pragma: no cover - optional dep
            return None
        try:
            return otel_metrics.get_meter(_METER_NAME)
        except Exception:  # pragma: no cover - defensive
            return None

    def _counter(self, name: str) -> Any:
        if self._meter is None:
            return None
        try:
            return self._meter.create_counter(name)
        except Exception:  # pragma: no cover - defensive
            return None

    def record_generation(self, *, verdict: str) -> None:
        """Increment ``surfaces_specgen_total`` for one generation attempt."""

        if self._specgen_total is None:
            self._specgen_total = self._counter(_SPECGEN_TOTAL)
        if self._specgen_total is None:
            return
        try:
            self._specgen_total.add(1, {"verdict": verdict})
        except Exception:
            logger.debug("surfaces_specgen.total.record_failed", exc_info=True)

    def record_tokens(
        self, *, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        """Add model token usage to ``surfaces_specgen_tokens`` per direction."""

        if input_tokens is None and output_tokens is None:
            return
        if self._specgen_tokens is None:
            self._specgen_tokens = self._counter(_SPECGEN_TOKENS)
        if self._specgen_tokens is None:
            return
        try:
            if input_tokens is not None and input_tokens > 0:
                self._specgen_tokens.add(
                    input_tokens, {"direction": TokenDirection.INPUT}
                )
            if output_tokens is not None and output_tokens > 0:
                self._specgen_tokens.add(
                    output_tokens, {"direction": TokenDirection.OUTPUT}
                )
        except Exception:
            logger.debug("surfaces_specgen.tokens.record_failed", exc_info=True)

    def record_render_fallback(self, *, tier: str = RenderFallbackTier.TIER3) -> None:
        """Increment ``surfaces_render_fallback_total`` for one spec-less envelope."""

        if self._render_fallback_total is None:
            self._render_fallback_total = self._counter(_RENDER_FALLBACK_TOTAL)
        if self._render_fallback_total is None:
            return
        try:
            self._render_fallback_total.add(1, {"tier": tier})
        except Exception:
            logger.debug("surfaces_specgen.fallback.record_failed", exc_info=True)


__all__ = (
    "RenderFallbackTier",
    "SpecgenVerdict",
    "SurfaceSpecgenMetrics",
    "TokenDirection",
)
