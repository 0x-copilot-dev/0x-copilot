"""Runtime kill switch for Generative Surfaces v2 emission (PRD-A3 D2 / E3 D5).

A single env flag, ``SURFACES_V2``, gates whether the runtime emits the Work
Ledger event types (``action.classified`` / ``read.executed`` /
``surface.created`` / ``view.derived`` / …) and binds the
:class:`WorkLedgerEmitter` for a run. **PRD-E3 flipped the default to on**: v2 now
*owns* surface emission (the v1 ``result["surface"]`` appendage was retired in the
same PR), so ``SURFACES_V2`` unset means "surfaces on". ``SURFACES_V2=false``
(or ``0`` / ``no`` / ``off``) is the explicit kill switch / rollback — with it the
runtime binds no emitter and emits no v2 events (chat-only degradation).

Semantics are pinned to the same truthy set the runtime settings loader uses
(``RuntimeExecutionSettings.surfaces_v2`` reads ``SURFACES_V2`` through
``_BOOL_TRUTHY = {"1", "true", "yes", "on"}``): the value is enabling only when
it is ``true`` / ``1`` / ``yes`` / ``on`` (case-insensitive, trimmed). The one
change in E3 is the **default when the var is absent** — it is now ``"true"``, so
an unset environment resolves on. An explicitly-empty or otherwise non-truthy
value (``""`` / ``false`` / ``0`` / garbage) still resolves off, byte-identical to
the settings loader's ``_BOOL_TRUTHY`` membership test. This class is the reusable
reader every wave imports; the run handler gates binding on the already-threaded
``settings.execution.surfaces_v2`` value, which resolves identically.

Follows the self-contained flag-reader precedent (``QueueTracePropagator.enabled``)
and the ``isRunCockpitWebEnabled`` fail-toward-ON default rather than threading a
new setting through a central config object.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import ClassVar


class SurfacesV2Flag:
    """Whether Generative Surfaces v2 ledger emission is active (default **on**, E3)."""

    ENV_VAR: ClassVar[str] = "SURFACES_V2"

    # Values that read as "on". An unset var defaults to this too (E3 flip). An
    # explicitly-empty, "false"/"0"/"no"/"off", or otherwise non-membership value
    # reads off. Matches ``settings._BOOL_TRUTHY`` exactly.
    _ENABLED_VALUES: ClassVar[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

    # The default applied when ``SURFACES_V2`` is absent. Flipped to on in E3 (D5)
    # once v2 owns surface emission; ``SURFACES_V2=false`` remains the kill switch.
    _DEFAULT_WHEN_UNSET: ClassVar[str] = "true"

    @classmethod
    def enabled(cls, environ: Mapping[str, str] | None = None) -> bool:
        """Return ``True`` unless ``SURFACES_V2`` is explicitly non-truthy.

        Default-on (E3 D5): an unset var resolves to :data:`_DEFAULT_WHEN_UNSET`
        (``"true"``). Only an explicit non-truthy value (``""`` / ``false`` / ``0``
        / ``no`` / ``off`` / garbage) turns it off — the kill switch / rollback.
        ``environ`` is injectable so tests can assert both branches without
        mutating process state; production reads ``os.environ``.
        """

        source = environ if environ is not None else os.environ
        raw = source.get(cls.ENV_VAR, cls._DEFAULT_WHEN_UNSET).strip().lower()
        return raw in cls._ENABLED_VALUES


__all__ = ["SurfacesV2Flag"]
