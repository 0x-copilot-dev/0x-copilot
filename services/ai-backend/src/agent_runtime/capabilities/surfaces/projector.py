"""Pure-domain surface projection (generative-UI PRD-02, plan D3/D4).

:class:`SurfaceProjector` turns a connector tool's output into a
:class:`SurfaceEnvelope` — a ``surface_uri`` plus ``{spec?, data}`` — that rides
inside the ``tool_result`` / ``draft_updated`` event payload. It is a *pure*
function of its inputs: no I/O, no transport, no env reads. The only injected
seam is an optional :class:`SurfaceSpecStorePort` (in-memory only in this PRD).

Spec-acquisition ladder (D4):

1. **builtin** curated spec (packaged JSON, :mod:`agent_runtime...surfaces.builtin`)
2. injected **store** (cached / later generated — in-memory impl here)
3. **miss** ⇒ envelope ships with ``state.data`` only (no spec) so the frontend
   renders the tier-3 generic view immediately; a spec may arrive later via
   ``surface_spec_generated`` and merge by URI (PRD-04 — not implemented here).

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

# Ordered id-bearing keys probed to build a stable URI segment (plan D3).
_ID_FIELDS: tuple[str, ...] = ("id", "key", "identifier", "number")

# Characters allowed verbatim in a URI id segment; everything else collapses to
# a dash so an untrusted value can never inject a path separator or scheme.
_URI_SEGMENT_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Length of the stable hash fallback used when no id field is present.
_HASH_LEN = 12


@runtime_checkable
class SurfaceSpecStorePort(Protocol):
    """Read seam for cached / generated specs (rung 2 of the ladder).

    Deliberately synchronous: the projector is pure and non-blocking, and the
    only implementation in this PRD is in-memory. Async-backed stores (file,
    backend-http) are adapted behind this same shape in PRD-07/08.
    """

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        """Return a stored spec for ``(server, tool)`` or ``None``."""
        ...


class InMemorySurfaceSpecStore:
    """In-memory :class:`SurfaceSpecStorePort` for tests and single-process dev."""

    def __init__(self) -> None:
        self._specs: dict[tuple[str, str], SurfaceSpec] = {}

    def put(self, spec: SurfaceSpec) -> None:
        """Register ``spec`` under its own ``source`` server/tool."""
        self._specs[self._key(spec.source.server, spec.source.tool)] = spec

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        """Return the stored spec for ``(server, tool)`` or ``None``."""
        return self._specs.get(self._key(server, tool))

    @staticmethod
    def _key(server: str, tool: str) -> tuple[str, str]:
        return (builtin.server_slug(server), builtin.tool_slug(tool))


@dataclass(frozen=True)
class SurfaceProjector:
    """Resolves a tool output into a :class:`SurfaceEnvelope` (or ``None``).

    ``store`` is the optional rung-2 seam. ``enabled`` mirrors the
    ``RUNTIME_SURFACE_EMISSION`` flag: when ``False`` the projector
    short-circuits to ``None`` so payloads stay byte-for-byte identical to
    pre-surface behaviour. Read the flag at the emission chokepoint and pass it
    in — the projector itself never touches the environment.
    """

    store: SurfaceSpecStorePort | None = None
    enabled: bool = True

    def resolve(
        self,
        server_name: str,
        tool_name: str,
        output: object,
        *,
        call_id: str | None = None,
    ) -> SurfaceEnvelope | None:
        """Return a surface envelope for a non-error tool output, or ``None``.

        ``None`` when emission is disabled or ``output`` is not a mapping
        (str/None/list scalars have no surface). A mapping always yields an
        envelope — with a spec when one is curated/stored, otherwise
        ``state.data`` only for the tier-3 fallback.
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
    "SurfaceProjector",
    "SurfaceSpecStorePort",
]
