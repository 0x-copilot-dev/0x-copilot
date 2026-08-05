"""Which SurfaceSpec archetypes a shipped renderer can actually draw.

The sibling of ``surface_spec.py``, and deliberately *not* the same list.
``SURFACE_ARCHETYPES`` is the frozen contract vocabulary — all ten values a
SurfaceSpec may legally carry, which must never shrink, because a spec replayed
from an older run may name any of them and an archetype the client cannot draw
is a fallback, never an error. This module is the smaller, moving fact: the
subset ``packages/surface-renderers`` implements today.

The distinction matters because the two are consumed for opposite purposes.
Validation is licensed by the vocabulary (accept everything ever emitted);
*generation* must be licensed by this set instead. Licensing generation by the
vocabulary is the defect this module closes: half the archetypes have no
renderer, so a generated ``dashboard`` silently collapsed to a generic view and
nothing ever told the model.

Both languages read the JSON sibling, so the fact has one home:

- **Python** — ``IMPLEMENTED_SURFACE_ARCHETYPES`` here, re-exposed as
  ``SurfaceArchetype.implemented()`` by the ai-backend's ``spec_models``, which
  is what the spec generator's prompt is built from.
- **TypeScript** — ``packages/surface-renderers`` derives
  ``IMPLEMENTED_ARCHETYPES`` from its own ``ARCHETYPE_ADAPTERS`` and a test
  asserts the two agree, so deleting a renderer fails CI until this file is
  updated — and updating this file is the *only* edit that relicenses the
  generator.

Follows the ``adapter_allowlist`` precedent: the JSON is loaded at import time
and treated as const after the process starts.
"""

from __future__ import annotations

import json
from importlib.resources import files


class _ImplementedArchetypesResource:
    """Where the JSON sibling lives, and which key carries the set."""

    PACKAGE: str = "copilot_service_contracts"
    FILENAME: str = "implemented_archetypes.json"
    KEY: str = "implemented"

    @classmethod
    def load(cls) -> tuple[str, ...]:
        raw = files(cls.PACKAGE).joinpath(cls.FILENAME).read_text(encoding="utf-8")
        return tuple(str(name) for name in json.loads(raw)[cls.KEY])


# The archetypes with a shipped renderer, in registry order. A strict subset of
# ``copilot_service_contracts.surface_spec.SURFACE_ARCHETYPES``; consumers that
# need the full contract vocabulary want that constant, not this one.
IMPLEMENTED_SURFACE_ARCHETYPES: tuple[str, ...] = _ImplementedArchetypesResource.load()


__all__ = ["IMPLEMENTED_SURFACE_ARCHETYPES"]
