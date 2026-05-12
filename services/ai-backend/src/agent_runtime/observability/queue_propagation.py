"""W3C trace-context propagation across the API-to-worker queue boundary.

The API and worker run in separate processes. Without propagation each worker
claim starts a new root span, breaking end-to-end trace continuity in the OTLP
backend. This module serialises the active W3C context (``traceparent`` /
``tracestate``) onto the command payload at enqueue time and deserialises it
at claim time.

Behavior is fail-soft on both ends: :meth:`inject` returns an empty dict when
no span is active; :meth:`extract` returns the default OTel ``Context`` on a
missing or malformed carrier — calling code simply begins a fresh trace.

The dispatch flag ``RUNTIME_PROPAGATE_QUEUE_TRACE`` defaults to ``true``.
Both sides honor the same flag so propagation can be disabled without code
changes — useful if dashboards keyed on fresh-trace-per-worker-run need to be
rebuilt first.

"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract as _otel_extract, inject as _otel_inject


class QueueTracePropagator:
    """Inject and extract W3C trace headers on queue command payloads."""

    _FLAG_ENV_VAR = "RUNTIME_PROPAGATE_QUEUE_TRACE"

    @classmethod
    def enabled(cls) -> bool:
        """Whether cross-process trace propagation is currently active.

        Defaults to true. Operators flip ``RUNTIME_PROPAGATE_QUEUE_TRACE``
        to ``"false"`` / ``"0"`` to disable both sides simultaneously.
        """

        raw = os.environ.get(cls._FLAG_ENV_VAR, "true").strip().lower()
        return raw not in {"false", "0", "no", "off", ""}

    @classmethod
    def inject(cls) -> dict[str, str]:
        """Return a propagation carrier for the current OTel context.

        Returns an empty dict when:
          - propagation is disabled via the env flag, or
          - no valid span is currently active.

        Callers attach the dict to ``command.trace_propagation`` before
        enqueue. The carrier is otherwise opaque — only the matching
        :meth:`extract` consumes it.
        """

        if not cls.enabled():
            return {}
        span = trace.get_current_span()
        if span is None:
            return {}
        ctx = span.get_span_context()
        if ctx is None or not ctx.is_valid:
            return {}
        carrier: dict[str, str] = {}
        _otel_inject(carrier)
        return carrier

    @classmethod
    def extract(cls, carrier: object) -> Context:
        """Return an OTel ``Context`` for the carrier headers.

        Tolerates:
          - missing carriers (``None`` / non-mapping values),
          - empty carriers,
          - malformed ``traceparent`` headers (OTel's textmap propagator
            returns the default context without raising on parse failure).

        When propagation is disabled, the default context is returned
        regardless of carrier contents.
        """

        if not cls.enabled():
            return Context()
        if not isinstance(carrier, dict):
            return Context()
        # The W3C propagator inspects only string keys and string values;
        # filter defensively so a malformed payload (e.g. a number that
        # snuck through the JSON boundary) cannot raise.
        cleaned = {
            k: v
            for k, v in carrier.items()
            if isinstance(k, str) and isinstance(v, str)
        }
        if not cleaned:
            return Context()
        return _otel_extract(cleaned)
