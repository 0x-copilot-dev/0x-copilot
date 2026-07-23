"""WorkLedgerEmitter — turns v1 runtime acts into v2 ledger events (PRD-A3 D3).

The emitter is the single seam that records, on the run's existing append-only
event log, what the v1 pipeline already does: an executed MCP tool read
(``action.classified`` + ``read.executed``), the v1 surface envelope it attached
(``surface.created`` + ``view.derived``), and the async spec-generation upgrade
(a second ``view.derived`` with ``basis: generated``). It never invents policy
(``class`` is always ``unknown``, ``basis`` ``default`` in A3 — a classifier
lands in PRD-C1) and never fails a tool call: every method swallows its own
exceptions.

Layering: this module takes an :data:`EmitFn` closure that maps an event-type
*value* (a raw A1 ``LedgerEventType`` string) to the runtime event producer, so
it never imports ``runtime_api``. It binds itself for a run through a ContextVar,
mirroring ``SurfaceGenerationScheduler`` in ``capabilities/surfaces/generator``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass

from agent_runtime.capabilities.surfaces.builtin import server_slug, tool_slug
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
    ) -> None:
        """Emit the read path for one executed MCP tool call.

        Order: ``action.classified`` → ``read.executed`` → (only when the v1
        projector attached an envelope) ``surface.created`` → ``view.derived``.
        Best-effort: any failure is logged and swallowed — a ledger emit never
        breaks a tool call.
        """

        try:
            connector = server_slug(server_name)
            op = tool_slug(tool_name)
            payload_ref = f"{Values.CALL_REF_PREFIX}{call_id}"

            await self._emit_action_classified(
                call_id=call_id, connector=connector, op=op
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
                    connector=connector,
                    op=op,
                    payload_ref=payload_ref,
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
            model = payload.get(_SchedulerPayload.GENERATOR_MODEL)
            if isinstance(model, str) and model:
                derived[Keys.Field.GEN] = {Keys.Field.MODEL: model}
            await self.emit(
                LedgerEventType.VIEW_DERIVED.value, derived, Messages.VIEW_DERIVED
            )
        except Exception:  # noqa: BLE001 - best-effort upgrade notification
            _LOGGER.warning(Messages.EMIT_RAISED, exc_info=True)

    # -- payload builders ---------------------------------------------------

    async def _emit_action_classified(
        self, *, call_id: str, connector: str, op: str
    ) -> None:
        payload = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.CONNECTOR: connector,
            Keys.Field.OP: op,
            Keys.Field.CLASS: Values.CLASS_UNKNOWN,
            Keys.Field.BASIS: Values.BASIS_DEFAULT,
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
        connector: str,
        op: str,
        payload_ref: str,
    ) -> None:
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
        spec = state.get(_EnvelopeKey.SPEC) if isinstance(state, Mapping) else None
        data = state.get(_EnvelopeKey.DATA) if isinstance(state, Mapping) else None
        has_spec = isinstance(spec, Mapping)

        kind = _ArchetypeKind.resolve(surface.get(_EnvelopeKey.ARCHETYPE))
        title = self._title_for(
            spec=spec if has_spec else None,
            data=data,
            connector=connector,
            op=op,
        )

        created = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.KIND: kind,
            Keys.Field.SOURCE: {Keys.Field.CONNECTOR: connector, Keys.Field.OP: op},
            Keys.Field.TITLE: title,
            Keys.Field.PAYLOAD_REF: payload_ref,
        }
        await self.emit(
            LedgerEventType.SURFACE_CREATED.value, created, Messages.SURFACE_CREATED
        )

        tier = Values.TIER_SHAPED if has_spec else Values.TIER_GENERIC
        basis = Values.BASIS_REGISTRY if has_spec else Values.BASIS_SCHEMA
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
    def _title_for(
        *,
        spec: Mapping[str, object] | None,
        data: object,
        connector: str,
        op: str,
    ) -> str:
        """Resolve the surface title (D1): ``spec.title_path`` against ``data``
        when a spec is present and the path resolves, else ``<connector> · <op>``.
        Truncated to :data:`Values.TITLE_MAX_LEN`."""

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


class _EnvelopeKey:
    """Keys read out of the v1 ``SurfaceEnvelope.model_dump`` mapping."""

    SURFACE_URI = "surface_uri"
    ARCHETYPE = "archetype"
    STATE = "state"
    SPEC = "spec"
    DATA = "data"
    TITLE_PATH = "title_path"


class _SchedulerPayload:
    """Keys read out of the ``surface_spec_generated`` scheduler payload (D4)."""

    SURFACE_URI = "surface_uri"
    GENERATOR_MODEL = "generator_model"


_EMITTER_CTX: ContextVar[WorkLedgerEmitter | None] = ContextVar(
    "work_ledger_emitter", default=None
)


__all__ = ["EmitFn", "WorkLedgerEmitter"]
