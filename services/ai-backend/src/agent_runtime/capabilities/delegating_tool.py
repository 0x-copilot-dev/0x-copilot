"""Base class for ``BaseTool`` wrappers that delegate to an inner tool.

Several middleware layers (budget, retry, error policy, citations) wrap the
model tool surface. Each one is a ``BaseTool`` that forwards the call to an
``inner`` tool, and each one must hand LangChain's ``RunnableConfig`` across
that boundary. Getting it wrong fails loudly but far from the cause:

    TypeError: StructuredTool._arun() missing 1 required keyword-only
    argument: 'config'

LangChain injects the config into a tool's ``_run`` / ``_arun`` only when that
method declares a parameter annotated *exactly* ``RunnableConfig`` — see
``langchain_core.tools.base._get_runnable_config_param``, which compares the
resolved type hint by identity. A wrapper that delegates with a bare
``*args, **kwargs`` signature therefore declares no such parameter, LangChain
skips the injection, and the wrapper then calls an inner
``StructuredTool._arun`` whose ``config`` is keyword-only and REQUIRED. Every
tool built by ``agent_runtime.execution.factory._structured_tool``
(``call_mcp_tool``, ``ask_a_question``, ``auth_mcp``, ...) dies that way, while
tools that accept ``**kwargs`` keep working — so the breakage is per-tool and
easy to miss.

This class centralises the hand-off so each wrapper only has to accept
``config`` and pass it to :meth:`delegate` / :meth:`adelegate`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, get_type_hints

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

# Stand-in for a wrapper invoked outside LangChain's tool runner (unit tests
# and any direct ``_run`` / ``_arun`` call). Never mutated — it exists so the
# ``config`` parameter can carry the exact ``RunnableConfig`` annotation
# LangChain matches on while still being optional for direct callers.
NO_CONFIG: RunnableConfig = {}


class DelegatingTool(BaseTool):
    """A ``BaseTool`` wrapper that forwards invocations to ``inner``.

    Subclasses declare ``config: RunnableConfig = NO_CONFIG`` on their
    ``_run`` / ``_arun`` (which is what makes LangChain inject the real config)
    and delegate through :meth:`delegate` / :meth:`adelegate`.
    """

    inner: BaseTool

    @staticmethod
    def _config_kwargs(
        method: Callable[..., Any], config: RunnableConfig
    ) -> dict[str, Any]:
        """Return ``{param_name: config}`` when *method* accepts a ``RunnableConfig``.

        Mirrors LangChain's own resolution so we forward the config to inners
        that want it and stay silent for inners that do not — deep-agents
        builtins take ``**kwargs`` and would treat an unexpected ``config`` as a
        tool argument.
        """
        try:
            hints = get_type_hints(method)
        except Exception:  # noqa: BLE001 — unresolvable hints must not break dispatch
            return {}
        for name, annotation in hints.items():
            if annotation is RunnableConfig:
                return {name: config}
        return {}

    def delegate(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Invoke the inner tool synchronously, forwarding ``config`` if it takes one."""
        extra = self._config_kwargs(self.inner._run, config)
        return self.inner._run(*args, **extra, **kwargs)

    async def adelegate(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Invoke the inner tool asynchronously, forwarding ``config`` if it takes one."""
        extra = self._config_kwargs(self.inner._arun, config)
        return await self.inner._arun(*args, **extra, **kwargs)


__all__ = ["NO_CONFIG", "DelegatingTool"]
