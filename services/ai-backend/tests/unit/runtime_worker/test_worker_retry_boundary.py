"""Generic worker retries stop at the run-handler dispatch boundary."""

from __future__ import annotations

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.records import (
    OutboxStatus,
    RuntimeWorkerResult,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeRunCommand
from runtime_worker.loop import _PreparedWorkerDispatch, RuntimeWorker


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_RETRIES": "2",
            "SURFACES_V2": "false",
        }
    )


def _command(run_id: str) -> RuntimeRunCommand:
    return RuntimeRunCommand(
        run_id=run_id,
        conversation_id="conversation_retry_boundary",
        org_id="org_retry_boundary",
        user_id="user_retry_boundary",
        trace_id=f"trace_{run_id}",
        runtime_context=AgentRuntimeContext(
            run_id=run_id,
            trace_id=f"trace_{run_id}",
            user_id="user_retry_boundary",
            org_id="org_retry_boundary",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


class _RecordingStore(InMemoryRuntimeApiStore):
    def __init__(self) -> None:
        super().__init__()
        self.retry_results: list[RuntimeWorkerResult] = []
        self.dead_letter_results: list[RuntimeWorkerResult] = []

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        self.retry_results.append(result)
        await super().mark_retry(result=result)

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        self.dead_letter_results.append(result)
        await super().mark_dead_letter(result=result)


class _RetryableFailingRunHandler:
    calls = 0

    async def handle(self, _command: RuntimeRunCommand) -> None:
        self.calls += 1
        raise AgentRuntimeError(
            RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            "Provider outcome is ambiguous after dispatch.",
            retryable=True,
        )


async def test_post_dispatch_run_failure_is_not_replayed() -> None:
    store = _RecordingStore()
    await store.enqueue_run(_command("run_post_dispatch"))
    handler = _RetryableFailingRunHandler()
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=_settings(),
        retry_delay_seconds=0,
        run_handler=handler,
    )

    assert await worker.run_once() is True
    assert await worker.run_once() is False

    command_id = store._queue_order[0]
    assert handler.calls == 1
    assert store._queue_statuses[command_id] is OutboxStatus.DEAD_LETTER
    assert store.retry_results == []
    assert len(store.dead_letter_results) == 1
    assert store.dead_letter_results[0].safe_error is not None
    assert store.dead_letter_results[0].safe_error.retryable is False


class _SuccessfulRunHandler:
    calls = 0

    async def handle(self, _command: RuntimeRunCommand) -> None:
        self.calls += 1


class _FailsOnceBeforeHandlerWorker(RuntimeWorker):
    pre_dispatch_attempts = 0

    async def _invoke_prepared_dispatch(
        self, prepared: _PreparedWorkerDispatch
    ) -> None:
        self.pre_dispatch_attempts += 1
        if self.pre_dispatch_attempts == 1:
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Trace setup was temporarily unavailable.",
                retryable=True,
            )
        await super()._invoke_prepared_dispatch(prepared)


async def test_pre_dispatch_transient_failure_can_retry_without_replaying_handler() -> (
    None
):
    store = _RecordingStore()
    await store.enqueue_run(_command("run_pre_dispatch"))
    handler = _SuccessfulRunHandler()
    worker = _FailsOnceBeforeHandlerWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=_settings(),
        retry_delay_seconds=0,
        run_handler=handler,
    )

    assert await worker.run_once() is True
    assert handler.calls == 0
    assert len(store.retry_results) == 1
    assert store.retry_results[0].safe_error is not None
    assert store.retry_results[0].safe_error.retryable is True

    assert await worker.run_once() is True
    assert await worker.run_once() is False

    command_id = store._queue_order[0]
    assert handler.calls == 1
    assert store._queue_statuses[command_id] is OutboxStatus.COMPLETED
    assert store.dead_letter_results == []
