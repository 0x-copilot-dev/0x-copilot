"""Pure-domain surface projection (generative-UI PRD-02, plan D3/D4).

:class:`SurfaceProjector` turns a connector tool's output into a
:class:`SurfaceEnvelope` — a ``surface_uri`` plus ``{spec?, data}`` — that rides
inside the ``tool_result`` / ``draft_updated`` event payload. It is a *pure*
function of its inputs: no I/O, no transport, no env reads. Two injected seams:
an optional :class:`~agent_runtime.capabilities.surfaces.store.SurfaceSpecReadPort`
(rung-2 cache read) and an optional :class:`SurfaceGenerationSchedulerPort`
(rung-3 async generation, PRD-07).

Spec-acquisition ladder (D4):

1. **builtin** curated spec (packaged JSON, :mod:`agent_runtime...surfaces.builtin`)
2. injected **store** (cached / previously generated — in-memory or file)
3. **miss** ⇒ envelope ships with ``state.data`` only (no spec) so the frontend
   renders the tier-3 generic view immediately, AND (when a scheduler is wired)
   async generation is scheduled; the generated spec arrives via
   ``surface_spec_generated`` and merges by URI (PRD-04).

The URI grammar is ``<archetype>://<server-slug>/<tool-or-resource>/<id>``; the
id segment is derived from a common id field on the output, else a stable hash
of the call id, so the same logical resource yields the same URI across events.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceEnvelope,
    SurfaceSpec,
    SurfaceState,
)
from agent_runtime.capabilities.surfaces.store import (
    InMemorySurfaceSpecStore,
    SurfaceSpecReadPort,
    SurfaceSpecStorePort,
)

# Ordered id-bearing keys probed to build a stable URI segment (plan D3).
_ID_FIELDS: tuple[str, ...] = ("id", "key", "identifier", "number")

# Characters allowed verbatim in a URI id segment; everything else collapses to
# a dash so an untrusted value can never inject a path separator or scheme.
_URI_SEGMENT_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Length of the stable hash fallback used when no id field is present.
_HASH_LEN = 12


@runtime_checkable
class SurfaceGenerationSchedulerPort(Protocol):
    """Rung-3 seam: schedule async generation for a ladder miss (PRD-07).

    Injected so the pure projector never imports the generation machinery or
    touches an event loop. ``tool_descriptor`` is typed ``object`` (the projector
    only forwards it); the scheduler expects a ``GenToolDescriptor``. Fully
    best-effort: implementations must swallow their own errors.
    """

    def maybe_schedule(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        output: object,
        surface_uri: str,
    ) -> None:
        """Schedule generation for ``(server, tool)`` unless capped/deduped."""
        ...


@dataclass(frozen=True)
class SurfaceProjector:
    """Resolves a tool output into a :class:`SurfaceEnvelope` (or ``None``).

    ``store`` is the optional rung-2 read seam. ``scheduler`` is the optional
    rung-3 seam: on a ladder miss the projector attaches the data-only envelope
    (tier-3 renders instantly) AND schedules async spec generation — never
    blocking the tool-call path. ``enabled`` is a self-contained short-circuit:
    when ``False`` the projector returns ``None`` without resolving — the caller
    decides the toggle and passes it in; the projector never touches the
    environment. (PRD-E3 retired the standalone ``RUNTIME_SURFACE_EMISSION`` env
    gate; the projector is now driven on-demand by the ``SURFACES_V2`` Work Ledger
    emitter, so the runtime construction path leaves ``enabled`` at its ``True``
    default.)
    """

    store: SurfaceSpecReadPort | None = None
    enabled: bool = True
    scheduler: SurfaceGenerationSchedulerPort | None = None

    def resolve(
        self,
        server_name: str,
        tool_name: str,
        output: object,
        *,
        call_id: str | None = None,
        tool_descriptor: object | None = None,
    ) -> SurfaceEnvelope | None:
        """Return a surface envelope for a non-error tool output, or ``None``.

        ``None`` when emission is disabled or ``output`` is not a mapping
        (str/None/list scalars have no surface). A mapping always yields an
        envelope — with a spec when one is curated/stored, otherwise
        ``state.data`` only for the tier-3 fallback, and (when a scheduler is
        wired and the model is enabled) an async generation is scheduled so the
        surface upgrades in place once ``surface_spec_generated`` lands.
        """

        if not self.enabled:
            return None
        if not isinstance(output, Mapping):
            return None

        spec = self._resolve_spec(server_name, tool_name)
        archetype = (
            spec.archetype if spec is not None else self._infer_archetype(output)
        )
        surface_uri = self._build_uri(
            archetype=archetype,
            server_name=server_name,
            tool_name=tool_name,
            output=output,
            call_id=call_id,
        )
        if spec is None and self.scheduler is not None:
            self.scheduler.maybe_schedule(
                server=server_name,
                tool=tool_name,
                tool_descriptor=tool_descriptor,
                output=output,
                surface_uri=surface_uri,
            )
        return SurfaceEnvelope(
            surface_uri=surface_uri,
            archetype=archetype,
            state=SurfaceState(spec=spec, data=output),
        )

    # -- ladder ---------------------------------------------------------------

    def _resolve_spec(self, server_name: str, tool_name: str) -> SurfaceSpec | None:
        spec = builtin.lookup(server_name, tool_name)
        if spec is not None:
            return spec
        if self.store is not None:
            return self.store.get(server=server_name, tool=tool_name)
        return None

    # -- URI construction -----------------------------------------------------

    def _build_uri(
        self,
        *,
        archetype: SurfaceArchetype,
        server_name: str,
        tool_name: str,
        output: Mapping[str, object],
        call_id: str | None,
    ) -> str:
        slug = builtin.server_slug(server_name) or "unknown"
        tool = builtin.tool_slug(tool_name) or "tool"
        identifier = self._derive_id(output, call_id)
        return f"{archetype.value}://{slug}/{tool}/{identifier}"

    @classmethod
    def _derive_id(cls, output: Mapping[str, object], call_id: str | None) -> str:
        raw = cls._first_id_field(output)
        if raw is not None:
            segment = _URI_SEGMENT_SAFE.sub("-", str(raw)).strip("-")
            if segment:
                return segment
        return cls._stable_hash(output, call_id)

    @classmethod
    def _first_id_field(cls, output: Mapping[str, object]) -> object | None:
        """Return the first present id-bearing scalar, top-level or one wrapper deep.

        Handles both flat outputs (``{"id": ...}``) and the common single-object
        envelope (``{"issue": {"identifier": ...}}``) without guessing across
        multiple nested objects.
        """

        for field in _ID_FIELDS:
            value = output.get(field)
            if cls._is_scalar_id(value):
                return value
        nested = [value for value in output.values() if isinstance(value, Mapping)]
        if len(nested) == 1:
            for field in _ID_FIELDS:
                value = nested[0].get(field)
                if cls._is_scalar_id(value):
                    return value
        return None

    @staticmethod
    def _is_scalar_id(value: object) -> bool:
        return isinstance(value, (str, int)) and not isinstance(value, bool)

    @staticmethod
    def _stable_hash(output: Mapping[str, object], call_id: str | None) -> str:
        if call_id:
            basis = call_id
        else:
            try:
                basis = json.dumps(output, sort_keys=True, default=str)
            except (TypeError, ValueError):
                basis = repr(sorted(output.keys()))
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        return digest[:_HASH_LEN]

    # -- archetype inference (no-spec case) -----------------------------------

    @staticmethod
    def _infer_archetype(output: Mapping[str, object]) -> SurfaceArchetype:
        """Coarse archetype for an uncurated output: table if collection-shaped.

        Keeps the URI scheme sensible (and closer to what a generated spec would
        pick) without a spec: a top-level array of objects reads as a ``table``;
        everything else as a ``record``. Purely the URI/lane hint — the frontend
        renders tier-3 generic until a spec arrives.
        """

        for value in output.values():
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, Mapping) for item in value)
            ):
                return SurfaceArchetype.TABLE
        return SurfaceArchetype.RECORD


__all__ = [
    "InMemorySurfaceSpecStore",
    "SurfaceGenerationSchedulerPort",
    "SurfaceProjector",
    "SurfaceSpecReadPort",
    "SurfaceSpecStorePort",
]
