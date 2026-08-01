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

The retry loop itself is driven by :mod:`tenacity` (``Retrying`` /
``AsyncRetrying``). ``stop_after_attempt`` caps the attempt count,
``wait_random_exponential`` supplies the exact "full jitter" backoff schedule
(``random.uniform(0, min(initial * 2 ** (attempt - 1), max))``), ``reraise``
lets the final underlying exception propagate unchanged so the runtime's
normal ``tool_exception`` path still applies, and a ``before_sleep`` callback
emits the structured ``tool_retry`` log. The retry *predicate* remains
:meth:`RetryingTool._should_retry`, which keeps the never-retry sentinel
winning over any caller-configured ``retry_exceptions``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from pydantic import ConfigDict


_LOGGER = logging.getLogger("agent_runtime.capabilities.retrying_tool")


_NEVER_RETRY: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


class RetryingTool(DelegatingTool):
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

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync retry loop; re-raises the last exception after ``max_attempts``."""
        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=self._wait(),
            retry=retry_if_exception(self._should_retry),
            before_sleep=self._before_sleep,
            reraise=True,
        )
        return retrying(self.delegate, *args, config=config, **kwargs)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async retry loop; re-raises the last exception after ``max_attempts``."""
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=self._wait(),
            retry=retry_if_exception(self._should_retry),
            before_sleep=self._before_sleep,
            reraise=True,
        )
        return await retrying(self.adelegate, *args, config=config, **kwargs)

    def _wait(self) -> wait_random_exponential:
        """Full-jitter exponential backoff capped at ``max_backoff_seconds``.

        ``wait_random_exponential`` yields
        ``random.uniform(0, min(initial * 2 ** (attempt - 1), max))`` — the
        "Full Jitter" schedule that distributes concurrent retries across the
        window instead of synchronising all callers at the same peak delay.
        """
        return wait_random_exponential(
            multiplier=self.initial_backoff_seconds,
            max=self.max_backoff_seconds,
        )

    def _should_retry(self, exc: BaseException) -> bool:
        """Return ``True`` when ``exc`` qualifies for a retry attempt."""
        if isinstance(exc, _NEVER_RETRY):
            return False
        return isinstance(exc, self.retry_exceptions)

    def _before_sleep(self, retry_state: RetryCallState) -> None:
        """Log a structured ``tool_retry`` event before tenacity sleeps."""
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        _LOGGER.info(
            "tool_retry",
            extra={
                "metadata": {
                    "tool_name": self.name,
                    "attempt": retry_state.attempt_number,
                    "max_attempts": self.max_attempts,
                    "error_class": exc.__class__.__name__,
                    "error_message": str(exc)[:200],
                }
            },
        )


__all__ = ("RetryingTool",)
