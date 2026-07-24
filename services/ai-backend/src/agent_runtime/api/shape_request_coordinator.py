"""ShapeRequestCoordinator — the API home of PRD-B4's "Suggest a shape".

The user-invited escape hatch when the automatic honest ladder ends at a raw or
generic view. ``POST /v1/agent/surfaces/{surface_id}/shape-request`` runs an
**immediate, higher-effort** shaping attempt (a bigger budget than the automatic
pass), reusing the shipped v1 generator. On success the generated SurfaceSpec is
persisted to the org shape registry (this surface upgrades now; every future
render of the tool is shaped) and the canvas merges the shaped view through the
same ``view.derived`` path B3's automatic upgrade uses. On failure the honest
fallback stays byte-identical.

Both the request and its outcome are ledgered (``shape.requested`` +
``shape.resolved``) and every model attempt is metered (``usage.recorded
{purpose: shape_request}``, A2 seam). The attempt runs **in the runtime_api
process** (the run may already be completed, so the worker's per-run scheduler
binding is unavailable) as an asyncio task; the outcome arrives over the existing
run SSE stream — there is no polling endpoint.

Keyed on ``surface_id`` + the owning ``run_id`` (SDR §4): A3 ships no
``surface_id → run_id`` index and the canvas already holds ``run_id``. Flag-off
is byte-identical: with ``SURFACES_V2`` off no v2 surfaces exist, so the route
404s without appending anything.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

from starlette import status

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.capabilities.surfaces.backend_store import build_surface_spec_store
from agent_runtime.capabilities.surfaces.generator import (
    SpecAuthoringSkill,
    SpecCompletionPort,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.shape_request import (
    InvitedShapeAttempt,
    ShapeRequestRunner,
)
from agent_runtime.execution.contracts import RuntimeErrorCode, StreamEventSource
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_meter import MeteredModelInvocation, UsageMeter
from agent_runtime.observability.usage_recorder import NullUsageRecorder, UsageRecorder
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from agent_runtime.surfaces_v2.constants import Keys, Messages, Values
from agent_runtime.surfaces_v2.content import SurfaceContentProjection
from agent_runtime.surfaces_v2.ledger_models import ShapeOutcome, ViewTier
from agent_runtime.surfaces_v2.projection import SurfaceSnapshot, SurfaceStoreProjection
from agent_runtime.surfaces_v2.view_deriver import _SurfaceScopedInvocation
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_api.schemas.surfaces_v2 import ShapeRequestAccepted

_LOGGER = logging.getLogger(__name__)

# A schedule seam: take the runner coroutine, arrange to run it, return the task.
# Production passes ``asyncio.create_task``; tests inject a collector so the
# in-flight guard + task lifecycle are asserted deterministically.
ScheduleShapeFn = Callable[[Coroutine[Any, Any, None]], "asyncio.Task[None]"]


class _CoordMessages:
    """Safe public messages surfaced through the typed API errors."""

    SURFACE_NOT_FOUND = "surface_not_found"
    RUN_NOT_FOUND = "run_not_found"
    ALREADY_SHAPED = "surface_already_shaped"
    IN_FLIGHT = "shape_request_in_flight"
    SHAPING_UNAVAILABLE = "shaping_unavailable"


class _StateKey:
    """Keys read out of the B2 content fold's ``{spec?, data}`` state."""

    DATA = "data"


# Tiers a fallback surface may be shaped from (the button only shows on these).
_SHAPEABLE_TIERS: frozenset[str] = frozenset(
    {ViewTier.RAW.value, ViewTier.GENERIC.value}
)


class ShapeRequestCoordinator:
    """Schedule + ledger the user-invited shaping attempt for one surface."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        event_producer,
        completion: SpecCompletionPort | None = None,
        usage_recorder: UsageRecorder | None = None,
        environ: Mapping[str, str] | None = None,
        schedule: ScheduleShapeFn | None = None,
    ) -> None:
        self._persistence = persistence
        self._event_store = event_store
        self._event_producer = event_producer
        # Injectable completion for tests (no live model); production builds one
        # from the resolved shaping model id on demand.
        self._completion = completion
        self._usage_recorder: UsageRecorder = usage_recorder or NullUsageRecorder()
        self._environ: Mapping[str, str] = (
            environ if environ is not None else os.environ
        )
        self._schedule: ScheduleShapeFn = schedule or self._default_schedule
        # Per-surface in-flight guard (single-process; the runtime_api owns the
        # canvas surface). A second POST while a task runs 409s.
        self._inflight: dict[str, asyncio.Task[None]] = {}

    async def request_shape(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        surface_id: str,
    ) -> ShapeRequestAccepted:
        """Validate, ledger ``shape.requested``, schedule the invited attempt."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events, snapshot = await self._load_surface(
            org_id=org_id, run_id=run_id, surface_id=surface_id
        )
        self._guard_tier(snapshot)
        self._guard_in_flight(surface_id)

        model_id = InvitedShapeAttempt.resolve_model_id(
            environ=self._environ, run_provider=run.model_provider
        )
        if model_id is None:
            # Checked BEFORE emitting shape.requested — nothing is ledgered for a
            # request that can never start (BYOK posture, SDR §13 #1).
            raise self._error(
                _CoordMessages.SHAPING_UNAVAILABLE,
                RuntimeErrorCode.VALIDATION_ERROR,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        sample_output = self._stored_payload(events, surface_id=surface_id)
        if sample_output is None:
            raise self._surface_not_found()

        await self._emit_requested(run, surface_id=surface_id)

        runner = self._build_runner(
            org_id=org_id,
            user_id=user_id,
            run=run,
            surface_id=surface_id,
            model_id=model_id,
        )
        coro = self._run_and_finalize(
            runner=runner,
            run=run,
            server=snapshot.connector,
            tool=snapshot.op,
            sample_output=sample_output,
            surface_id=surface_id,
        )
        self._inflight[surface_id] = self._schedule(coro)
        return ShapeRequestAccepted(surface_id=surface_id)

    # -- runner assembly ----------------------------------------------------

    def _build_runner(
        self,
        *,
        org_id: str,
        user_id: str,
        run: RunRecord,
        surface_id: str,
        model_id: str,
    ) -> ShapeRequestRunner:
        """Build the budget-raised, meter-wired runner for the invited attempt.

        The generator is constructed with a bigger retry budget
        (``with_max_retries``) and an A2 ``MeteredModelInvocation`` bound to this
        run + ``purpose=shape_request`` + this ``surface_id``, so every attempt
        (incl. retries) records a metered ``usage.recorded {purpose:
        shape_request}`` row carrying the surface (DoD).
        """

        store = build_surface_spec_store(
            environ=self._environ, org_id=org_id, user_id=user_id
        )
        skill = SpecAuthoringSkill.load().with_max_retries(
            InvitedShapeAttempt.max_retries(self._environ)
        )
        invocation = MeteredModelInvocation(
            meter=UsageMeter(
                recorder=self._usage_recorder,
                emit_event=self._make_usage_emitter(run),
                surfaces_v2=SurfacesV2Flag.enabled(self._environ),
            ),
            run=run,
            purpose=Purpose.SHAPE_REQUEST,
        )
        scoped = _SurfaceScopedInvocation(invocation=invocation, surface_id=surface_id)
        generator = SurfaceSpecGenerator(
            completion=self._completion_for(model_id),
            skill=skill,
            usage_meter=scoped,
        )
        return ShapeRequestRunner(
            generator=generator,
            store=store,
            emit=self._make_emit(run),
            model_id=model_id,
        )

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

    async def _run_and_finalize(
        self,
        *,
        runner: ShapeRequestRunner,
        run: RunRecord,
        server: str,
        tool: str,
        sample_output: object,
        surface_id: str,
    ) -> None:
        """Await the invited attempt; a crash resolves ``no_fit`` (never hung)."""

        try:
            await runner.run(
                server=server,
                tool=tool,
                sample_output=sample_output,
                surface_id=surface_id,
            )
        except Exception:  # noqa: BLE001 - an invited attempt must never hang "requested"
            _LOGGER.warning(
                "[surfaces_v2] shape_request.runner_raised",
                extra={"safe_message": "shape request runner failed"},
                exc_info=True,
            )
            await self._safe_emit_no_fit(run, surface_id=surface_id)
        finally:
            self._inflight.pop(surface_id, None)

    async def _safe_emit_no_fit(self, run: RunRecord, *, surface_id: str) -> None:
        # A runner crash still records an honest, closed ``no_fit`` outcome so the
        # surface is never left in a hung "requested" state.
        payload: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.OUTCOME: ShapeOutcome.NO_FIT.value,
            Keys.Field.REASON: Messages.SHAPE_NO_FIT_REASON,
        }
        try:
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.SHAPE_RESOLVED,
                payload=payload,
                summary=Messages.SHAPE_RESOLVED,
            )
        except Exception:  # noqa: BLE001 - fail-soft; the row is best-effort
            _LOGGER.warning("[surfaces_v2] shape_request.no_fit_emit_failed")

    def _default_schedule(
        self, coro: Coroutine[Any, Any, None]
    ) -> "asyncio.Task[None]":
        return asyncio.create_task(coro)

    # -- emit closures ------------------------------------------------------

    async def _emit_requested(self, run: RunRecord, *, surface_id: str) -> None:
        payload = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: surface_id,
            Keys.Field.ACTOR: Values.ACTOR_USER,
        }
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.SHAPE_REQUESTED,
            payload=payload,
            summary=Messages.SHAPE_REQUESTED,
        )

    def _make_emit(self, run: RunRecord):
        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType(str(event_type_value)),
                payload=dict(payload),
                summary=summary,
            )

        return _emit

    def _make_usage_emitter(self, run: RunRecord) -> Callable[..., Awaitable[None]]:
        async def _emit_usage(payload: Mapping[str, object]) -> None:
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.USAGE_RECORDED,
                payload=dict(payload),
            )

        return _emit_usage

    # -- guards + ledger reads ----------------------------------------------

    def _guard_tier(self, snapshot: SurfaceSnapshot) -> None:
        """409 when the surface already carries a shaped view (nothing to invite)."""

        view = snapshot.view
        tier = view.tier if view is not None else None
        if tier is not None and tier not in _SHAPEABLE_TIERS:
            raise self._error(
                _CoordMessages.ALREADY_SHAPED,
                RuntimeErrorCode.VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )

    def _guard_in_flight(self, surface_id: str) -> None:
        existing = self._inflight.get(surface_id)
        if existing is not None and not existing.done():
            raise self._error(
                _CoordMessages.IN_FLIGHT,
                RuntimeErrorCode.VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if existing is not None:
            # A finished task lingering in the map — drop it so a re-invite works.
            self._inflight.pop(surface_id, None)

    async def _load_surface(
        self, *, org_id: str, run_id: str, surface_id: str
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
        """The stored tool-output payload for a surface (B2 content fold)."""

        content = SurfaceContentProjection.fold(events)
        state = content.get(surface_id)
        if not isinstance(state, Mapping):
            return None
        data = state.get(_StateKey.DATA)
        return data if data is not None else dict(state)

    async def _run_for_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunRecord:
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise self._error(
                _CoordMessages.RUN_NOT_FOUND,
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                status.HTTP_404_NOT_FOUND,
            )
        return run

    # -- typed errors -------------------------------------------------------

    @staticmethod
    def _error(
        message: str, code: RuntimeErrorCode, http_status: int
    ) -> RuntimeApiError:
        return RuntimeApiError(code, message, http_status=http_status, retryable=False)

    @staticmethod
    def _surface_not_found() -> RuntimeApiError:
        return RuntimeApiError(
            RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            _CoordMessages.SURFACE_NOT_FOUND,
            http_status=status.HTTP_404_NOT_FOUND,
            retryable=False,
        )


__all__ = ["ShapeRequestCoordinator"]
