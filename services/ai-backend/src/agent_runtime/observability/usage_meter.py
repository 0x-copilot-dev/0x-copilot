"""UsageMeter recording seam (Generative Surfaces v2, PRD-A2, ../02-sdr.md §8).

D1 splits the SDR's "one ``MeteredModelInvocation`` wrapper" phrase into two
enforced halves because wrapping the shared ``BaseChatModel`` per-call would
double-count the streaming path (usage is chunk-accumulated per ``message.id``
with field-wise-max merge — ``token_usage.py``):

1. **Construction seam** — every model is built via ``build_chat_model`` /
   ``build_chat_model_from_id`` / ``build_embeddings_model`` in
   ``deep_agent_builder.py``; pinned by the pre-commit AST guard plus the
   in-suite gate test (``tests/unit/test_llm_seam_gate.py``).
2. **Recording seam (this module)** — one port, :class:`UsageMeter`, through
   which every usage observation flows to (a) the row store via the existing
   :class:`UsageRecorder` (query index) and (b) the Work Ledger as a
   ``usage.recorded`` event when ``SURFACES_V2`` is on (audit truth). The
   streaming accumulator and the non-streamed callers are feeders into this
   single port.

Fail-soft discipline mirrors :class:`PostgresUsageRecorder`: a ledger-emit
failure is logged (structured, ``safe_message``, ids/counts only — never
content) and swallowed. Usage attribution must never break a run.

The ledger ``purpose`` is the closed 4-value vocabulary (SDR §5), not the
store's 14-value :class:`Purpose`; :meth:`UsageMeter.ledger_purpose_for` is the
exhaustive-over-``Purpose`` mapping (background jobs → ``None`` = row only, no
ledger event: they are not part of the run's canvas story).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
import logging
from typing import ClassVar

from agent_runtime.execution.contracts import JsonObject
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_recorder import UsageRecorder
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from agent_runtime.surfaces_v2.ledger_models import UsagePurpose
from copilot_service_contracts.work_ledger import LEDGER_PAYLOAD_VERSION
from runtime_api.schemas import RunRecord


class _PayloadKeys:
    """``usage.recorded`` payload field names (SDR §5 verbatim).

    Class-scoped so the wire keys travel with the module — never inline
    strings (ai-backend rule). Re-filtered on append by the projector's
    ``_usage_recorded_payload`` allow-list.
    """

    V = "v"
    PURPOSE = "purpose"
    MODEL = "model"
    TOKENS_IN = "tokens_in"
    TOKENS_OUT = "tokens_out"
    SURFACE_ID = "surface_id"

    # ``<provider>:<model_name>`` join used for the ``model`` field.
    MODEL_JOIN = ":"


class _MeterLogger:
    """Class-scoped structured-log event names for the recording seam."""

    EVENT_EMIT_FAILED = "usage_recorded_emit_failed"
    EVENT_RECORD_BUILD_FAILED = "usage_meter_record_build_failed"
    SAFE_EMIT = "usage.recorded ledger emit failed"
    SAFE_BUILD = "usage meter could not build a call record"


class UsageMeter:
    """Recording port: usage row (always) + ``usage.recorded`` event (gated).

    :meth:`record_call` writes the query-index row through the injected
    :class:`UsageRecorder`, then — only when ``surfaces_v2`` is on, an
    ``emit_event`` closure is wired, and the row's ``purpose`` maps to a
    non-``None`` ledger purpose — emits the SDR §5 ``usage.recorded`` payload.
    Both writes are fail-soft: the recorder swallows row failures, and the emit
    closure's failures are logged and swallowed here.
    """

    # Purpose (store, 14 values) → UsagePurpose (ledger, 4 values) | None.
    # Exhaustive over ``Purpose`` (pinned by
    # ``test_ledger_purpose_mapping_is_exhaustive_over_purpose_enum``).
    # ``None`` = row only, no ledger event (background jobs are not part of the
    # run's canvas story: todo/library/memory/palette extraction).
    _PURPOSE_TO_LEDGER: ClassVar[Mapping[Purpose, UsagePurpose | None]] = {
        Purpose.MAIN: UsagePurpose.RUN,
        Purpose.TOOL_PLANNING: UsagePurpose.RUN,
        Purpose.TOOL_INTERPRETATION: UsagePurpose.RUN,
        Purpose.CONTEXT_COMPRESSION: UsagePurpose.RUN,
        Purpose.SUBAGENT_WORK: UsagePurpose.SUBAGENT,
        Purpose.VIEW_SHAPING: UsagePurpose.VIEW_SHAPING,
        Purpose.SHAPE_REQUEST: UsagePurpose.SHAPE_REQUEST,
        Purpose.TODO_EXTRACTION: None,
        Purpose.LIBRARY_RETRIEVAL: None,
        Purpose.LIBRARY_INDEXING: None,
        Purpose.PALETTE_RANKING: None,
        Purpose.MEMORY_RETRIEVAL: None,
        Purpose.MEMORY_INDEXING: None,
        Purpose.MEMORY_EXTRACTION: None,
    }

    def __init__(
        self,
        *,
        recorder: UsageRecorder,
        emit_event: Callable[[JsonObject], Awaitable[None]] | None,
        surfaces_v2: bool,
        logger: logging.Logger | None = None,
    ) -> None:
        self._recorder = recorder
        self._emit_event = emit_event
        self._surfaces_v2 = surfaces_v2
        self._logger = logger or logging.getLogger("agent_runtime.usage_meter")

    @classmethod
    def ledger_purpose_for(cls, purpose: str) -> UsagePurpose | None:
        """Map a store ``purpose`` string to its ledger purpose, or ``None``.

        ``None`` means "write the row, emit no ledger event" — both for the
        background-job purposes and for any unknown/legacy string (fail-soft:
        an unrecognised purpose never crashes the seam).
        """

        try:
            store_purpose = Purpose(purpose)
        except ValueError:
            return None
        return cls._PURPOSE_TO_LEDGER.get(store_purpose)

    async def record_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> None:
        """Write the usage row, then emit ``usage.recorded`` when in-contract.

        The row write is unconditional (additive, invisible to flag-off flows —
        FR-G4). The ledger emit is gated on ``surfaces_v2`` AND a wired emitter
        AND a non-``None`` ledger purpose.
        """

        await self._recorder.record_call(record, pricing_at=pricing_at)
        if not self._surfaces_v2 or self._emit_event is None:
            return
        ledger_purpose = self.ledger_purpose_for(record.purpose)
        if ledger_purpose is None:
            return
        payload = self._usage_recorded_payload(record, ledger_purpose)
        try:
            await self._emit_event(payload)
        except Exception:
            self._logger.warning(
                _MeterLogger.EVENT_EMIT_FAILED,
                extra={
                    "safe_message": _MeterLogger.SAFE_EMIT,
                    "metadata": {
                        "run_id": record.run_id,
                        "purpose": record.purpose,
                    },
                },
                exc_info=True,
            )

    @classmethod
    def _usage_recorded_payload(
        cls,
        record: RuntimeModelCallUsageRecord,
        ledger_purpose: UsagePurpose,
    ) -> JsonObject:
        """Build the SDR §5 ``usage.recorded`` payload from the written row."""

        return cls.build_ledger_payload(
            ledger_purpose=ledger_purpose,
            model=(
                f"{record.model_provider}{_PayloadKeys.MODEL_JOIN}{record.model_name}"
            ),
            tokens_in=record.input_tokens,
            tokens_out=record.output_tokens,
            surface_id=record.surface_id,
        )

    @classmethod
    def build_ledger_payload(
        cls,
        *,
        ledger_purpose: UsagePurpose,
        model: str,
        tokens_in: int,
        tokens_out: int,
        surface_id: str | None = None,
    ) -> JsonObject:
        """Build the SDR §5 ``usage.recorded`` payload from primitives.

        Shared by :meth:`record_call` (non-streamed callers) and the streaming
        executor's per-call emit hook so the wire keys live in exactly one place
        (``_PayloadKeys``). ``surface_id`` is omitted when ``None`` (optional in
        the SDR §5 schema); the projector re-filters this on append.
        """

        payload: JsonObject = {
            _PayloadKeys.V: LEDGER_PAYLOAD_VERSION,
            _PayloadKeys.PURPOSE: ledger_purpose.value,
            _PayloadKeys.MODEL: model,
            _PayloadKeys.TOKENS_IN: tokens_in,
            _PayloadKeys.TOKENS_OUT: tokens_out,
        }
        if surface_id is not None:
            payload[_PayloadKeys.SURFACE_ID] = surface_id
        return payload


class MeteredModelInvocation:
    """Non-streamed adapter: build a per-call row from reported token counts.

    Bound to one run's attribution (org/user/conversation/run/trace). Each
    :meth:`record_attempt` builds a :class:`RuntimeModelCallUsageRecord`
    directly (no :class:`UsageAttributionContext` — its invariants are
    stream-shaped) and feeds it to the :class:`UsageMeter` per attempt, so a
    retried shaping records real per-attempt spend (SDR §8 retry correctness).

    B4's shape-request path constructs this with ``purpose=Purpose.SHAPE_REQUEST``
    and a concrete ``surface_id``; spec generation (B3/A2) uses
    ``Purpose.VIEW_SHAPING`` with ``surface_id=None``.
    """

    def __init__(
        self,
        *,
        meter: UsageMeter,
        run: RunRecord,
        purpose: Purpose,
        logger: logging.Logger | None = None,
    ) -> None:
        self._meter = meter
        self._run = run
        self._purpose = purpose
        self._logger = logger or logging.getLogger("agent_runtime.usage_meter")

    async def record_attempt(
        self,
        *,
        model_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int,
        surface_id: str | None = None,
    ) -> None:
        """Record one model completion attempt. Never raises into the caller."""

        try:
            record = self._build_record(
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                surface_id=surface_id,
            )
        except Exception:
            # A malformed model id (or any record-build error) must not break a
            # run — usage is best-effort. Log ids/counts only, never content.
            self._logger.warning(
                _MeterLogger.EVENT_RECORD_BUILD_FAILED,
                extra={
                    "safe_message": _MeterLogger.SAFE_BUILD,
                    "metadata": {
                        "run_id": self._run.run_id,
                        "purpose": self._purpose.value,
                    },
                },
                exc_info=True,
            )
            return
        await self._meter.record_call(record, pricing_at=record.created_at)

    def _build_record(
        self,
        *,
        model_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int,
        surface_id: str | None,
    ) -> RuntimeModelCallUsageRecord:
        """Build the per-call row from the bound run + the reported counts.

        The per-call ``model_id`` is the shaping model (e.g. ``SURFACE_SPEC_MODEL``),
        NOT the run's main model, split into the row's separate
        ``model_provider`` / ``model_name`` columns via
        :meth:`SurfaceModelConfigFactory.from_id` (function-level import mirroring
        ``generator.py`` — not an ``init_chat_model`` reference, so the D7 seam
        gate stays green). ``None`` token counts record as ``0``.
        """

        from agent_runtime.execution.deep_agent_builder import (  # noqa: PLC0415
            SurfaceModelConfigFactory,
        )

        config = SurfaceModelConfigFactory.from_id(model_id)
        created_at = datetime.now(timezone.utc)
        return RuntimeModelCallUsageRecord(
            org_id=self._run.org_id,
            run_id=self._run.run_id,
            conversation_id=self._run.conversation_id,
            trace_id=self._run.trace_id,
            user_id=self._run.user_id,
            model_provider=config.provider,
            model_name=config.model_name,
            purpose=self._purpose.value,
            surface_id=surface_id,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            duration_ms=duration_ms,
            created_at=created_at,
        )


__all__ = [
    "MeteredModelInvocation",
    "UsageMeter",
]
