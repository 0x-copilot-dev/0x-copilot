"""Async subagent lifecycle orchestration with deterministic in-memory state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from agent_runtime.delegation.subagents.constants import Limits, Messages
from agent_runtime.delegation.subagents.contracts import (
    AsyncSubagentLaunch,
    AsyncTaskLifecycleResult,
    AsyncTaskState,
    AsyncTaskStatus,
    SubagentDefinition,
    SubagentError,
    SubagentErrorCode,
    SubagentResult,
    SubagentTask,
)
from agent_runtime.delegation.subagents.definitions import DynamicSubagentCatalog

RawSubagentResult = SubagentResult | Mapping[str, object] | None
RawSubagentLaunch = AsyncSubagentLaunch | Mapping[str, object]


class SubagentRunner(Protocol):
    """Adapter boundary for co-deployed or remote subagent execution."""

    async def start(
        self,
        definition: SubagentDefinition,
        task: SubagentTask,
    ) -> RawSubagentLaunch:
        """Start a subagent task and return runner IDs."""

    async def check(self, state: AsyncTaskState) -> RawSubagentResult:
        """Return a completed result, or None when the task is still running."""

    async def update(self, state: AsyncTaskState, task: SubagentTask) -> None:
        """Send updated instructions to a running subagent task."""

    async def cancel(self, state: AsyncTaskState) -> None:
        """Cancel a running subagent task."""


@dataclass
class InMemoryAsyncTaskStore:
    """Small lifecycle state store used for tests and non-durable local execution."""

    _states: dict[str, AsyncTaskState] = field(default_factory=dict)

    def save(self, state: AsyncTaskState) -> AsyncTaskState:
        self._states[state.task_id] = state
        return state

    def get(self, task_id: str) -> AsyncTaskState | None:
        return self._states.get(task_id)

    def list_states(self) -> tuple[AsyncTaskState, ...]:
        return tuple(sorted(self._states.values(), key=lambda state: state.created_at))

    def count_active(self, subagent_name: str) -> int:
        return sum(
            1
            for state in self._states.values()
            if state.subagent_name == subagent_name
            and state.status in {AsyncTaskStatus.QUEUED, AsyncTaskStatus.RUNNING}
        )


@dataclass
class AsyncSubagentLifecycle:
    """Start, check, update, cancel, and list async subagent task state."""

    catalog: DynamicSubagentCatalog
    runner: SubagentRunner
    store: InMemoryAsyncTaskStore = field(default_factory=InMemoryAsyncTaskStore)
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))

    async def start(
        self,
        *,
        context: object,
        subagent_name: str,
        task: SubagentTask,
    ) -> AsyncTaskLifecycleResult:
        """Start a subagent task or queue it when its concurrency limit is exhausted."""

        resolution = self.catalog.resolve_subagent(subagent_name, context)  # type: ignore[arg-type]
        if isinstance(resolution, SubagentError):
            return AsyncTaskLifecycleResult(error=resolution)

        definition = resolution.definition
        if self.store.count_active(definition.name) >= definition.concurrency_limit:
            state = AsyncTaskStateFactory.create(
                definition=definition,
                status=AsyncTaskStatus.QUEUED,
                now=self.clock(),
                thread_id=uuid4().hex,
                run_id=uuid4().hex,
            )
            self.store.save(state)
            return AsyncTaskLifecycleResult.from_state(state)

        try:
            raw_launch = await self.runner.start(definition, task)
            launch = AsyncTaskLifecycleParser.parse_launch(raw_launch)
        except (TimeoutError, asyncio.TimeoutError):
            return AsyncTaskLifecycleResult.fail(
                SubagentErrorCode.TIMEOUT,
                Messages.Lifecycle.TASK_TIMEOUT,
                retryable=True,
                correlation_id=task.runtime_context_ref.trace_id,
            )
        except (ValidationError, ValueError):
            return AsyncTaskLifecycleResult.fail(
                SubagentErrorCode.VALIDATION_ERROR,
                Messages.Lifecycle.RUNNER_ERROR,
                retryable=False,
                correlation_id=task.runtime_context_ref.trace_id,
            )
        except Exception:
            return AsyncTaskLifecycleResult.fail(
                SubagentErrorCode.RUNNER_ERROR,
                Messages.Lifecycle.RUNNER_ERROR,
                retryable=True,
                correlation_id=task.runtime_context_ref.trace_id,
            )

        state = AsyncTaskStateFactory.create(
            definition=definition,
            status=launch.status,
            now=self.clock(),
            thread_id=launch.thread_id,
            run_id=launch.run_id,
        )
        self.store.save(state)
        return AsyncTaskLifecycleResult.from_state(state)

    async def check(self, task_id: str) -> AsyncTaskLifecycleResult:
        """Check task progress and validate completed subagent output."""

        state = self.store.get(task_id)
        if state is None:
            return AsyncTaskLifecycleErrors.stale_task(task_id)
        if state.status is AsyncTaskStatus.CANCELLED:
            return AsyncTaskLifecycleErrors.cancelled_task(state)
        if state.status in {
            AsyncTaskStatus.SUCCEEDED,
            AsyncTaskStatus.FAILED,
            AsyncTaskStatus.TIMED_OUT,
        }:
            return AsyncTaskLifecycleResult.from_state(state)
        if AsyncTaskLifecyclePolicy.is_timed_out(state, self.clock()):
            timed_out = AsyncTaskStateTransition.with_status(
                state,
                AsyncTaskStatus.TIMED_OUT,
                self.clock(),
            )
            self.store.save(timed_out)
            return AsyncTaskLifecycleResult.from_state(
                timed_out,
                result=SubagentResult.fail(
                    SubagentErrorCode.TIMEOUT,
                    Messages.Lifecycle.TASK_TIMEOUT,
                    retryable=True,
                    task_id=state.task_id,
                ),
            )
        if state.status is AsyncTaskStatus.QUEUED:
            return AsyncTaskLifecycleResult.from_state(state)

        try:
            raw_result = await self.runner.check(state)
        except Exception:
            failed = AsyncTaskStateTransition.with_status(
                state,
                AsyncTaskStatus.FAILED,
                self.clock(),
            )
            self.store.save(failed)
            return AsyncTaskLifecycleResult.from_state(
                failed,
                result=SubagentResult.fail(
                    SubagentErrorCode.RUNNER_ERROR,
                    Messages.Lifecycle.RUNNER_ERROR,
                    retryable=True,
                    task_id=state.task_id,
                ),
            )

        if raw_result is None:
            return AsyncTaskLifecycleResult.from_state(state)

        result = AsyncTaskLifecycleParser.parse_result(raw_result, state)
        status = (
            AsyncTaskStatus.FAILED
            if result.error is not None
            else AsyncTaskStatus.SUCCEEDED
        )
        completed = AsyncTaskStateTransition.with_status(state, status, self.clock())
        self.store.save(completed)
        return AsyncTaskLifecycleResult.from_state(completed, result=result)

    async def update(self, task_id: str, task: SubagentTask) -> AsyncTaskLifecycleResult:
        """Update a running subagent task with a compact replacement handoff."""

        state = self.store.get(task_id)
        if state is None:
            return AsyncTaskLifecycleErrors.stale_task(task_id)
        if state.status is AsyncTaskStatus.CANCELLED:
            return AsyncTaskLifecycleErrors.cancelled_task(state)
        if state.status is not AsyncTaskStatus.RUNNING:
            return AsyncTaskLifecycleErrors.stale_task(task_id)

        try:
            await self.runner.update(state, task)
        except Exception:
            return AsyncTaskLifecycleResult.fail(
                SubagentErrorCode.RUNNER_ERROR,
                Messages.Lifecycle.RUNNER_ERROR,
                retryable=True,
                task_id=state.task_id,
                correlation_id=task.runtime_context_ref.trace_id,
            )

        updated = AsyncTaskStateTransition.with_status(
            state,
            AsyncTaskStatus.RUNNING,
            self.clock(),
        )
        self.store.save(updated)
        return AsyncTaskLifecycleResult.from_state(updated)

    async def cancel(self, task_id: str) -> AsyncTaskLifecycleResult:
        """Cancel a running or queued subagent task."""

        state = self.store.get(task_id)
        if state is None:
            return AsyncTaskLifecycleErrors.stale_task(task_id)
        if state.status is AsyncTaskStatus.CANCELLED:
            return AsyncTaskLifecycleErrors.cancelled_task(state)
        if state.status in {
            AsyncTaskStatus.SUCCEEDED,
            AsyncTaskStatus.FAILED,
            AsyncTaskStatus.TIMED_OUT,
        }:
            return AsyncTaskLifecycleErrors.stale_task(task_id)

        try:
            if state.status is AsyncTaskStatus.RUNNING:
                await self.runner.cancel(state)
        except Exception:
            return AsyncTaskLifecycleResult.fail(
                SubagentErrorCode.RUNNER_ERROR,
                Messages.Lifecycle.RUNNER_ERROR,
                retryable=True,
                task_id=state.task_id,
            )

        cancelled = AsyncTaskStateTransition.with_status(
            state,
            AsyncTaskStatus.CANCELLED,
            self.clock(),
        )
        self.store.save(cancelled)
        return AsyncTaskLifecycleResult.from_state(cancelled)

    def list_tasks(self) -> AsyncTaskLifecycleResult:
        """List async task metadata stored outside message history."""

        return AsyncTaskLifecycleResult.from_tasks(self.store.list_states())


class AsyncTaskStateFactory:
    """Factory methods for lifecycle state with validated timestamps."""

    @classmethod
    def create(
        cls,
        *,
        definition: SubagentDefinition,
        status: AsyncTaskStatus,
        now: datetime,
        thread_id: str,
        run_id: str,
    ) -> AsyncTaskState:
        return AsyncTaskState(
            task_id=uuid4().hex,
            subagent_name=definition.name,
            thread_id=thread_id,
            run_id=run_id,
            status=status,
            created_at=now,
            updated_at=now,
            deadline_at=now + timedelta(seconds=definition.timeout_seconds),
        )


class AsyncTaskStateTransition:
    """Immutable state transitions for async task metadata."""

    @classmethod
    def with_status(
        cls,
        state: AsyncTaskState,
        status: AsyncTaskStatus,
        now: datetime,
    ) -> AsyncTaskState:
        return AsyncTaskState(
            task_id=state.task_id,
            subagent_name=state.subagent_name,
            thread_id=state.thread_id,
            run_id=state.run_id,
            status=status,
            created_at=state.created_at,
            updated_at=now,
            deadline_at=state.deadline_at,
        )


class AsyncTaskLifecycleParser:
    """Parser helpers for untrusted runner output."""

    @classmethod
    def parse_launch(cls, raw_launch: RawSubagentLaunch) -> AsyncSubagentLaunch:
        if isinstance(raw_launch, AsyncSubagentLaunch):
            return raw_launch
        return AsyncSubagentLaunch.model_validate(raw_launch)

    @classmethod
    def parse_result(
        cls,
        raw_result: SubagentResult | Mapping[str, object],
        state: AsyncTaskState,
    ) -> SubagentResult:
        if isinstance(raw_result, SubagentResult):
            return raw_result
        if cls.has_oversized_result(raw_result):
            return SubagentResult.fail(
                SubagentErrorCode.OVERSIZED_RESULT,
                Messages.Lifecycle.OVERSIZED_RESULT,
                retryable=False,
                task_id=state.task_id,
            )
        try:
            return SubagentResult.model_validate(raw_result)
        except ValidationError:
            return SubagentResult.fail(
                SubagentErrorCode.MALFORMED_RESULT,
                Messages.Lifecycle.MALFORMED_RESULT,
                retryable=False,
                task_id=state.task_id,
            )

    @classmethod
    def has_oversized_result(cls, raw_result: Mapping[str, object]) -> bool:
        response = raw_result.get("response")
        execution_summary = raw_result.get("execution_summary")
        plan_summary = raw_result.get("plan_summary")
        return (
            isinstance(response, str)
            and len(response) > Limits.RESULT_RESPONSE_MAX_LENGTH
            or isinstance(execution_summary, str)
            and len(execution_summary) > Limits.SUMMARY_MAX_LENGTH
            or isinstance(plan_summary, str)
            and len(plan_summary) > Limits.SUMMARY_MAX_LENGTH
        )


class AsyncTaskLifecyclePolicy:
    """Lifecycle policy checks independent from runner IO."""

    @classmethod
    def is_timed_out(cls, state: AsyncTaskState, now: datetime) -> bool:
        return state.deadline_at is not None and now >= state.deadline_at


class AsyncTaskLifecycleErrors:
    """Factory methods for deterministic lifecycle errors."""

    @classmethod
    def stale_task(cls, task_id: str) -> AsyncTaskLifecycleResult:
        return AsyncTaskLifecycleResult.fail(
            SubagentErrorCode.STALE_TASK_ID,
            Messages.Lifecycle.STALE_TASK_ID,
            retryable=False,
            task_id=task_id,
        )

    @classmethod
    def cancelled_task(cls, state: AsyncTaskState) -> AsyncTaskLifecycleResult:
        return AsyncTaskLifecycleResult.fail(
            SubagentErrorCode.CANCELLED,
            Messages.Lifecycle.CANCELLED_TASK,
            retryable=False,
            task_id=state.task_id,
        )
