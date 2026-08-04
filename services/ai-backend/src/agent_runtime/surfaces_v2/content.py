"""Surface content resolution for the v2 canvas (PRD-B3 D8 + the floor PRD).

A surface's renderer state — ``{spec?, source?, data}`` — is **carried on the
``surface.created`` record that declares it**. This module's main job is to read
it back out and merge the one upgrade that legitimately arrives later (a
model-refined spec on ``surface_spec_generated``).

That is a deliberate reversal. The state used to be shipped in pieces and
rejoined here: the spec from one event, the payload from another via
``payload_ref`` → ``call_id`` → ``tool_result.output``. Four hops had to work
for one object to arrive, and every one of them broke independently in
production — the live path cannot obtain a tool call id at all, and the payload
the run persists is a different *representation* of the read than the one the
spec was inferred from. Rejoining two registers of one payload by an id nobody
holds is not a hydration strategy; carrying the object is.

The reference path survives for **historic replay only**, where it is the only
thing that exists: runs recorded before the state was carried have a
``surface.created`` with a real ``call_id`` in ``payload_ref`` and their payload
sitting in a sibling ``tool_result``. That pairing is pinned by the frozen
cross-language corpus ``legacy_v2_replay_corpus.json``, so it is contract, not
convenience. Carried state always wins over it.

Either way this stays replayable, scope-preserving, and free of the retired v1
presentation envelope: a surface with neither carried state nor a resolvable
reference is simply left unhydrated for the client to show its honest raw or
loading state, never a body invented from arbitrary event fields.
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
    #: The carried renderer state on ``surface.created`` — this module's
    #: primary input.
    STATE = "state"
    #: Read from ``surface_spec_generated`` (the model's later refinement) and,
    #: inside ``state``, from ``surface.created`` (the ladder's resolved spec).
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


class _CarriedState:
    """Read the renderer state off the record that declared the surface.

    Rebuilt member by member rather than copied wholesale, for the same reason
    the transport allow-list rebuilds it: this becomes what a user is shown, and
    a key nobody declared must not ride through to a renderer just because it
    was on a trusted event type. Total over junk — every member degrades on its
    own and an unreadable state is simply no state.
    """

    @classmethod
    def read(cls, payload: Mapping[str, object]) -> dict[str, object] | None:
        state = payload.get(_Key.STATE)
        if not isinstance(state, Mapping):
            return None
        carried: dict[str, object] = {}
        spec = state.get(_StateKey.SPEC)
        if isinstance(spec, Mapping):
            carried[_StateKey.SPEC] = dict(spec)
        source = cls._source(state.get(_StateKey.SOURCE))
        if source is not None:
            carried[_StateKey.SOURCE] = source
        if _StateKey.DATA in state:
            carried[_StateKey.DATA] = state[_StateKey.DATA]
        return carried or None

    @staticmethod
    def _source(value: object) -> dict[str, str] | None:
        """The carried ``{server, tool}`` pair, or ``None`` if half-named."""

        if not isinstance(value, Mapping):
            return None
        server = SurfaceContentProjection._text(value.get(_StateKey.SERVER))
        tool = SurfaceContentProjection._text(value.get(_StateKey.TOOL))
        if server is None or tool is None:
            return None
        return {_StateKey.SERVER: server, _StateKey.TOOL: tool}


class SurfaceContentProjection:
    """Resolve surfaces into the ``{spec?, source?, data}`` state renderers read.

    Three inputs, in precedence order:

    * the state **carried** on ``surface.created`` — the projector's own object,
      spec and payload in the register they were resolved together in;
    * for historic runs that carry none, the declared ``payload_ref`` resolved
      against this run's ``tool_result`` (see the module docstring);
    * the later ``surface_spec_generated`` refinement, overlaid on whichever of
      those produced the surface — the model refines a delivered spec, it never
      authors from a blank page, and it never un-delivers one.

    ``source`` is the read's own provenance as the runtime recorded it — never
    inferred from the payload, because a payload that could name its own
    provenance could claim to be any tool.

    ``surface_payload_refs`` is normally supplied from ``SurfaceStoreState`` so
    the HTTP endpoint explicitly declares which subjects it is authorized to
    hydrate; the optional structural fallback reads the same durable
    ``surface.created`` records. Note what that argument now is: purely an
    **authorization filter** naming the subjects this caller may see. It was
    also the data path, which is why an unresolvable reference used to mean an
    empty surface rather than merely an unnamed one.
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
        carried_by_surface: dict[str, dict[str, object]] = {}
        generated_by_surface: dict[str, Mapping[str, object]] = {}
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
                if surface_id is not None and surface_id in refs:
                    carried = _CarriedState.read(payload)
                    if carried is not None:
                        carried_by_surface[surface_id] = carried
                    elif surface_id not in carried_by_surface:
                        # Historic record with no carried state. Keep a place
                        # for it so the reference path below can still name the
                        # surface's provenance from the ledger's own spelling.
                        legacy = SurfaceContentProjection._legacy_source(payload)
                        if legacy is not None:
                            carried_by_surface[surface_id] = {_StateKey.SOURCE: legacy}
            elif event_type == _EventType.SURFACE_SPEC_GENERATED:
                # The model's refinement of an already-delivered spec, keyed on
                # the same surface identity. Held apart from the delivered state
                # rather than merged in stream order: a repeat read of the same
                # record re-emits ``surface.created``, and that must refresh the
                # DATA without demoting the shape back down the ladder.
                surface_id = SurfaceContentProjection._text(
                    payload.get(_Key.SURFACE_URI)
                ) or SurfaceContentProjection._text(payload.get(_Key.SURFACE_ID))
                spec = payload.get(_Key.SPEC)
                if surface_id in refs and isinstance(spec, Mapping):
                    generated_by_surface[surface_id] = dict(spec)

        content: dict[str, dict[str, object]] = {}
        for surface_id, payload_ref in refs.items():
            state = dict(carried_by_surface.get(surface_id, {}))
            if _StateKey.DATA not in state:
                # Historic replay only — see the module docstring.
                call_id = SurfaceContentProjection._call_id_from_ref(payload_ref)
                if call_id is not None and call_id in output_by_call:
                    state[_StateKey.DATA] = output_by_call[call_id]
            generated = generated_by_surface.get(surface_id)
            if generated is not None:
                state[_StateKey.SPEC] = dict(generated)
            if _StateKey.DATA in state or _StateKey.SPEC in state:
                # Provenance rides only on a state that already has content.
                # A ``source``-only state would flip an unhydrated surface from
                # "no content event yet" (the honest skeleton) to "hydrated with
                # nothing in it", which is the fabricated body this module
                # exists to avoid.
                content[surface_id] = state
        return content

    @staticmethod
    def _legacy_source(payload: Mapping[str, object]) -> dict[str, str] | None:
        """Translate ``surface.created``'s ``source {connector, op}`` into the
        renderer contract's ``source {server, tool}``.

        Only for records that carry no ``state`` of their own: current emission
        puts this pair inside the state, produced once, so translating it a
        second time here would re-open the split where one tool could be served
        under two names depending which path a client read.

        This is the only thing that can name the tool on a spec-less surface —
        the tier-3 note reads ``state.source.tool``, and a generic surface has
        no spec to read it from. Returns ``None`` unless BOTH members resolve to
        non-blank strings: a half-named source would put "unknown" in front of
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
