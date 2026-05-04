"""Unit tests for ``with_optimistic_retry`` (C3) and the runtime pool builder (C4)."""

from __future__ import annotations

import pytest

from agent_runtime.persistence.errors import (
    ConcurrentMemoryItemUpdateError,
    ConcurrentRunUpdateError,
    PersistenceError,
)
from agent_runtime.persistence.optimistic import with_optimistic_retry
from runtime_adapters.postgres.runtime_api_store import _PoolEnv


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestWithOptimisticRetry:
    async def test_returns_value_on_first_attempt(self) -> None:
        async def operation() -> int:
            return 42

        result = await with_optimistic_retry(operation)
        assert result == 42

    async def test_retries_on_concurrent_update_error_then_succeeds(self) -> None:
        attempts = 0

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConcurrentRunUpdateError(run_id="run_1", expected_version=3)
            return "ok"

        result = await with_optimistic_retry(
            operation, max_attempts=3, base_delay_seconds=0.001
        )
        assert result == "ok"
        assert attempts == 2

    async def test_re_raises_after_attempts_exhausted(self) -> None:
        async def operation() -> str:
            raise ConcurrentRunUpdateError(run_id="run_1", expected_version=1)

        with pytest.raises(ConcurrentRunUpdateError):
            await with_optimistic_retry(
                operation, max_attempts=2, base_delay_seconds=0.001
            )

    async def test_non_retryable_error_propagates_immediately(self) -> None:
        attempts = 0

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            await with_optimistic_retry(
                operation, max_attempts=5, base_delay_seconds=0.001
            )
        # Should not have retried since ValueError is outside the retry tuple.
        assert attempts == 1

    async def test_memory_item_update_error_is_retryable_by_default(self) -> None:
        attempts = 0

        async def operation() -> int:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConcurrentMemoryItemUpdateError(
                    item_id="mem_1", expected_version=2
                )
            return 7

        result = await with_optimistic_retry(
            operation, max_attempts=3, base_delay_seconds=0.001
        )
        assert result == 7
        assert attempts == 2

    async def test_invalid_max_attempts_rejected(self) -> None:
        async def operation() -> int:
            return 1

        with pytest.raises(ValueError):
            await with_optimistic_retry(operation, max_attempts=0)

    async def test_custom_retryable_tuple_respected(self) -> None:
        class _CustomError(PersistenceError):
            pass

        attempts = 0

        async def operation() -> str:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise _CustomError("custom")
            return "done"

        result = await with_optimistic_retry(
            operation,
            max_attempts=3,
            base_delay_seconds=0.001,
            retryable=(_CustomError,),
        )
        assert result == "done"
        assert attempts == 2


class TestRuntimePoolEnv:
    def test_default_options_include_application_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in (
            _PoolEnv.STATEMENT_TIMEOUT_MS,
            _PoolEnv.LOCK_TIMEOUT_MS,
            _PoolEnv.IDLE_IN_TXN_TIMEOUT_MS,
        ):
            monkeypatch.delenv(key, raising=False)
        kwargs = _PoolEnv.build_pool_kwargs(role="api")
        assert kwargs["row_factory"] is not None
        options = kwargs["options"]
        assert "application_name=ai-backend:api" in options
        assert "statement_timeout=10000" in options
        assert "lock_timeout=3000" in options
        assert "idle_in_transaction_session_timeout=30000" in options

    def test_role_appears_in_application_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in (
            _PoolEnv.STATEMENT_TIMEOUT_MS,
            _PoolEnv.LOCK_TIMEOUT_MS,
            _PoolEnv.IDLE_IN_TXN_TIMEOUT_MS,
        ):
            monkeypatch.delenv(key, raising=False)
        kwargs = _PoolEnv.build_pool_kwargs(role="worker")
        assert "application_name=ai-backend:worker" in kwargs["options"]

    def test_env_var_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_PoolEnv.STATEMENT_TIMEOUT_MS, "8000")
        monkeypatch.setenv(_PoolEnv.LOCK_TIMEOUT_MS, "2000")
        monkeypatch.setenv(_PoolEnv.IDLE_IN_TXN_TIMEOUT_MS, "45000")
        kwargs = _PoolEnv.build_pool_kwargs(role="api")
        options = kwargs["options"]
        assert "statement_timeout=8000" in options
        assert "lock_timeout=2000" in options
        assert "idle_in_transaction_session_timeout=45000" in options

    def test_env_int_falls_back_on_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_PoolEnv.POOL_MIN_SIZE, "garbage")
        assert _PoolEnv.env_int(_PoolEnv.POOL_MIN_SIZE, 7) == 7

    def test_env_float_falls_back_on_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_PoolEnv.POOL_ACQUIRE_TIMEOUT_SECONDS, "nope")
        assert _PoolEnv.env_float(_PoolEnv.POOL_ACQUIRE_TIMEOUT_SECONDS, 1.5) == 1.5
