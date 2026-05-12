"""Generic LangChain ``BaseTool`` wrapper that retries transient failures.

Some external tools (notably ``DuckDuckGoSearchResults`` backed by ``ddgs``)
raise opaque exceptions on temporary network or rate-limit failures. The
runtime treats a tool exception as fatal — it surfaces a ``tool_exception``
result and ends the run — so a single transient hiccup ends an otherwise
healthy subagent task. This wrapper absorbs the transient case by retrying
the inner tool's invocation with exponential backoff + jitter before letting
the exception propagate.

The wrapper is generic: any LangChain ``BaseTool`` can be wrapped, and the
caller picks how many attempts and which exception types qualify as
retryable. Cancellation, keyboard interrupts, and system exits are never
retried regardless of configuration.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict


_LOGGER = logging.getLogger("agent_runtime.capabilities.retrying_tool")


class _NeverRetry(BaseException):
    """Sentinel used to express ``raise`` types that bypass the retry loop."""


_NEVER_RETRY: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


class RetryingTool(BaseTool):
    """LangChain ``BaseTool`` wrapper that retries transient inner failures.

    The inner tool's ``name`` / ``description`` / ``args_schema`` are
    propagated unchanged so the model sees an identical surface — only the
    invocation path differs. After ``max_attempts`` attempts, the last
    exception is re-raised so the runtime's normal ``tool_exception`` path
    still applies for genuinely-broken tools.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0
    # Retried by default. Callers can narrow (e.g. ``(httpx.ConnectError,)``)
    # to avoid masking permanent failures. ``_NEVER_RETRY`` always wins.
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Sync retry loop; re-raises the last exception after ``max_attempts``."""
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.inner._run(*args, **kwargs)
            except _NEVER_RETRY:
                raise
            except BaseException as exc:  # noqa: BLE001 — width is intentional
                if not self._should_retry(exc):
                    raise
                last_exc = exc
                if attempt == self.max_attempts:
                    break
                self._log_retry(attempt=attempt, exc=exc)
                time.sleep(self._backoff_seconds(attempt))
        assert last_exc is not None  # narrow for type checker
        raise last_exc

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """Async retry loop; re-raises the last exception after ``max_attempts``."""
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self.inner._arun(*args, **kwargs)
            except _NEVER_RETRY:
                raise
            except BaseException as exc:  # noqa: BLE001 — width is intentional
                if not self._should_retry(exc):
                    raise
                last_exc = exc
                if attempt == self.max_attempts:
                    break
                self._log_retry(attempt=attempt, exc=exc)
                await asyncio.sleep(self._backoff_seconds(attempt))
        assert last_exc is not None
        raise last_exc

    def _should_retry(self, exc: BaseException) -> bool:
        """Return ``True`` when ``exc`` qualifies for a retry attempt."""
        if isinstance(exc, _NEVER_RETRY):
            return False
        return isinstance(exc, self.retry_exceptions)

    def _backoff_seconds(self, attempt: int) -> float:
        """Compute an exponential backoff with full jitter, capped at ``max_backoff_seconds``."""
        # Full jitter distributes concurrent retries across the window instead
        # of synchronising all callers at the same peak delay.
        target = min(
            self.initial_backoff_seconds * (2 ** (attempt - 1)),
            self.max_backoff_seconds,
        )
        return random.uniform(0.0, target)

    def _log_retry(self, *, attempt: int, exc: BaseException) -> None:
        """Log a structured ``tool_retry`` event before sleeping."""
        _LOGGER.info(
            "tool_retry",
            extra={
                "metadata": {
                    "tool_name": self.name,
                    "attempt": attempt,
                    "max_attempts": self.max_attempts,
                    "error_class": exc.__class__.__name__,
                    "error_message": str(exc)[:200],
                }
            },
        )


__all__ = ("RetryingTool",)
