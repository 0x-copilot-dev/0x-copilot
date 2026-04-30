"""Async worker loop for durable runtime commands."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4

from agent_runtime.api.ports import EventStorePort, PersistencePort, RuntimeQueuePort
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim, RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import RuntimeApprovalResolvedCommand, RuntimeCancelCommand, RuntimeRunCommand
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.cancel import RuntimeCancelHandler
from runtime_worker.handlers.run import RuntimeRunHandler


class RuntimeWorker:
    """Claim and process queued runtime commands with bounded concurrency."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        queue: RuntimeQueuePort,
        settings: RuntimeSettings | None = None,
        worker_id: str | None = None,
        lock_seconds: int = 60,
        retry_delay_seconds: float = 1,
        run_handler: RuntimeRunHandler | None = None,
        cancel_handler: RuntimeCancelHandler | None = None,
        approval_handler: RuntimeApprovalHandler | None = None,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.queue = queue
        self.settings = settings or RuntimeSettings.load()
        self.worker_id = worker_id or f"runtime-worker-{uuid4().hex[:8]}"
        self.lock_seconds = lock_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.run_handler = run_handler or RuntimeRunHandler(
            persistence=persistence,
            event_store=event_store,
            settings=self.settings,
        )
        self.cancel_handler = cancel_handler or RuntimeCancelHandler(
            persistence=persistence,
            event_store=event_store,
        )
        self.approval_handler = approval_handler or RuntimeApprovalHandler(
            persistence=persistence,
            event_store=event_store,
        )
        self._semaphore = asyncio.Semaphore(self.settings.execution.max_parallel_runs)
        self.logger = logging.getLogger("runtime_worker")

    async def run_once(self) -> bool:
        """Claim and process one command, returning whether work was found."""

        claim = self._claim_next()
        if claim is None:
            return False
        async with self._semaphore:
            await self._handle_claim(claim)
        return True

    async def run_until_idle(self) -> int:
        """Process commands until the queue has no immediately claimable work."""

        processed = 0
        while True:
            claims = self._claim_batch()
            if not claims:
                return processed
            await asyncio.gather(*(self._handle_claim_with_limit(claim) for claim in claims))
            processed += len(claims)

    def _claim_next(self) -> RuntimeWorkerClaim | None:
        return self.queue.claim_next(
            worker_id=self.worker_id,
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=self.lock_seconds),
        )

    def _claim_batch(self) -> tuple[RuntimeWorkerClaim, ...]:
        claims: list[RuntimeWorkerClaim] = []
        for _ in range(self.settings.execution.max_parallel_runs):
            claim = self._claim_next()
            if claim is None:
                break
            claims.append(claim)
        return tuple(claims)

    async def _handle_claim_with_limit(self, claim: RuntimeWorkerClaim) -> None:
        async with self._semaphore:
            await self._handle_claim(claim)

    async def run_forever(self, *, poll_interval_seconds: float = 1.0) -> None:
        """Continuously process queue claims."""

        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)

    async def _handle_claim(self, claim: RuntimeWorkerClaim) -> None:
        try:
            await self._dispatch(claim)
        except AgentRuntimeError as exc:
            self.logger.exception(
                "runtime worker command failed command_id=%s command_type=%s run_id=%s",
                claim.command_id,
                claim.command_type,
                claim.run_id,
            )
            self._mark_failure(claim=claim, error=exc)
            return
        except Exception as exc:
            self.logger.exception(
                "runtime worker command crashed command_id=%s command_type=%s run_id=%s",
                claim.command_id,
                claim.command_type,
                claim.run_id,
            )
            safe_error = AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Runtime worker command failed safely.",
                retryable=True,
            )
            self._mark_failure(claim=claim, error=safe_error)
            return
        self.queue.mark_complete(
            result=RuntimeWorkerResult(command_id=claim.command_id, succeeded=True)
        )

    async def _dispatch(self, claim: RuntimeWorkerClaim) -> None:
        command_type = claim.command_type
        if command_type == PersistenceValues.EventType.RUN_REQUESTED:
            command = self._runtime_run_command(claim)
            await self.run_handler.handle(command)
            return
        if command_type == PersistenceValues.EventType.RUN_CANCEL_REQUESTED:
            command = self._runtime_cancel_command(claim)
            await self.cancel_handler.handle(command)
            return
        if command_type == PersistenceValues.EventType.APPROVAL_RESOLVED:
            command = self._runtime_approval_command(claim)
            await self.approval_handler.handle(command)
            return
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            f"Unsupported worker command type '{command_type}'.",
            retryable=False,
        )

    def _mark_failure(self, *, claim: RuntimeWorkerClaim, error: AgentRuntimeError) -> None:
        result = RuntimeWorkerResult(
            command_id=claim.command_id,
            succeeded=False,
            safe_error=error.to_envelope(),
            retry_available_at=datetime.now(UTC) + timedelta(seconds=self.retry_delay_seconds),
        )
        if error.retryable and claim.attempts <= self.settings.execution.max_retries:
            self.queue.mark_retry(result=result)
            return
        self.queue.mark_dead_letter(result=result)

    def _runtime_run_command(self, claim: RuntimeWorkerClaim) -> RuntimeRunCommand:
        if "runtime_context" in claim.payload:
            return RuntimeRunCommand.model_validate(self._command_payload(claim))
        command = self._command_from_store(claim, getattr(self.queue, "run_commands", ()))
        if isinstance(command, RuntimeRunCommand):
            return command
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Run command payload is unavailable.",
            retryable=False,
        )

    def _runtime_cancel_command(self, claim: RuntimeWorkerClaim) -> RuntimeCancelCommand:
        if "requested_by_user_id" in claim.payload:
            return RuntimeCancelCommand.model_validate(self._command_payload(claim))
        command = self._command_from_store(claim, getattr(self.queue, "cancel_commands", ()))
        if isinstance(command, RuntimeCancelCommand):
            return command
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Cancel command payload is unavailable.",
            retryable=False,
        )

    def _runtime_approval_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeApprovalResolvedCommand:
        if "decision" in claim.payload:
            return RuntimeApprovalResolvedCommand.model_validate(self._command_payload(claim))
        command = self._command_from_store(claim, getattr(self.queue, "approval_commands", ()))
        if isinstance(command, RuntimeApprovalResolvedCommand):
            return command
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Approval command payload is unavailable.",
            retryable=False,
        )

    @staticmethod
    def _command_from_store(claim: RuntimeWorkerClaim, commands: object) -> object | None:
        for command in commands:
            if getattr(command, "command_id", None) == claim.command_id:
                return command
        return None

    @staticmethod
    def _command_payload(claim: RuntimeWorkerClaim) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in claim.payload.items():
            if key == "command_type":
                continue
            if key == "approval_id" and value is None:
                continue
            payload[key] = value
        return payload
