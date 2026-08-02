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
from typing import Any, Final

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
from agent_runtime.hyperparameters.contracts import RetryHyperparameters
from pydantic import ConfigDict


_LOGGER = logging.getLogger("agent_runtime.capabilities.retrying_tool")


_NEVER_RETRY: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


#: The ``retry`` section of ``hyperparameters.json``, whose declared defaults
#: are the three knobs on :class:`RetryingTool`. Before this binding the same
#: ``3`` was written twice — on the class and again at the
#: ``RetryingTool.wrapping(..., max_attempts=3)`` call in
#: ``runtime_worker/dependencies.py`` — with nothing making the two agree.
#: Module level rather than a class attribute because ``RetryingTool`` is a
#: Pydantic model, where an underscore-prefixed class attribute is claimed as a
#: private attribute instead of staying an ordinary constant.
_RETRY_POLICY: Final = RetryHyperparameters()


class RetryingTool(DelegatingTool):
    """LangChain ``BaseTool`` wrapper that retries transient inner failures.

    The inner tool's surface is propagated unchanged so the model sees an
    identical tool — only the invocation path differs. After ``max_attempts``
    attempts, the last exception is re-raised so the runtime's normal
    ``tool_exception`` path still applies for genuinely-broken tools.

    "Surface" means the whole of it, not just the model-visible half. Build
    every wrapper through :meth:`wrapping` rather than hand-listing fields at
    the call site: the built-in ``web_search`` was constructed by hand with
    ``name`` / ``description`` / ``args_schema`` only, so it silently dropped
    the inner's ``response_format="content_and_artifact"``. LangChain reads
    ``response_format`` off the tool it dispatches, so the inner kept returning
    ``(content, artifact)`` while the wrapper promised a bare ``content`` — the
    pair was stringified into ``ToolMessage.content`` and ``artifact`` was
    ``None`` on every call.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    max_attempts: int = _RETRY_POLICY.max_attempts
    initial_backoff_seconds: float = _RETRY_POLICY.initial_backoff_seconds
    max_backoff_seconds: float = _RETRY_POLICY.max_backoff_seconds
    # Retried by default. Callers can narrow (e.g. ``(httpx.ConnectError,)``)
    # to avoid masking permanent failures. ``_NEVER_RETRY`` always wins.
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,)

    @classmethod
    def wrapping(
        cls,
        inner: BaseTool,
        *,
        max_attempts: int | None = None,
        initial_backoff_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
        retry_exceptions: tuple[type[BaseException], ...] | None = None,
    ) -> RetryingTool:
        """Return a retry wrapper presenting ``inner``'s full tool surface.

        The propagated field set is
        :class:`~agent_runtime.capabilities.mcp.middleware.compose.ToolSchemaIdentity`'s
        — the single definition of what a wrapper must reproduce from the tool
        it wraps, covering both the model surface and the dispatch surface
        (``response_format`` / ``return_direct`` / ``metadata`` / ``tags``).
        Reusing it is the point: a second, parallel propagation list is exactly
        how ``response_format`` went missing here in the first place.

        A ``None`` retry knob keeps this class's own field default, so the
        defaults live in one place instead of being restated in this signature.
        """

        # Imported inside the method on purpose: ``exec_policy_tool`` (reached
        # from the ``capabilities.mcp`` package __init__) imports THIS module,
        # so a module-level import of ``compose`` would close an import cycle
        # and break every path that touches ``RetryingTool`` first.
        from agent_runtime.capabilities.mcp.middleware.compose import (  # noqa: PLC0415
            ToolSchemaIdentity,
        )

        overrides: dict[str, Any] = {
            "max_attempts": max_attempts,
            "initial_backoff_seconds": initial_backoff_seconds,
            "max_backoff_seconds": max_backoff_seconds,
            "retry_exceptions": retry_exceptions,
        }
        return cls(
            **ToolSchemaIdentity.fields_of(inner),
            inner=inner,
            **{name: value for name, value in overrides.items() if value is not None},
        )

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
