"""WorkLedgerEmitter — turns v1 runtime acts into v2 ledger events (PRD-A3 D3).

The emitter is the single seam that records, on the run's existing append-only
event log, what the v1 pipeline already does: an executed MCP tool read
(``action.classified`` + ``read.executed``), the v1 surface envelope it attached
(``surface.created`` + ``view.derived``), and the async spec-generation upgrade
(a second ``view.derived`` with ``basis: generated``). It never invents policy
(``class`` is always ``unknown``, ``basis`` ``default`` in A3 — a classifier
lands in PRD-C1) and never fails a tool call: every method swallows its own
exceptions.

``surface.created`` also **carries the renderer's whole state inline** —
``{spec?, source?, data}``, the exact ``SurfaceState`` the projector built
(generative-UI floor PRD §3.2b, widened from ``spec`` to the full state).

It used to ship only ``{v, surface_id, kind, source, title, payload_ref}``. The
projector had already assembled one coherent object; the pipeline then took it
apart and shipped the pieces separately, so reassembling it needed FOUR hops to
all work — and each one broke independently in production while the unit suite
stayed green:

* the transport allow-list silently stripped the spec;
* ``payload_ref`` is always ``call:unattributed`` on the live agent path,
  because the tool call id is structurally unobtainable in the tool layer
  (three doors verified shut — see ``McpPresentedTool._call_id``), so the
  id-based join to the payload could never resolve;
* the persisted ``tool_result.output`` is the MODEL-facing content half,
  doubly JSON-encoded, while the spec was inferred from the ARTIFACT half —
  one payload in two registers, and ``items_path`` resolved against neither;
* the client's own id round-trip lost the surface on the way back.

Carrying the state removes the reassembly rather than repairing it: the record
that declares a surface now also carries what to draw in it, in the same
register the spec was written against. ``payload_ref`` stays on the event as
provenance (the receipt/audit path reads it, and historic replay still resolves
through it) — it simply stops being the only way to reach the payload.

Layering: this module takes an :data:`EmitFn` closure that maps an event-type
*value* (a raw A1 ``LedgerEventType`` string) to the runtime event producer, so
it never imports ``runtime_api``. It binds itself for a run through a ContextVar,
mirroring ``SurfaceGenerationScheduler`` in ``capabilities/surfaces/generator``.
"""

from __future__ import annotations

__operation_boundary__ = "presentation"

import logging
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from agent_runtime.capabilities.mcp.annotations import McpToolAnnotationsRegistry
from agent_runtime.capabilities.surfaces.builtin import (
    display_name,
    server_slug,
    tool_slug,
)
from agent_runtime.capabilities.surfaces.generator import DotPathResolver
from agent_runtime.capabilities.surfaces.spec_models import SurfaceArchetype
from agent_runtime.surfaces_v2.constants import Keys, Messages, Titles, Values
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType, SurfaceKind

_LOGGER = logging.getLogger(__name__)

# (event_type_value, payload, summary) → append. Event types are raw A1 string
# values so this module never imports runtime_api (runtime_api imports
# agent_runtime, never the reverse).
EmitFn = Callable[[str, Mapping[str, object], str | None], Awaitable[None]]


class _ArchetypeKind:
    """Maps a v1 ``SurfaceArchetype`` onto the SDR §5 surface-kind set (D1).

    ``record`` / ``table`` / ``message`` pass through; ``board`` collapses to
    ``table`` (a board renders as a grouped table); every other v1 archetype
    (``doc`` / ``event`` / ``timeline`` / ``dashboard`` / ``file`` / ``form``)
    has no v2 kind of its own yet and folds to ``record``. Reconciled against
    the A1 golden fixture's ``surface.created.kind`` examples (``record``): the
    fixture wins and this mapping agrees.
    """

    _MAP: Mapping[SurfaceArchetype, SurfaceKind] = {
        SurfaceArchetype.RECORD: SurfaceKind.RECORD,
        SurfaceArchetype.TABLE: SurfaceKind.TABLE,
        SurfaceArchetype.BOARD: SurfaceKind.TABLE,
        SurfaceArchetype.MESSAGE: SurfaceKind.MESSAGE,
    }

    @classmethod
    def resolve(cls, archetype: object) -> str:
        """Return the SDR kind value for an archetype string, defaulting record."""

        try:
            key = SurfaceArchetype(archetype)
        except ValueError:
            return SurfaceKind.RECORD.value
        return cls._MAP.get(key, SurfaceKind.RECORD).value


class SpecRung:
    """Which rung of the spec ladder produced the spec on an envelope.

    Public because it is the vocabulary of :meth:`WorkLedgerEmitter.on_tool_result`'s
    ``spec_rung`` argument — the caller that ran the ladder is the only party
    that knows which rung answered, and the emitter must not re-run it (two
    producers of one fact is the defect this module already fixed once for
    ``source``).

    ``generated`` is here for completeness of the vocabulary only: the model
    upgrade lands out of band, as a second ``view.derived`` emitted by
    :meth:`WorkLedgerEmitter.on_spec_generated`, never through this path.
    """

    BUILTIN = "builtin"
    STORE = "store"
    SHAPE_MATCH = "shape_match"
    INFERRED = "inferred"
    GENERATED = "generated"


class _ViewDerivation:
    """Maps a :class:`SpecRung` onto the ledger's ``(tier, basis)`` pair.

    ``tier`` and ``basis`` are closed, pinned cross-language enums, so an
    INFERRED spec does **not** get a tier of its own: it is ``shaped``, derived
    from structure — which is exactly what ``schema`` already means. Adding a
    value here would be a contract change across three mirrors to say something
    the existing pair already says.

    * builtin / store ⇒ ``shaped`` / ``registry`` — a spec a person curated or a
      previous run persisted.
    * shape_match ⇒ ``shaped`` / ``schema`` — see below.
    * inferred ⇒ ``shaped`` / ``schema`` — derived deterministically from the
      payload's own structure.
    * generated ⇒ ``shaped`` / ``generated`` (emitted by ``on_spec_generated``).
    * no spec at all ⇒ ``generic`` / ``schema``. The floor makes this
      unreachable in production, but the emitter stays total over an envelope
      that carries none.

    ``shape_match`` is the one that has to be argued, because both readings are
    defensible: the spec IS a curated registry entry, but the connector it was
    reused for was never curated. ``registry`` claims a human vouched for *this*
    binding, and under a shape match nobody did — the binding was chosen by the
    payload's structure clearing a similarity threshold, which is precisely what
    ``schema`` already means. So it reports ``schema``, understating a fact
    (there was a curated spec involved) rather than asserting one that never
    happened (a person approved this pairing). Identical to how ``_UNSTATED``
    below was resolved, and for the identical reason: in a durable compliance
    record, understating provenance is recoverable and overstating it is not.
    """

    _BY_RUNG: Mapping[str, tuple[str, str]] = {
        SpecRung.BUILTIN: (Values.TIER_SHAPED, Values.BASIS_REGISTRY),
        SpecRung.STORE: (Values.TIER_SHAPED, Values.BASIS_REGISTRY),
        SpecRung.SHAPE_MATCH: (Values.TIER_SHAPED, Values.BASIS_SCHEMA),
        SpecRung.INFERRED: (Values.TIER_SHAPED, Values.BASIS_SCHEMA),
        SpecRung.GENERATED: (Values.TIER_SHAPED, Values.BASIS_GENERATED),
    }
    _NO_SPEC: tuple[str, str] = (Values.TIER_GENERIC, Values.BASIS_SCHEMA)
    # An unstated rung over a spec that IS present. This used to report
    # ``registry`` on the reasoning that builtin and store were the only rungs
    # that could produce one — and its own comment predicted the failure that
    # then happened: "it stops being true the moment a caller resolves a spec by
    # inference without saying so." The inference floor made that the common
    # case, and an unwired caller silently stamped ``basis: registry`` on
    # structurally-derived specs.
    #
    # It now fails toward the LEAST-CLAIMING basis instead. Both readings are
    # wrong when the rung is unstated, but they are not equally wrong: this is a
    # durable compliance record, so understating provenance ("shaped from the
    # payload's structure") is recoverable, while overstating it ("shaped from a
    # curated registry entry") asserts human authorship that never happened.
    _UNSTATED: tuple[str, str] = (Values.TIER_SHAPED, Values.BASIS_SCHEMA)

    @classmethod
    def resolve(cls, *, rung: object, has_spec: bool) -> tuple[str, str]:
        """Return the ``(tier, basis)`` wire pair for a rung, total over junk."""

        if not has_spec:
            return cls._NO_SPEC
        if isinstance(rung, str):
            derived = cls._BY_RUNG.get(rung)
            if derived is not None:
                return derived
        return cls._UNSTATED


class _CarriedState:
    """The ``{spec?, source?, data}`` renderer state carried on ``surface.created``.

    A restatement of the envelope's own ``state``, not a second derivation of
    it. The projector assembled this object; this class copies the three keys
    the renderer contract declares and drops everything else, so a future
    envelope field cannot silently widen what a client is handed.

    ``source`` is the one member rebuilt rather than copied, from the
    ``(connector, op)`` pair :meth:`WorkLedgerEmitter._emit_surface` already
    resolved — which IS the envelope's ``state.source`` when it declared one,
    and the call's own names when it did not. That keeps one producer for the
    served tool name (the defect this layer already fixed once) and it means a
    surface whose envelope named no source still reaches the renderer able to
    say which tool it is showing.
    """

    @classmethod
    def build(
        cls,
        state: object,
        *,
        connector: str,
        op: str,
    ) -> dict[str, object] | None:
        """Return the carried state, or ``None`` when the envelope has none."""

        if not isinstance(state, Mapping):
            return None
        carried: dict[str, object] = {}
        spec = state.get(_EnvelopeKey.SPEC)
        if isinstance(spec, Mapping):
            # Copied, not referenced: the envelope belongs to the caller and an
            # event payload is a durable record of what was sent.
            carried[_StateKey.SPEC] = dict(spec)
        if connector and op:
            carried[_StateKey.SOURCE] = {
                _StateKey.SERVER: connector,
                _StateKey.TOOL: op,
            }
        # Present only when the envelope carried it. ``SurfaceState.data`` is a
        # required field, so its absence here means the dump excluded a ``None``
        # — a surface with nothing to draw. Writing ``data: null`` anyway would
        # tell the fold this surface is hydrated and empty, which is the
        # fabricated body the whole path is written to avoid.
        if _EnvelopeKey.DATA in state:
            carried[_StateKey.DATA] = state[_EnvelopeKey.DATA]
        return carried or None


@dataclass(frozen=True)
class WorkLedgerEmitter:
    """Emits the four A3 ledger event types through a bound :data:`EmitFn`."""

    emit: EmitFn

    # -- emission -----------------------------------------------------------

    async def on_tool_result(
        self,
        *,
        server_name: str,
        tool_name: str,
        call_id: str,
        output: object,
        surface: object,
        surface_uri: object,
        latency_ms: int | None,
        spec_rung: str | None = None,
    ) -> None:
        """Emit the read path for one executed MCP tool call.

        Order: ``action.classified`` → ``read.executed`` → (only when the v1
        projector attached an envelope) ``surface.created`` → ``view.derived``.
        Best-effort: any failure is logged and swallowed — a ledger emit never
        breaks a tool call.

        ``spec_rung`` is a :class:`SpecRung` value naming which rung of the
        ladder produced ``surface.state.spec``; it decides the ``view.derived``
        ``(tier, basis)`` pair. It is passed IN rather than inferred here
        because the caller ran the ladder and the emitter did not — the same
        one-producer rule ``source`` follows. ``None`` (no caller states it yet)
        reports a present spec as ``registry``, which is what the builtin/store
        ladder actually resolves today; a caller that resolves a spec by
        inference must say so or the ledger will over-claim.
        """

        try:
            # Two registers, deliberately not one value. ``action.classified``
            # and ``read.executed`` identify the CALL, so they carry the lookup
            # slugs the classifier and the catalogs key on. ``surface.created``
            # carries the SURFACE's provenance, which is the pair the fold hands
            # to the renderer as ``state.source`` and the no-spec note reads out
            # loud — see ``_emit_surface``.
            connector = server_slug(server_name)
            op = tool_slug(tool_name)
            payload_ref = f"{Values.CALL_REF_PREFIX}{call_id}"

            action_class, basis = self._classify(
                server_name=server_name, tool_name=tool_name
            )
            await self._emit_action_classified(
                call_id=call_id,
                connector=connector,
                op=op,
                action_class=action_class,
                basis=basis,
            )
            await self._emit_read_executed(
                call_id=call_id,
                connector=connector,
                op=op,
                latency_ms=latency_ms,
                payload_ref=payload_ref,
            )
            if isinstance(surface, Mapping):
                await self._emit_surface(
                    surface=surface,
                    surface_uri=surface_uri,
                    server_name=server_name,
                    tool_name=tool_name,
                    payload_ref=payload_ref,
                    spec_rung=spec_rung,
                )
        except Exception:  # noqa: BLE001 - ledger emission never fails a tool call
            _LOGGER.warning(Messages.EMIT_RAISED, exc_info=True)

    async def on_spec_generated(self, *, payload: Mapping[str, object]) -> None:
        """Emit the ``view.derived {basis: generated}`` upgrade for a spec (D4).

        Fired after the v1 ``surface_spec_generated`` event so the derived-view
        is additive. Best-effort: logged + swallowed on failure.
        """

        try:
            surface_id = payload.get(_SchedulerPayload.SURFACE_URI)
            if not isinstance(surface_id, str) or not surface_id:
                return
            derived: dict[str, object] = {
                Keys.Field.V: Values.PAYLOAD_V,
                Keys.Field.SURFACE_ID: surface_id,
                Keys.Field.TIER: Values.TIER_SHAPED,
                Keys.Field.BASIS: Values.BASIS_GENERATED,
            }
            # PRD-B3: the generated-basis upgrade carries the ``spec_ref`` for the
            # authored spec (same ``spec:<server>/<tool>`` convention regenerate
            # uses) and the ``gen`` block ``{model, ms}`` (A3 emitted model only).
            spec_ref = self._spec_ref_from_spec(payload.get(_EnvelopeKey.SPEC))
            if spec_ref is not None:
                derived[Keys.Field.SPEC_REF] = spec_ref
            model = payload.get(_SchedulerPayload.GENERATOR_MODEL)
            if isinstance(model, str) and model:
                gen: dict[str, object] = {Keys.Field.MODEL: model}
                ms = payload.get(_SchedulerPayload.GENERATOR_MS)
                if isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0:
                    gen[Keys.Field.MS] = ms
                derived[Keys.Field.GEN] = gen
            await self.emit(
                LedgerEventType.VIEW_DERIVED.value, derived, Messages.VIEW_DERIVED
            )
        except Exception:  # noqa: BLE001 - best-effort upgrade notification
            _LOGGER.warning(Messages.EMIT_RAISED, exc_info=True)

    # -- payload builders ---------------------------------------------------

    @staticmethod
    def _spec_ref_from_spec(spec: object) -> str | None:
        """Build the ``spec:<server>/<tool>`` ref from a generated spec's source.

        The scheduler's ``surface_spec_generated`` payload carries the authored
        spec; its ``source.server`` / ``source.tool`` key the same normalised ref
        the regenerate path and the registry ladder use. Returns ``None`` when the
        spec (or its source) is absent/malformed — the upgrade still emits without
        a ref rather than fabricating one.
        """

        if not isinstance(spec, Mapping):
            return None
        source = spec.get(_EnvelopeKey.SOURCE)
        if not isinstance(source, Mapping):
            return None
        server = source.get(_EnvelopeKey.SERVER)
        tool = source.get(_EnvelopeKey.TOOL)
        if not isinstance(server, str) or not server:
            return None
        if not isinstance(tool, str) or not tool:
            return None
        return f"{Values.SPEC_REF_PREFIX}{server_slug(server)}/{tool_slug(tool)}"

    @staticmethod
    def _classify(*, server_name: str, tool_name: str) -> tuple[str, str]:
        """Classify the call into ``(class, basis)`` wire strings (PRD-C1).

        Consults the module-level classifier singleton with the annotations the
        per-run registry captured for this ``(server, tool)`` (``None`` when the
        registry is unbound — replay/eval/tests — which falls through to
        catalog/default, fail-closed). Best-effort: the classifier is pure and
        total, but any surprise degrades to the honest ``unknown`` / ``default``
        pair (A3's behaviour) rather than raising into the tool-call path.
        """

        try:
            # The surfaces package is also imported while the action catalog is
            # initialized. Resolve the classifier at the execution seam to keep
            # package initialization acyclic; classification remains synchronous
            # and uses the same process-wide singleton for every emitted event.
            from agent_runtime.capabilities.actions.classifier import (  # noqa: PLC0415
                ACTION_CLASSIFIER,
            )

            annotations = McpToolAnnotationsRegistry.get(server_name, tool_name)
            classified = ACTION_CLASSIFIER.classify(
                server=server_name, tool=tool_name, annotations=annotations
            )
            return classified.action_class.value, classified.basis.value
        except Exception:  # noqa: BLE001 - classification never fails a tool call
            _LOGGER.warning(Messages.CLASSIFY_RAISED, exc_info=True)
            return Values.CLASS_UNKNOWN, Values.BASIS_DEFAULT

    async def _emit_action_classified(
        self,
        *,
        call_id: str,
        connector: str,
        op: str,
        action_class: str,
        basis: str,
    ) -> None:
        payload = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.CONNECTOR: connector,
            Keys.Field.OP: op,
            Keys.Field.CLASS: action_class,
            Keys.Field.BASIS: basis,
        }
        await self.emit(LedgerEventType.ACTION_CLASSIFIED.value, payload, None)

    async def _emit_read_executed(
        self,
        *,
        call_id: str,
        connector: str,
        op: str,
        latency_ms: int | None,
        payload_ref: str,
    ) -> None:
        payload: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.CONNECTOR: connector,
            Keys.Field.OP: op,
            Keys.Field.PAYLOAD_REF: payload_ref,
        }
        if latency_ms is not None:
            payload[Keys.Field.LATENCY_MS] = latency_ms
        await self.emit(
            LedgerEventType.READ_EXECUTED.value, payload, Messages.READ_EXECUTED
        )

    async def _emit_surface(
        self,
        *,
        surface: Mapping[str, object],
        surface_uri: object,
        server_name: str,
        tool_name: str,
        payload_ref: str,
        spec_rung: str | None = None,
    ) -> None:
        """Emit ``surface.created`` + the first ``view.derived`` for one surface.

        ``state`` — the renderer's whole ``{spec?, source?, data}`` — rides
        ``surface.created``. This is the delivery. The ladder's
        builtin/store/inferred rungs all resolve backend-side and had no way
        onto the wire, and neither did the payload the spec was resolved
        AGAINST, so the client fell through to the spec-less view over an empty
        body no matter what we matched (PRD §3.2b).

        It is READ OUT OF THE ENVELOPE rather than reassembled here, which is
        the whole point: the projector produced one coherent object, and every
        attempt to ship its pieces separately and rejoin them downstream failed
        at a different hop. A second resolution here could also disagree with
        the one the ``view.derived`` basis describes. A later
        ``surface_spec_generated`` refines the same subject in place; the fold
        overlays the generated spec on the delivered state, which is the
        ladder's own order.

        ``source`` is the surface's provenance in the DISPLAY register, not the
        lookup slugs, and it is read out of the envelope for the same reason:
        the tier-3 note prints it — "No spec matched ``<tool>``".
        ``SurfaceProjector._state_source`` already produced this pair; the two
        used to derive it separately — this one through ``tool_slug``, the
        projector not — so one tool could be served under two names depending on
        which path a client read. One producer, one value. The call's own names
        remain the fallback for an envelope that carries no source (a nameless
        tool).

        Nothing keys off this value: every registry/store/spec-ref lookup that
        later receives it (``ViewDeriver.regenerate`` via
        ``SurfaceSnapshot.connector`` / ``.op``) re-slugs its inputs, so a name
        that is not a slug resolves exactly where a slug did.
        """

        connector, op = self._surface_source(
            surface, server_name=server_name, tool_name=tool_name
        )
        surface_id = (
            surface_uri if isinstance(surface_uri, str) and surface_uri else None
        )
        if surface_id is None:
            uri = surface.get(_EnvelopeKey.SURFACE_URI)
            surface_id = uri if isinstance(uri, str) and uri else None
        if surface_id is None:
            # No stable id ⇒ no surface event (defensive; v1 always sets it).
            return

        state = surface.get(_EnvelopeKey.STATE)
        raw_spec = state.get(_EnvelopeKey.SPEC) if isinstance(state, Mapping) else None
        data = state.get(_EnvelopeKey.DATA) if isinstance(state, Mapping) else None
        spec = raw_spec if isinstance(raw_spec, Mapping) else None

        kind = _ArchetypeKind.resolve(surface.get(_EnvelopeKey.ARCHETYPE))
        title = self._title_for(
            spec=spec,
            data=data,
            connector=connector,
            op=op,
        )

        created: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.KIND: kind,
            Keys.Field.SOURCE: {Keys.Field.CONNECTOR: connector, Keys.Field.OP: op},
            Keys.Field.TITLE: title,
            Keys.Field.PAYLOAD_REF: payload_ref,
        }
        carried = _CarriedState.build(state, connector=connector, op=op)
        if carried is not None:
            created[_AdditiveField.STATE] = carried
        await self.emit(
            LedgerEventType.SURFACE_CREATED.value, created, Messages.SURFACE_CREATED
        )

        tier, basis = _ViewDerivation.resolve(rung=spec_rung, has_spec=spec is not None)
        derived = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.TIER: tier,
            Keys.Field.BASIS: basis,
        }
        await self.emit(
            LedgerEventType.VIEW_DERIVED.value, derived, Messages.VIEW_DERIVED
        )

    @staticmethod
    def _surface_source(
        surface: Mapping[str, object],
        *,
        server_name: str,
        tool_name: str,
    ) -> tuple[str, str]:
        """The envelope's ``state.source`` as ``(connector, op)``, display register.

        Total over an untrusted mapping: a missing, non-mapping, or half-named
        source falls back to the identity names this call was made with, which
        is the same pair the projector would have used. Both members must
        resolve together — a source naming only its server would otherwise pair
        a real connector name with a slugged op and re-open the split.
        """

        state = surface.get(_EnvelopeKey.STATE)
        source = state.get(_EnvelopeKey.SOURCE) if isinstance(state, Mapping) else None
        if isinstance(source, Mapping):
            server = display_name(source.get(_EnvelopeKey.SERVER))
            tool = display_name(source.get(_EnvelopeKey.TOOL))
            if server and tool:
                return server, tool
        return display_name(server_name), display_name(tool_name)

    @staticmethod
    def _title_for(
        *,
        spec: Mapping[str, object] | None,
        data: object,
        connector: str,
        op: str,
    ) -> str:
        """Resolve the surface title (D1): ``spec.title_path`` against ``data``
        when a spec is present and the path resolves, else ``<connector> · <op>``.
        Truncated to :data:`Values.TITLE_MAX_LEN`.

        The fallback is a tab label a person reads, so ``connector`` / ``op``
        arrive already in the display register — the same reason ``source`` does.
        """

        resolved: str | None = None
        if spec is not None:
            title_path = spec.get(_EnvelopeKey.TITLE_PATH)
            if isinstance(title_path, str) and title_path:
                found, value = DotPathResolver.resolve(data, title_path)
                if found and isinstance(value, str) and value.strip():
                    resolved = value.strip()
        if resolved is None:
            resolved = f"{connector}{Titles.SEPARATOR}{op}"
        return resolved[: Values.TITLE_MAX_LEN]

    # -- ContextVar run binding (mirrors SurfaceGenerationScheduler) ---------

    @classmethod
    def bind_for_run(cls, emitter: "WorkLedgerEmitter") -> object:
        """Set the active emitter; return the token for restoration."""

        return _EMITTER_CTX.set(emitter)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous emitter token."""

        _EMITTER_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "WorkLedgerEmitter | None":
        """Return the currently bound emitter, or ``None`` when unbound."""

        return _EMITTER_CTX.get(None)


class _AdditiveField:
    """Ledger payload keys this emitter writes that ``Keys.Field`` does not name.

    ``Keys.Field`` mirrors the A3-frozen SDR §5 field set.
    ``surface.created.state`` is additive to it (generative-UI floor PRD §3.2b)
    and is declared in the same pass on ``SurfaceCreatedPayload``, in
    ``work_ledger.json``, in the ``runtime_api`` payload allow-list, and in the
    ``packages/api-types`` mirror — one key, four declarations, no drift.
    """

    STATE = "state"


class _StateKey:
    """Keys of the ``SurfaceState`` contract the renderers consume.

    Deliberately not :class:`_EnvelopeKey`: those name what the v1 envelope
    dump spells, these name the wire shape shared with
    ``packages/api-types`` and ``packages/surface-renderers``. The two agree on
    ``spec``/``data`` and differ on ``source``, whose members the ledger calls
    ``connector``/``op`` and the renderer calls ``server``/``tool``.
    """

    SPEC = "spec"
    SOURCE = "source"
    DATA = "data"
    SERVER = "server"
    TOOL = "tool"


class _EnvelopeKey:
    """Keys read out of the v1 ``SurfaceEnvelope.model_dump`` mapping."""

    SURFACE_URI = "surface_uri"
    ARCHETYPE = "archetype"
    STATE = "state"
    SPEC = "spec"
    DATA = "data"
    TITLE_PATH = "title_path"
    # ``spec.source.{server,tool}`` — the connector/tool a generated spec binds
    # to, used to build the B3 ``spec_ref``.
    SOURCE = "source"
    SERVER = "server"
    TOOL = "tool"


class _SchedulerPayload:
    """Keys read out of the ``surface_spec_generated`` scheduler payload (D4)."""

    SURFACE_URI = "surface_uri"
    GENERATOR_MODEL = "generator_model"
    GENERATOR_MS = "generator_ms"


_EMITTER_CTX: ContextVar[WorkLedgerEmitter | None] = ContextVar(
    "work_ledger_emitter", default=None
)


__all__ = ["EmitFn", "SpecRung", "WorkLedgerEmitter"]
