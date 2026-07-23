"""Runtime kill switch for Generative Surfaces v2 emission (PRD-A3 D2).

A single env flag, ``SURFACES_V2``, gates whether the runtime emits the Work
Ledger event types (``action.classified`` / ``read.executed`` /
``surface.created`` / ``view.derived``) and binds the :class:`WorkLedgerEmitter`
for a run. It defaults **off** — the deliberate opposite of
``RUNTIME_SURFACE_EMISSION`` (``capabilities/surfaces/config.py``): v2 emission
is additive and, until Wave B renders it, purely observational, so it ships dark
and flag-off is byte-identical to today.

Semantics are pinned to the same truthy set the runtime settings loader uses
(``RuntimeExecutionSettings.surfaces_v2`` reads ``SURFACES_V2`` through
``_BOOL_TRUTHY = {"1", "true", "yes", "on"}``): only ``true`` / ``1`` / ``yes`` /
``on`` (case-insensitive, trimmed) enable; unset, empty, and everything else are
off. This class is the reusable reader every later wave imports; the run handler
gates binding on the already-threaded ``settings.execution.surfaces_v2`` value,
which resolves identically.

Follows the self-contained flag-reader precedent (``SurfaceEmissionFlag``,
``QueueTracePropagator.enabled``) rather than threading a new setting through a
central config object.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import ClassVar


class SurfacesV2Flag:
    """Whether Generative Surfaces v2 ledger emission is active (default off)."""

    ENV_VAR: ClassVar[str] = "SURFACES_V2"

    # Values that read as "on". Everything else — unset, empty, "false", "0",
    # arbitrary garbage — is off. Matches ``settings._BOOL_TRUTHY`` exactly.
    _ENABLED_VALUES: ClassVar[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

    @classmethod
    def enabled(cls, environ: Mapping[str, str] | None = None) -> bool:
        """Return ``True`` only when ``SURFACES_V2`` is explicitly truthy.

        ``environ`` is injectable so tests can assert both branches without
        mutating process state; production reads ``os.environ``.
        """

        source = environ if environ is not None else os.environ
        raw = source.get(cls.ENV_VAR, "").strip().lower()
        return raw in cls._ENABLED_VALUES


__all__ = ["SurfacesV2Flag"]
