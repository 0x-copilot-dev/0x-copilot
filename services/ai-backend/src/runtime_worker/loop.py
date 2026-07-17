"""Async worker loop for durable runtime commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from opentelemetry import trace as otel_trace

from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
)
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim, RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeRunCommand,
)
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.cancel import RuntimeCancelHandler
from agent_runtime.persistence.ports import (
    CitationStorePort,
    ConversationToolOrdinalStorePort,
    DraftStorePort,
)
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
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
        on_event_appended: Callable[[str], None] | None = None,
        draft_store: "DraftStorePort | None" = None,
        conversation_tool_ordinal_store: (
            "ConversationToolOrdinalStorePort | None"
        ) = None,
        mcp_discovery_cache: object | None = None,
        user_policies_resolver: object | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.queue: RuntimeQueuePort = queue
        self.settings = settings or RuntimeSettings.load()
        self.worker_id = worker_id or f"runtime-worker-{uuid4().hex[:8]}"
        self.lock_seconds = lock_seconds
        self.retry_delay_seconds = retry_delay_seconds
        # Reuse the same pool when the adapter satisfies CitationStorePort (Postgres);
        # fall back to an in-memory sibling for dev and unit tests.
        citation_store: CitationStorePort = (
            self.persistence
            if isinstance(self.persistence, CitationStorePort)
            else InMemoryCitationStore()
        )
        # Defaults to an in-memory adapter for dev/tests; production injects a Postgres
        # adapter that shares the main connection pool.
        self.conversation_tool_ordinal_store: ConversationToolOrdinalStorePort = (
            conversation_tool_ordinal_store or InMemoryConversationToolOrdinalStore()
        )
        # Process-wide MCP discovery cache (when wired). Forwarded into the
        # default run / approval handler dependencies factories so every
        # ``McpLoader`` built for a run in this process shares one cache.
        self.mcp_discovery_cache = mcp_discovery_cache
        self.run_handler = run_handler or RuntimeRunHandler(
            persistence=self.persistence,
            event_store=self.event_store,
            settings=self.settings,
            on_event_appended=on_event_appended,
            citation_store=citation_store,
            draft_store=draft_store,
            conversation_tool_ordinal_store=self.conversation_tool_ordinal_store,
            mcp_discovery_cache=mcp_discovery_cache,
            user_policies_resolver=user_policies_resolver,  # type: ignore[arg-type]
        )
        self.cancel_handler = cancel_handler or RuntimeCancelHandler(
            persistence=self.persistence,
            event_store=self.event_store,
        )
        self.approval_handler = approval_handler or RuntimeApprovalHandler(
            persistence=self.persistence,
            event_store=self.event_store,
            settings=self.settings,
            on_event_appended=on_event_appended,
            draft_store=draft_store,
            conversation_tool_ordinal_store=self.conversation_tool_ordinal_store,
            mcp_discovery_cache=mcp_discovery_cache,
            user_policies_resolver=user_policies_resolver,  # type: ignore[arg-type]
        )
        self._semaphore = asyncio.Semaphore(self.settings.execution.max_parallel_runs)
        self.logger = logging.getLogger("runtime_worker")

    async def run_once(self) -> bool:
        """Claim and process one command, returning whether work was found."""

        claim = await self._claim_next()
        if claim is None:
            return False
        async with self._semaphore:
            await self._handle_claim(claim)
        return True

    async def run_until_idle(self) -> int:
        """Process commands until the queue has no immediately claimable work."""

        processed = 0
        while True:
            claims = await self._claim_batch()
            if not claims:
                return processed
            await asyncio.gather(
                *(self._handle_claim_with_limit(claim) for claim in claims)
            )
            processed += len(claims)

    async def _claim_next(self) -> RuntimeWorkerClaim | None:
        """Attempt to claim one command from the queue; returns ``None`` when the queue is empty."""
        return await self.queue.claim_next(
            worker_id=self.worker_id,
            lock_expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=self.lock_seconds),
        )

    async def _claim_batch(self) -> tuple[RuntimeWorkerClaim, ...]:
        """Claim up to ``max_parallel_runs`` commands in one pass."""
        claims: list[RuntimeWorkerClaim] = []
        for _ in range(self.settings.execution.max_parallel_runs):
            claim = await self._claim_next()
            if claim is None:
                break
            claims.append(claim)
        return tuple(claims)

    async def _handle_claim_with_limit(self, claim: RuntimeWorkerClaim) -> None:
        """Acquire the concurrency semaphore then dispatch the claim."""
        async with self._semaphore:
            await self._handle_claim(claim)

    async def run_forever(self, *, poll_interval_seconds: float = 1.0) -> None:
        """Continuously process queue claims."""

        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)

    async def _handle_claim(self, claim: RuntimeWorkerClaim) -> None:
        """Dispatch the claim and mark it complete, retry, or dead-letter on error."""
        try:
            await self._dispatch(claim)
        except AgentRuntimeError as exc:
            self.logger.exception(
                "runtime worker command failed command_id=%s command_type=%s run_id=%s",
                claim.command_id,
                claim.command_type,
                claim.run_id,
            )
            await self._mark_failure(claim=claim, error=exc)
            return
        except Exception:
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
            await self._mark_failure(claim=claim, error=safe_error)
            return
        await self.queue.mark_complete(
            result=RuntimeWorkerResult(command_id=claim.command_id, succeeded=True)
        )

    # Re-parent handler spans under the API's trace tree so one trace_id covers
    # ingress → enqueue → handler. When trace_propagation is absent (legacy
    # claim or sweeper), the span begins a fresh trace.
    _DISPATCH_SPAN_NAMES: dict[str, str] = {
        PersistenceValues.EventType.RUN_REQUESTED: "runtime_worker.run",
        PersistenceValues.EventType.RUN_CANCEL_REQUESTED: "runtime_worker.cancel",
        PersistenceValues.EventType.APPROVAL_RESOLVED: "runtime_worker.approval_resolved",
    }

    async def _dispatch(self, claim: RuntimeWorkerClaim) -> None:
        """Route a claimed command to the appropriate handler under the extracted OTel trace context."""
        command_type = claim.command_type
        carrier = claim.payload.get("trace_propagation")
        parent_ctx = QueueTracePropagator.extract(carrier)
        span_name = self._DISPATCH_SPAN_NAMES.get(
            command_type, f"runtime_worker.{command_type}"
        )
        tracer = otel_trace.get_tracer("agent_runtime.runtime_worker")
        with tracer.start_as_current_span(span_name, context=parent_ctx):
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

    async def _mark_failure(
        self, *, claim: RuntimeWorkerClaim, error: AgentRuntimeError
    ) -> None:
        """Mark the claim as failed; routes to retry or dead-letter based on the error and attempt count."""
        result = RuntimeWorkerResult(
            command_id=claim.command_id,
            succeeded=False,
            safe_error=error.to_envelope(),
            retry_available_at=datetime.now(timezone.utc)
            + timedelta(seconds=self.retry_delay_seconds),
        )
        if error.retryable and claim.attempts <= self.settings.execution.max_retries:
            await self.queue.mark_retry(result=result)
            return
        await self.queue.mark_dead_letter(result=result)

    def _runtime_run_command(self, claim: RuntimeWorkerClaim) -> RuntimeRunCommand:
        """Deserialise the claim payload into a ``RuntimeRunCommand``."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeRunCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Run command payload is unavailable.",
            retryable=False,
        )

    def _runtime_cancel_command(
        self, claim: RuntimeWorkerClaim
    ) -> RuntimeCancelCommand:
        """Deserialise the claim payload into a ``RuntimeCancelCommand``."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeCancelCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Cancel command payload is unavailable.",
            retryable=False,
        )

    def _runtime_approval_command(
        self,
        claim: RuntimeWorkerClaim,
    ) -> RuntimeApprovalResolvedCommand:
        """Deserialise the claim payload into a ``RuntimeApprovalResolvedCommand``."""
        payload = self._command_payload(claim)
        if payload:
            return RuntimeApprovalResolvedCommand.model_validate(payload)
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Approval command payload is unavailable.",
            retryable=False,
        )

    @staticmethod
    def _command_payload(claim: RuntimeWorkerClaim) -> dict[str, object]:
        """Extract the command payload from the claim, stripping internal metadata keys."""
        payload: dict[str, object] = {}
        for key, value in claim.payload.items():
            if key == "command_type":
                continue
            if key == "approval_id" and value is None:
                continue
            payload[key] = value
        return payload
