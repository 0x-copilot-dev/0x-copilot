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


class SurfaceContentProjection:
    """Resolve declared surface references into ``{data, spec?}`` state.

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
        for event in ordered:
            event_type, payload = SurfaceContentProjection._fields(event)
            if event_type == _EventType.TOOL_RESULT:
                call_id = SurfaceContentProjection._text(payload.get(_Key.CALL_ID))
                if call_id is not None and _Key.OUTPUT in payload:
                    # ``output`` is persisted tool data. It may be scalar,
                    # list, or object; no shape is inferred here.
                    output_by_call[call_id] = payload[_Key.OUTPUT]
            elif event_type == _EventType.SURFACE_SPEC_GENERATED:
                surface_id = SurfaceContentProjection._text(
                    payload.get(_Key.SURFACE_ID)
                )
                spec = payload.get(_Key.SPEC)
                if surface_id in refs and isinstance(spec, Mapping):
                    spec_by_surface[surface_id] = dict(spec)

        content: dict[str, dict[str, object]] = {}
        for surface_id, payload_ref in refs.items():
            call_id = SurfaceContentProjection._call_id_from_ref(payload_ref)
            state: dict[str, object] = {}
            if call_id is not None and call_id in output_by_call:
                state["data"] = output_by_call[call_id]
            spec = spec_by_surface.get(surface_id)
            if spec is not None:
                state["spec"] = dict(spec)
            if state:
                content[surface_id] = state
        return content

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
