"""Unit tests for ``with_optimistic_retry`` (C3)."""

from __future__ import annotations

import pytest

from agent_runtime.persistence.errors import (
    ConcurrentMemoryItemUpdateError,
    ConcurrentRunUpdateError,
    PersistenceError,
)
from agent_runtime.persistence.optimistic import with_optimistic_retry


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
