"""Policy gate for the tier-2 render-adapter generator (generative-UI PRD-10).

Tier-2 (executable, agent-generated adapters) is the **narrow escape hatch**,
not the default: the archetype / ``SurfaceSpec`` path (Waves 1-2) handles the
common case (plan §1 / decision D1 — the projector prefers specs). The generator
is therefore invocable only when BOTH conditions hold:

1. the surface's archetype is ``None`` / unexpressible (no ``SurfaceSpec`` in the
   vocabulary can render it), AND
2. the operator has explicitly opted in via ``RUNTIME_TIER2_GENERATION=true``.

The flag defaults **OFF** and is **not model-facing by default**: a normal run
never reaches the generator, so shipping executable-codegen dark is impossible
without deliberately flipping the flag. This follows the self-contained
flag-reader precedent (e.g. ``QueueTracePropagator.enabled``) rather than
threading a new setting through a central config object nothing else in this
package reads.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import ClassVar

# Values that read as "on". Anything else (including unset ⇒ default) is off:
# executable codegen is a privileged escape hatch that must be opted into, not a
# best-effort display enhancement, so it defaults off.
_ENABLED_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})


class Tier2GenerationFlag:
    """Whether operator policy permits tier-2 executable adapter generation."""

    ENV_VAR: ClassVar[str] = "RUNTIME_TIER2_GENERATION"

    @classmethod
    def enabled(cls, environ: Mapping[str, str] | None = None) -> bool:
        """Return ``True`` only when the flag is explicitly opted in (default off).

        ``environ`` is injectable so tests can assert both branches without
        mutating process state; production reads ``os.environ``.
        """

        source = environ if environ is not None else os.environ
        raw = source.get(cls.ENV_VAR, "").strip().lower()
        return raw in _ENABLED_VALUES


def should_invoke_tier2_generator(
    *,
    archetype: object | None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the render-adapter generator may run for this surface.

    ``archetype`` is the resolved surface archetype (a ``SurfaceArchetype`` or
    ``None``). An archetype the ``SurfaceSpec`` vocabulary already covers must
    NEVER route through tier-2 — the projector prefers specs (D1) — so an
    expressible archetype short-circuits to ``False`` *regardless* of the flag.
    Only an unexpressible surface (``archetype is None``) AND an opted-in policy
    flag unlock the generator. This is the two-condition gate PRD-10 specifies.
    """

    if archetype is not None:
        return False
    return Tier2GenerationFlag.enabled(environ)


__all__ = [
    "Tier2GenerationFlag",
    "should_invoke_tier2_generator",
]
