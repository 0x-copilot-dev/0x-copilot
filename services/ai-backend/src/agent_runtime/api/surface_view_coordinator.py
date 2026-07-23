"""SurfaceViewCoordinator — the API home of PRD-B3's view-lifecycle endpoints.

Two user-invited, surface-keyed mutations that append to the run's Work Ledger
(so their effect survives reload by replay — the SurfaceStore fold reconstructs
the pinned tier / re-derived view):

* ``POST /v1/agent/surfaces/{surface_id}/view-preference`` — pin ``generic`` /
  ``shaped`` (``view.preference``). Durable "Keep generic".
* ``POST /v1/agent/surfaces/{surface_id}/regenerate`` — re-derive the view from
  the **stored** response payload (``view.derived``), with **zero** new connector
  traffic (the :class:`ViewDeriver` never touches the MCP client), bounded by a
  per-surface cap, metered per shaping attempt (``purpose: view_shaping``).

Both are keyed on ``surface_id`` **plus** an owning ``run_id`` (SDR §4, resolved
2026-07-23): A3 ships no ``surface_id → run_id`` index, and the canvas already
holds the ``run_id`` (it fetched ``GET /v1/agent/runs/{run_id}/surfaces``), so the
endpoints require it as a query param. Tenancy reuses the same org/user run-scope
gate ``GET /v1/agent/runs/{run_id}`` uses.

Flag-off is byte-identical: with ``SURFACES_V2`` off no v2 surfaces exist, so both
routes 404 without appending anything.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from starlette import status

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.capabilities.surfaces.backend_store import build_surface_spec_store
from agent_runtime.capabilities.surfaces.generator import (
    SpecCompletionPort,
    SurfaceSpecGenerator,
)
from agent_runtime.execution.contracts import RuntimeErrorCode, StreamEventSource
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_meter import MeteredModelInvocation, UsageMeter
from agent_runtime.observability.usage_recorder import NullUsageRecorder, UsageRecorder
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from agent_runtime.surfaces_v2.constants import Keys, Messages, Values
from agent_runtime.surfaces_v2.content import SurfaceContentProjection
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    ViewBasis,
    ViewKeep,
    ViewTier,
)
from agent_runtime.surfaces_v2.projection import (
    SurfaceSnapshot,
    SurfaceStoreProjection,
)
from agent_runtime.surfaces_v2.shaping_policy import ShapingModelResolver
from agent_runtime.surfaces_v2.view_deriver import (
    RegenerateLimitError,
    ViewDeriver,
    ViewDeriverError,
    _SurfaceScopedInvocation,
)
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_api.schemas.surfaces_v2 import (
    SurfaceViewActionResponse,
    SurfaceViewPreferenceResponse,
)


class _CoordMessages:
    """Safe public messages surfaced through the typed API errors."""

    SURFACE_NOT_FOUND = "surface_not_found"
    RUN_NOT_FOUND = "run_not_found"
    REGENERATE_LIMIT_REACHED = "regenerate_limit_reached"
    VIEW_TIER_UNAVAILABLE = "view_tier_unavailable"


class _StateKey:
    """Keys read out of the B2 content fold's ``{spec?, data}`` state."""

    DATA = "data"


class SurfaceViewCoordinator:
    """Append-and-project the two B3 view-lifecycle mutations for one run."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        event_producer,
        completion: SpecCompletionPort | None = None,
        usage_recorder: UsageRecorder | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._persistence = persistence
        self._event_store = event_store
        self._event_producer = event_producer
        # Injectable completion for tests (no live model); production builds one
        # from the resolved shaping model id on demand.
        self._completion = completion
        # Durable per-call usage table (A2). NullUsageRecorder keeps the ledger
        # ``usage.recorded`` emit working without a table when none is wired.
        self._usage_recorder: UsageRecorder = usage_recorder or NullUsageRecorder()
        self._environ: Mapping[str, str] = (
            environ if environ is not None else os.environ
        )

    # -- view-preference ----------------------------------------------------

    async def set_view_preference(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        surface_id: str,
        keep: ViewKeep,
    ) -> SurfaceViewPreferenceResponse:
        """Append ``view.preference`` for a surface; durable via replay."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events, snapshot = await self._load_surface(
            org_id=org_id, run_id=run_id, surface_id=surface_id
        )
        if keep is ViewKeep.SHAPED and not self._shaped_available(
            events, surface_id=surface_id
        ):
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                _CoordMessages.VIEW_TIER_UNAVAILABLE,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
            )
        payload = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.KEEP: keep.value,
            Keys.Field.ACTOR: Values.ACTOR_USER,
        }
        envelope = await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.VIEW_PREFERENCE,
            payload=payload,
            summary=Messages.VIEW_PREFERENCE,
        )
        return SurfaceViewPreferenceResponse(
            surface_id=surface_id,
            keep=keep,
            ledger_id=LedgerIdCodec.format(run_id, envelope.sequence_no),
        )

    # -- regenerate ---------------------------------------------------------

    async def regenerate_view(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        surface_id: str,
    ) -> SurfaceViewActionResponse:
        """Re-derive a surface's view from its STORED payload (zero re-fetch)."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events, snapshot = await self._load_surface(
            org_id=org_id, run_id=run_id, surface_id=surface_id
        )
        payload = self._stored_payload(events, surface_id=surface_id)
        regen_count = self._regen_count(events, surface_id=surface_id)

        deriver, sink = self._build_deriver(
            org_id=org_id,
            user_id=user_id,
            run=run,
            surface_id=surface_id,
        )
        try:
            derivation = await deriver.regenerate(
                surface_id=surface_id,
                server=snapshot.connector,
                tool=snapshot.op,
                payload=payload,
                regen_count=regen_count,
            )
        except RegenerateLimitError as exc:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                _CoordMessages.REGENERATE_LIMIT_REACHED,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
            ) from exc
        except ViewDeriverError as exc:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                _CoordMessages.SURFACE_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            ) from exc
        if sink.sequence_no is None:  # pragma: no cover - emit always fires here
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                _CoordMessages.SURFACE_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return SurfaceViewActionResponse(
            surface_id=surface_id,
            tier=derivation.tier,
            basis=derivation.basis,
            ledger_id=LedgerIdCodec.format(run_id, sink.sequence_no),
        )

    # -- deriver assembly ---------------------------------------------------

    def _build_deriver(
        self,
        *,
        org_id: str,
        user_id: str,
        run: RunRecord,
        surface_id: str,
    ) -> tuple[ViewDeriver, "_EmitSink"]:
        """Build a request-scoped ViewDeriver + an emit sink capturing the seq.

        The generator (when a shaping model resolves) is wrapped in the A2 meter
        with the surface bound, so each attempt records a ``view_shaping`` usage
        row carrying this ``surface_id`` (DoD). No run-scoped scheduler: the user
        explicitly asked, so a single awaited attempt is correct.
        """

        store = build_surface_spec_store(
            environ=self._environ, org_id=org_id, user_id=user_id
        )
        model_id = ShapingModelResolver.resolve(
            environ=self._environ, run_provider=run.model_provider
        )
        generator: SurfaceSpecGenerator | None = None
        if model_id is not None:
            invocation = MeteredModelInvocation(
                meter=UsageMeter(
                    recorder=self._usage_recorder,
                    emit_event=self._make_usage_emitter(run),
                    surfaces_v2=SurfacesV2Flag.enabled(self._environ),
                ),
                run=run,
                purpose=Purpose.VIEW_SHAPING,
            )
            scoped = _SurfaceScopedInvocation(
                invocation=invocation, surface_id=surface_id
            )
            generator = SurfaceSpecGenerator(
                completion=self._completion_for(model_id),
                usage_meter=scoped,
            )
        sink = _EmitSink()
        deriver = ViewDeriver(
            store=store,
            emit=self._make_emit(run, sink),
            generator=generator,
            scheduler=None,
            model_id=model_id,
        )
        return deriver, sink

    def _completion_for(self, model_id: str) -> SpecCompletionPort:
        if self._completion is not None:
            return self._completion
        from agent_runtime.capabilities.surfaces.generator import (  # noqa: PLC0415
            LangChainSpecCompletion,
        )
        from agent_runtime.execution.deep_agent_builder import (  # noqa: PLC0415
            build_chat_model_from_id,
        )

        model = build_chat_model_from_id(model_id)
        return LangChainSpecCompletion(model=model, model_id=model_id)

    def _make_emit(self, run: RunRecord, sink: "_EmitSink"):
        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            envelope = await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType(str(event_type_value)),
                payload=dict(payload),
                summary=summary,
            )
            sink.sequence_no = envelope.sequence_no

        return _emit

    def _make_usage_emitter(self, run: RunRecord):
        async def _emit_usage(payload: Mapping[str, object]) -> None:
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.USAGE_RECORDED,
                payload=dict(payload),
            )

        return _emit_usage

    # -- ledger reads -------------------------------------------------------

    async def _load_surface(
        self,
        *,
        org_id: str,
        run_id: str,
        surface_id: str,
    ) -> tuple[list, SurfaceSnapshot]:
        """Replay the run's ledger, fold surfaces, return the target snapshot.

        Flag-off ⇒ no v2 surfaces ⇒ 404 (byte-identical, nothing appended).
        """

        if not SurfacesV2Flag.enabled(self._environ):
            raise self._surface_not_found()
        events = await self._event_store.list_events_after(
            org_id=org_id, run_id=run_id, after_sequence=0
        )
        state = SurfaceStoreProjection.fold(run_id, events)
        for snapshot in state.surfaces:
            if snapshot.surface_id == surface_id:
                return list(events), snapshot
        raise self._surface_not_found()

    def _stored_payload(self, events, *, surface_id: str) -> object:
        """The stored tool-output payload for a surface (B2 content fold).

        Prefers the surface's ``data`` (the tool output shape a spec is generated
        for); falls back to the whole hydrated ``state`` when no ``data`` key is
        present. ``None`` ⇒ the deriver raises ``surface_not_found``.
        """

        content = SurfaceContentProjection.fold(events)
        state = content.get(surface_id)
        if not isinstance(state, Mapping):
            return None
        data = state.get(_StateKey.DATA)
        return data if data is not None else dict(state)

    @staticmethod
    def _regen_count(events, *, surface_id: str) -> int:
        """Count prior user regenerations from the ledger (no mutable state).

        Per SDR/PRD-B3: fold the surface's ``view.derived`` events with
        ``basis != registry``, drop the first (the initial derivation), count the
        rest. Deterministic + total; the server cap is authoritative.
        """

        non_registry = 0
        for event in events:
            if _event_type_value(event) != LedgerEventType.VIEW_DERIVED.value:
                continue
            payload = getattr(event, "payload", None)
            if not isinstance(payload, Mapping):
                continue
            if payload.get(Keys.Field.SURFACE_ID) != surface_id:
                continue
            if payload.get(Keys.Field.BASIS) == ViewBasis.REGISTRY.value:
                continue
            non_registry += 1
        return max(0, non_registry - 1)

    @staticmethod
    def _shaped_available(events, *, surface_id: str) -> bool:
        """Whether any shaped derivation exists to pin to (else pin-shaped 409s).

        Scans the surface's ``view.derived`` events for a ``tier: shaped`` — a
        registry hit or a generated upgrade. A "Keep generic" pin does not erase
        the shaped derivation from the ledger, so the toggle back stays valid.
        """

        for event in events:
            if _event_type_value(event) != LedgerEventType.VIEW_DERIVED.value:
                continue
            payload = getattr(event, "payload", None)
            if not isinstance(payload, Mapping):
                continue
            if payload.get(Keys.Field.SURFACE_ID) != surface_id:
                continue
            if payload.get(Keys.Field.TIER) == ViewTier.SHAPED.value:
                return True
        return False

    async def _run_for_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunRecord:
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                _CoordMessages.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return run

    @staticmethod
    def _surface_not_found() -> RuntimeApiError:
        return RuntimeApiError(
            RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            _CoordMessages.SURFACE_NOT_FOUND,
            http_status=status.HTTP_404_NOT_FOUND,
            retryable=False,
        )


class _EmitSink:
    """Captures the ``sequence_no`` of the last event the deriver appended."""

    def __init__(self) -> None:
        self.sequence_no: int | None = None


def _event_type_value(event: object) -> str:
    event_type = getattr(event, "event_type", "")
    value = getattr(event_type, "value", None)
    if isinstance(value, str):
        return value
    return str(event_type)


__all__ = ["SurfaceViewCoordinator"]
