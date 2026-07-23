"""Production ``StageCommitConnector`` — the CallMcpTool seam outside the agent loop.

The :class:`CommitEngine` (``commit_engine.py``) is pure and fully fakeable; this
is the one place that touches a real external system in the v2 write path. It
replicates ``CallMcpTool.ainvoke``'s MCP dispatch seam
(``registry.resolve_server`` → ``resolution.provider.create_client`` →
``client.call_tool`` under ``asyncio.wait_for``) with the same typed-exception
mapping, but driven by an approved :class:`StageCommitRequest` from the worker
rather than a model tool call.

Fail-closed at every boundary: an unresolvable / disabled / unconfigured server,
a timeout, an auth/connection/client error, or a protocol-level ``isError`` all
raise a typed engine exception, which the engine maps to a ``FAILED`` /
``INDETERMINATE`` outcome — the handler then ledgers ``write.applied{failed}`` and
NOTHING sends twice. Connector output is untrusted: only a best-effort
``external_ref`` string is projected onto the result; the raw payload is never
echoed into a ledger event.

``read_remote_state`` returns ``None`` in D2 (a draft-send has no remote
precondition source — the handler's local draft-status precondition does the
work); the seam exists for D3 field-writes that read a record fingerprint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from agent_runtime.capabilities.mcp.cards import McpLoadError
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpTimeoutError,
)
from agent_runtime.capabilities.mcp.outcomes import McpToolCallOutcome
from agent_runtime.capabilities.surfaces.commit import (
    ConnectorCommitResult,
    RemoteState,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
    StageCommitTimeout,
)

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]

_UNRESOLVED_SERVER = "The target connector could not be resolved."
_NO_REGISTRY = "No MCP registry is configured for this deployment."
_PROTOCOL_ERROR = "The connector reported an error applying the write."
_DISPATCH_FAILED = "The connector dispatch failed."

# Keys we may lift from an untrusted connector result as the external receipt id.
_EXTERNAL_REF_KEYS = ("external_ref", "id", "message_id", "ref")


class McpStageCommitConnector:
    """Dispatch an approved revision through the real MCP client (PRD-D2).

    Built with the run's ``runtime_context`` (real roles / scopes so the registry
    permission-filters exactly as the run would) plus the worker's
    ``dependencies_factory`` and a dispatch timeout. Stateless per call — a fresh
    registry is composed each ``execute`` so a mid-run auth change is honored.
    """

    def __init__(
        self,
        *,
        runtime_context: AgentRuntimeContext,
        dependencies_factory: RuntimeDependenciesFactory,
        timeout_seconds: float,
    ) -> None:
        self._runtime_context = runtime_context
        self._dependencies_factory = dependencies_factory
        self._timeout_seconds = timeout_seconds

    async def read_remote_state(
        self, request: StageCommitRequest
    ) -> RemoteState | None:
        """No remote precondition source for a draft-send (D2). See module docstring."""

        return None

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        """Dispatch exactly the approved revision; raise typed errors on failure."""

        registry = self._dependencies_factory(self._runtime_context).mcp_registry
        resolve = getattr(registry, "resolve_server", None)
        if resolve is None:
            # No providers configured (``EmptyMcpRegistry``) — fail closed.
            raise StageCommitConnectorError(_NO_REGISTRY)

        resolution = await resolve(request.target_connector)
        if isinstance(resolution, McpLoadError):
            raise StageCommitConnectorError(
                resolution.safe_message or _UNRESOLVED_SERVER
            )

        try:
            client = resolution.provider.create_client(resolution.card)
            output = await asyncio.wait_for(
                client.call_tool(
                    tool_name=request.target_op,
                    arguments=request.tool_arguments(),
                ),
                timeout=self._timeout_seconds,
            )
        except (McpTimeoutError, asyncio.TimeoutError, TimeoutError) as exc:
            # The send may have left the building — INDETERMINATE, never resend.
            raise StageCommitTimeout() from exc
        except (
            McpAuthError,
            PermissionError,
            McpConnectionError,
            ConnectionError,
            McpClientError,
        ) as exc:
            raise StageCommitConnectorError(_DISPATCH_FAILED) from exc
        except Exception as exc:  # noqa: BLE001 — any dispatch fault fails closed.
            raise StageCommitConnectorError(_DISPATCH_FAILED) from exc

        # A successful transport response can still carry ``isError: true`` — that
        # is a failure, not a send (MCP spec). Treat it as a connector error.
        if McpToolCallOutcome.is_protocol_error(output):
            raise StageCommitConnectorError(_PROTOCOL_ERROR)

        return ConnectorCommitResult(
            status="sent",
            external_ref=self._external_ref(output),
            # The raw connector output is UNTRUSTED — never echoed into the event.
            detail={},
        )

    @staticmethod
    def _external_ref(output: object) -> str | None:
        """Best-effort receipt id from an untrusted connector result (or None)."""

        candidate: Any = output
        if isinstance(candidate, dict):
            for key in _EXTERNAL_REF_KEYS:
                value = candidate.get(key)
                if isinstance(value, str) and value:
                    return value
        return None


__all__ = ["McpStageCommitConnector", "RuntimeDependenciesFactory"]
