"""Declared-reference hydration for v2 surfaces (PRD-B3 D8).

The metadata fold owns a surface's identity and ``payload_ref``.  This module
only resolves that declared reference against a production ``tool_result``
event.  It deliberately does not inspect historical presentation envelopes or
invent a surface-shaped body from arbitrary event fields.

That makes hydration replayable, scope-preserving, and independent of the old
v1 renderer path: a missing or malformed reference simply leaves the surface
unhydrated for the client to show its honest raw/loading state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.projection import _LedgerEventLike


class _EventType:
    SURFACE_CREATED = LedgerEventType.SURFACE_CREATED.value
    TOOL_RESULT = "tool_result"
    SURFACE_SPEC_GENERATED = "surface_spec_generated"


class _Key:
    CALL_ID = "call_id"
    OUTPUT = "output"
    PAYLOAD_REF = "payload_ref"
    SPEC = "spec"
    SURFACE_ID = "surface_id"
    # ``surface_spec_generated`` predates v2 ledger events and publishes the
    # stable surface identity as ``surface_uri``.  The v2 alias stays accepted
    # for replay compatibility, but production generation always uses this
    # canonical field.
    SURFACE_URI = "surface_uri"
    # ``surface.created`` names the read's origin as ``source {connector, op}``
    # (the ledger's vocabulary).  The renderer contract spells the same two
    # facts ``source {server, tool}`` — see :class:`_StateKey`.
    SOURCE = "source"
    CONNECTOR = "connector"
    OP = "op"


class _StateKey:
    """Keys of the ``{spec?, source?, data}`` state the surface renderers read.

    Named apart from :class:`_Key` because they are a different contract: those
    are ledger-event payload keys, these are the ``SurfaceState`` wire shape
    (``packages/api-types`` ``SurfaceState`` / the ai-backend pydantic mirror).
    """

    DATA = "data"
    SPEC = "spec"
    SOURCE = "source"
    SERVER = "server"
    TOOL = "tool"


class SurfaceContentProjection:
    """Resolve declared surface references into ``{data, spec?, source?}`` state.

    ``source`` is the read's own ``surface.created`` provenance re-spelled in the
    renderer's vocabulary — never inferred from the payload, and never invented
    for a subject the caller did not declare.

    ``surface_payload_refs`` is normally supplied from ``SurfaceStoreState`` so
    the HTTP endpoint explicitly declares which subjects it is authorized to
    hydrate.  The optional structural fallback only reads the same durable
    ``surface.created`` records; it exists for coordinators that already have a
    replay batch but not the folded state.  Neither path falls back to retired
    presentation envelopes.
    """

    @staticmethod
    def fold(
        events: Iterable[_LedgerEventLike],
        *,
        surface_payload_refs: Mapping[str, str] | None = None,
    ) -> dict[str, dict[str, object]]:
        ordered = list(events)
        refs = (
            {
                surface_id: payload_ref
                for surface_id, payload_ref in surface_payload_refs.items()
                if isinstance(surface_id, str)
                and surface_id
                and isinstance(payload_ref, str)
                and payload_ref
            }
            if surface_payload_refs is not None
            else SurfaceContentProjection._declared_refs(ordered)
        )
        if not refs:
            return {}

        output_by_call: dict[str, object] = {}
        spec_by_surface: dict[str, Mapping[str, object]] = {}
        source_by_surface: dict[str, dict[str, str]] = {}
        for event in ordered:
            event_type, payload = SurfaceContentProjection._fields(event)
            if event_type == _EventType.TOOL_RESULT:
                call_id = SurfaceContentProjection._text(payload.get(_Key.CALL_ID))
                if call_id is not None and _Key.OUTPUT in payload:
                    # ``output`` is persisted tool data. It may be scalar,
                    # list, or object; no shape is inferred here.
                    output_by_call[call_id] = payload[_Key.OUTPUT]
            elif event_type == _EventType.SURFACE_CREATED:
                surface_id = SurfaceContentProjection._text(
                    payload.get(_Key.SURFACE_ID)
                )
                source = SurfaceContentProjection._state_source(payload)
                if surface_id in refs and source is not None:
                    source_by_surface[surface_id] = source
            elif event_type == _EventType.SURFACE_SPEC_GENERATED:
                surface_id = SurfaceContentProjection._text(
                    payload.get(_Key.SURFACE_URI)
                ) or SurfaceContentProjection._text(payload.get(_Key.SURFACE_ID))
                spec = payload.get(_Key.SPEC)
                if surface_id in refs and isinstance(spec, Mapping):
                    spec_by_surface[surface_id] = dict(spec)

        content: dict[str, dict[str, object]] = {}
        for surface_id, payload_ref in refs.items():
            call_id = SurfaceContentProjection._call_id_from_ref(payload_ref)
            state: dict[str, object] = {}
            if call_id is not None and call_id in output_by_call:
                state[_StateKey.DATA] = output_by_call[call_id]
            spec = spec_by_surface.get(surface_id)
            if spec is not None:
                state[_StateKey.SPEC] = dict(spec)
            if state:
                # Provenance rides only on a state that already has content.
                # A ``source``-only state would flip an unhydrated surface from
                # "no content event yet" (the honest skeleton) to "hydrated with
                # nothing in it", which is the fabricated body this module exists
                # to avoid.
                source = source_by_surface.get(surface_id)
                if source is not None:
                    state[_StateKey.SOURCE] = dict(source)
                content[surface_id] = state
        return content

    @staticmethod
    def _state_source(payload: Mapping[str, object]) -> dict[str, str] | None:
        """Translate ``surface.created``'s ``source {connector, op}`` into the
        renderer contract's ``source {server, tool}``.

        This is the only thing that can name the tool on a spec-less surface:
        the tier-3 note reads ``state.source.tool``, and a generic surface has
        no spec to read it from. Returns ``None`` unless BOTH members resolve to
        non-blank strings — a half-named source would put "unknown" in front of
        the user in a register that says "this is what the system knows".
        """

        source = payload.get(_Key.SOURCE)
        if not isinstance(source, Mapping):
            return None
        server = SurfaceContentProjection._text(source.get(_Key.CONNECTOR))
        tool = SurfaceContentProjection._text(source.get(_Key.OP))
        if server is None or tool is None:
            return None
        return {_StateKey.SERVER: server, _StateKey.TOOL: tool}

    @staticmethod
    def _declared_refs(events: Iterable[_LedgerEventLike]) -> dict[str, str]:
        refs: dict[str, str] = {}
        for event in events:
            event_type, payload = SurfaceContentProjection._fields(event)
            if event_type != _EventType.SURFACE_CREATED:
                continue
            surface_id = SurfaceContentProjection._text(payload.get(_Key.SURFACE_ID))
            payload_ref = SurfaceContentProjection._text(payload.get(_Key.PAYLOAD_REF))
            if surface_id is not None and payload_ref is not None:
                refs[surface_id] = payload_ref
        return refs

    @staticmethod
    def _call_id_from_ref(payload_ref: str) -> str | None:
        prefix, separator, call_id = payload_ref.partition(":")
        if prefix != "call" or not separator:
            return None
        return SurfaceContentProjection._text(call_id)

    @staticmethod
    def _fields(event: _LedgerEventLike) -> tuple[str, Mapping[str, object]]:
        event_type = getattr(event, "event_type", "")
        value = getattr(event_type, "value", None)
        event_type_text = value if isinstance(value, str) else str(event_type)
        payload = getattr(event, "payload", None)
        return event_type_text, payload if isinstance(payload, Mapping) else {}

    @staticmethod
    def _text(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = ["SurfaceContentProjection"]
