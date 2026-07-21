"""Runtime configuration for generative-UI surface emission (PRD-02).

A single env flag, ``RUNTIME_SURFACE_EMISSION``, gates whether the runtime
attaches ``surface`` envelopes to tool results and draft updates. It defaults
to **true**: emission is best-effort and never changes tool behaviour, so the
feature ships on. Operators flip it to ``"false"``/``"0"`` to short-circuit the
projector to ``None`` — the payloads then match today's byte-for-byte.

Follows the established self-contained flag-reader precedent
(``QueueTracePropagator.enabled`` in ``agent_runtime.observability``), rather
than threading a new setting through a central config object nothing else in
this package reads.
"""

from __future__ import annotations

import os
from typing import ClassVar

# Values that read as "off". Anything else (including unset ⇒ default) is on.
_DISABLED_VALUES: frozenset[str] = frozenset({"false", "0", "no", "off"})


class SurfaceEmissionFlag:
    """Whether the runtime attaches surface envelopes to emitted events."""

    ENV_VAR: ClassVar[str] = "RUNTIME_SURFACE_EMISSION"

    @classmethod
    def enabled(cls, environ: dict[str, str] | None = None) -> bool:
        """Return ``True`` when surface emission is active (the default).

        ``environ`` is injectable so tests can assert both branches without
        mutating process state; production reads ``os.environ``.
        """

        source = environ if environ is not None else os.environ
        raw = source.get(cls.ENV_VAR, "true").strip().lower()
        return raw not in _DISABLED_VALUES and raw != ""


__all__ = ["SurfaceEmissionFlag"]
