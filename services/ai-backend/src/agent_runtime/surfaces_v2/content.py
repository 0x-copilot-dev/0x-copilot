"""Surface content hydration — resolve a v2 surface's materialized payload (B2).

The SurfaceStore fold (``projection.py``) produces **metadata-only** snapshots:
it folds ``surface.created`` / ``view.derived`` into ``payload_ref`` + view
bookkeeping, never the surface's actual content. The v2 canvas needs the content
too — the record/table/message body, or the raw blob for the honest fallback.

That content is not stored separately: it already rides the run's existing event
stream. The v1 surface projector attaches a ``surface`` envelope
(``{surface_uri, archetype, state:{spec?, data}}``) to ``tool_result`` /
``draft_updated`` / ``presentation_updated`` events, and a late spec arrives via
``surface_spec_generated``. ``surface.created.surface_id`` is exactly that
``surface_uri`` (SDR §5 note: ``payload_ref = call:<call_id>`` resolves to the
tool_result carrying the same call). So content hydration is a **pure fold** over
the same events, keyed by ``surface_uri`` — the Python twin of chat-surface's
``applySurfaceEvent`` (``eventProjector.ts``).

Kept a clean sibling of the metadata fold: this module reads events
**structurally** (``event_type`` / ``payload``), never imports ``runtime_api``,
and never mutates the parity-pinned ``SurfaceStoreState`` — the endpoint merges
this content onto the HTTP snapshot only (``HydratedSurfaceSnapshot``), so the
cross-language parity snapshot stays byte-identical.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_runtime.surfaces_v2.projection import _LedgerEventLike


class _EventType:
    """v1 event types that carry a surface envelope (mirror of ``isSurfaceMutation``)."""

    TOOL_RESULT = "tool_result"
    DRAFT_UPDATED = "draft_updated"
    PRESENTATION_UPDATED = "presentation_updated"
    SURFACE_SPEC_GENERATED = "surface_spec_generated"

    MUTATIONS = frozenset({TOOL_RESULT, DRAFT_UPDATED, PRESENTATION_UPDATED})


class _Key:
    """Keys read out of the v1 ``payload.surface`` envelope / legacy flat form."""

    SURFACE = "surface"
    SURFACE_URI = "surface_uri"
    STATE = "state"
    SPEC = "spec"


class SurfaceContentProjection:
    """Pure fold from a run's events to each surface's materialized ``{spec?, data}``.

    Deterministic + total, and a faithful twin of the client ``applySurfaceEvent``:
    surface-mutation events shallow-merge their ``state`` into the surface's
    content (later events win per key); ``surface_spec_generated`` merges **only**
    the ``spec`` key so a late spec never clobbers newer ``data``. Malformed or
    unrelated events are skipped without error. The result is keyed by
    ``surface_uri`` (== ``surface.created.surface_id``); a surface with no content
    event yet is simply absent (honest "not hydrated", never a fabricated body).
    """

    @staticmethod
    def fold(
        events: Iterable[_LedgerEventLike],
    ) -> dict[str, dict[str, object]]:
        content: dict[str, dict[str, object]] = {}
        for event in events:
            event_type = SurfaceContentProjection._event_type_value(event)
            payload = getattr(event, "payload", None)
            if not isinstance(payload, Mapping):
                continue
            if event_type == _EventType.SURFACE_SPEC_GENERATED:
                SurfaceContentProjection._apply_spec_generated(content, payload)
            elif event_type in _EventType.MUTATIONS:
                SurfaceContentProjection._apply_mutation(content, payload)
        return content

    # -- reducers -----------------------------------------------------------

    @staticmethod
    def _apply_mutation(
        content: dict[str, dict[str, object]],
        payload: Mapping[str, object],
    ) -> None:
        uri, state = SurfaceContentProjection._envelope(payload)
        if uri is None:
            return
        merged = content.setdefault(uri, {})
        if isinstance(state, Mapping):
            merged.update(state)

    @staticmethod
    def _apply_spec_generated(
        content: dict[str, dict[str, object]],
        payload: Mapping[str, object],
    ) -> None:
        uri = SurfaceContentProjection._uri_of(payload)
        if uri is None:
            return
        spec = payload.get(_Key.SPEC)
        if not isinstance(spec, Mapping):
            return
        # Spec merge only — ``data`` (if any) is preserved untouched (D4 twin).
        content.setdefault(uri, {})[_Key.SPEC] = dict(spec)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _envelope(
        payload: Mapping[str, object],
    ) -> tuple[str | None, Mapping[str, object] | None]:
        """Return ``(surface_uri, state)`` from the PRD-01 envelope or legacy flat."""

        surface = payload.get(_Key.SURFACE)
        if isinstance(surface, Mapping):
            uri = surface.get(_Key.SURFACE_URI)
            state = surface.get(_Key.STATE)
            if isinstance(uri, str) and uri:
                return uri, state if isinstance(state, Mapping) else None
        # Legacy flat: ``payload.surface_uri`` + ``payload.state``.
        flat_uri = payload.get(_Key.SURFACE_URI)
        if isinstance(flat_uri, str) and flat_uri:
            flat_state = payload.get(_Key.STATE)
            return flat_uri, flat_state if isinstance(flat_state, Mapping) else None
        return None, None

    @staticmethod
    def _uri_of(payload: Mapping[str, object]) -> str | None:
        surface = payload.get(_Key.SURFACE)
        if isinstance(surface, Mapping):
            uri = surface.get(_Key.SURFACE_URI)
            if isinstance(uri, str) and uri:
                return uri
        flat = payload.get(_Key.SURFACE_URI)
        return flat if isinstance(flat, str) and flat else None

    @staticmethod
    def _event_type_value(event: _LedgerEventLike) -> str:
        event_type = getattr(event, "event_type", "")
        value = getattr(event_type, "value", None)
        if isinstance(value, str):
            return value
        return str(event_type)


__all__ = ["SurfaceContentProjection"]
