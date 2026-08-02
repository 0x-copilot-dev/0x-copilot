"""Queued cancel command handling."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.budgets import BudgetCharger
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminalRunObserverPort,
    TerminationReason,
)
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.observability.usage_recorder import (
    NullUsageRecorder,
    UsageRecorder,
)
from agent_runtime.surfaces_v2.config import SurfacesV2Flag
from runtime_api.schemas import (
    ACTIVE_RUN_STATUSES,
    AgentRunStatus,
    RuntimeCancelCommand,
)
from runtime_worker.batch_concurrency_composition import LiveBatchAdmissionRegistry
from runtime_worker.handlers.receipt_hook import emit_receipt_if_enabled
from runtime_worker.model_invocation_terminal import ModelInvocationTerminalIntegration
from runtime_worker.run_cancellation import LiveRunRegistry
from runtime_worker.run_control import RunControlPlaneBuilder
from runtime_worker.run_metrics import AssistantRunMetrics


class RuntimeCancelHandler:
    """Apply a queued cancellation request."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        terminal_run_observer: TerminalRunObserverPort | None = None,
        run_control_builder: RunControlPlaneBuilder | None = None,
        model_invocation_store: ModelInvocationStorePort | None = None,
        usage_recorder: UsageRecorder | None = None,
        model_invocation_terminal: ModelInvocationTerminalIntegration | None = None,
        live_batch_admissions: LiveBatchAdmissionRegistry | None = None,
        live_runs: LiveRunRegistry | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
        )
        self.run_termination = RunTerminationCoordinator(
            event_producer=self.event_producer,
            terminal_observer=terminal_run_observer,
        )
        self._run_control_builder = run_control_builder
        # F6 batch work executing in *this* process for the run being cancelled.
        # ``None`` — the default, and what an unconfigured deployment gets —
        # means this handler behaves exactly as it did before W4.
        self._live_batch_admissions = live_batch_admissions
        # The run itself executing in *this* process. Stopping it is the only
        # thing that stops an in-process subagent, because the ``task`` tool
        # awaits the child graph inside the parent's own tool call.
        self._live_runs = live_runs
        self._budget_charger = BudgetCharger(self.persistence)
        self.usage_recorder: UsageRecorder = usage_recorder or NullUsageRecorder()
        self._model_invocation_terminal = (
            model_invocation_terminal
            or ModelInvocationTerminalIntegration(
                journal=model_invocation_store,
                usage_recorder=self.usage_recorder,
                persistence=self.persistence,
            )
        )

    async def handle(self, command: RuntimeCancelCommand) -> None:
        """Cancel the run if it exists and the requester is the run's owner; otherwise no-ops."""
        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None:
            return
        if run.user_id != command.requested_by_user_id:
            return
        subject_fingerprint = (
            self._run_control_builder.subject_fingerprint_for(run)
            if self._run_control_builder is not None
            else None
        )
        if run.status is not AgentRunStatus.CANCELLED:
            # ``ACTIVE_RUN_STATUSES`` rather than a re-typed tuple, and that is
            # the whole fix for the bug this guard used to be: the re-typed copy
            # omitted ``CANCELLING``, which is the only status a cancel command
            # ever arrives on — ``RunCoordinator.cancel_run`` flips the run to
            # ``cancelling`` *before* enqueueing the command. So this handler
            # returned early on every Stop a user ever pressed, and the run sat
            # in ``cancelling`` until it finished on its own. The shared
            # constant exists precisely to stop this literal drifting.
            if run.status not in ACTIVE_RUN_STATUSES:
                return
            # W4 — F6.6's cancellation, on the path a run is actually cancelled
            # by. Its position before the status flip and the terminal event is
            # not stylistic. Two things follow from it, in this order of
            # importance:
            #
            # 1. Admission stops at the earliest moment this process knows the
            #    run is over, so the window in which another child can still be
            #    admitted is as short as the handler can make it.
            # 2. The ``indeterminate`` transitions land while the run's stream
            #    is still open. The batch journal writes through the raw event
            #    store rather than ``append_api_event``, so the causal-prefix
            #    seal would not *refuse* a later append — it would do something
            #    quieter and worse, leaving a durable fact that no live client
            #    ever receives, because the stream closes on the seal.
            #
            # Inside the status guard, so a repeated cancel command does not
            # re-cancel a run that is already terminal.
            await self._cancel_live_batches(run.run_id)
            # Then the run itself, and — because subagents are in-process —
            # every subagent it is awaiting. The drain is awaited *before* the
            # status flip and the terminal event on purpose: the run's own
            # cancellation path settles its in-flight tool calls and closes its
            # open subagent cards, and those are causal facts that must land
            # inside the sealed prefix. Emitted after the terminal event they
            # would be durable and invisible, because a client's stream closes
            # on the seal. See ``agent_runtime.api.ledger_seal``.
            await self._cancel_live_run(run.run_id)
            run = await with_optimistic_retry(
                lambda: self.persistence.update_run_status(
                    run_id=command.run_id,
                    status=AgentRunStatus.CANCELLED,
                )
            )
            # Generative Surfaces v2 (PRD-E1): a cancelled run's receipt matters
            # most. Fold + append the receipt before the terminal event, gated on
            # SURFACES_V2 (flag-off ⇒ no-op, byte-identical to today).
            await emit_receipt_if_enabled(
                enabled=SurfacesV2Flag.enabled(),
                event_producer=self.event_producer,
                event_store=self.event_store,
                run=run,
            )
            await self.run_termination.terminate(
                run=run,
                terminal_status=AgentRunStatus.CANCELLED,
                reason=TerminationReason.CANCELLED,
                summary="Run cancelled",
                extra_payload={"cancel_reason": command.reason},
            )
        completed_at = run.completed_at or datetime.now(timezone.utc)
        metrics = AssistantRunMetrics.from_run(run)
        await self._model_invocation_terminal.finalize(
            run=run,
            metrics=metrics,
            subject_fingerprint=subject_fingerprint,
            completed_at=completed_at,
        )
        observed_cost = await self._model_invocation_terminal.record_run_usage(
            run=run,
            metrics=metrics,
            completed_at=completed_at,
            status=run.status.value,
        )
        try:
            await self._budget_charger.charge_run(
                org_id=run.org_id,
                user_id=run.user_id,
                run_id=run.run_id,
                observed_micro_usd=observed_cost,
                observed_tokens=metrics.to_usage_record(
                    run,
                    completed_at=completed_at,
                    status=run.status.value,
                ).total_tokens,
            )
        except Exception:
            # Cancellation must remain terminal even when a post-run budget
            # observation cannot be recorded; the charge is idempotent by run.
            pass

    async def _cancel_live_batches(self, run_id: str) -> None:
        """Stop this run's F6 batch work in this process, if it is here.

        A miss is the ordinary case, not a fault: F6 may be unconfigured, the
        run may have planned no batch, or — in a multi-worker deployment — the
        cancel command may simply have been claimed by a process that is not the
        one executing the run. All three answer ``None`` and leave this handler
        doing exactly what it did before, which is the honest degradation. It is
        deliberately not papered over with a broadcast: reaching a coordinator in
        another process is a real distributed problem, and pretending to have
        solved it here would be worse than not solving it.
        """

        if self._live_batch_admissions is None:
            return
        admission = self._live_batch_admissions.admission_for(run_id)
        if admission is None:
            return
        # ``acancel`` is total, so no exception from a run that is already over
        # can stop it from becoming terminal.
        await admission.acancel()

    async def _cancel_live_run(self, run_id: str) -> None:
        """Stop this run's graph execution in this process, if it is here.

        The same honest degradation as ``_cancel_live_batches``: a miss is the
        ordinary multi-worker case, not a fault, and it leaves this handler
        doing exactly what it did before — record the cancellation and let the
        run finish wherever it is actually running. What a miss must never do is
        pretend, so nothing here reports a stopped run it did not stop.
        """

        if self._live_runs is None:
            return
        await self._live_runs.cancel(run_id)
