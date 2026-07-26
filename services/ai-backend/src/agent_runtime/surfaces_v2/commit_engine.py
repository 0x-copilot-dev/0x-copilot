"""Compatibility request types for staged-write MCP execution.

The old ``CommitEngine`` was a second claim ledger and direct connector
dispatcher.  Approved staged writes now enter
``agent_runtime.effects.dispatch.EffectDispatchCoordinator`` and execute only
through ``runtime_worker.mcp_effect_executor.McpEffectExecutor``.  This module
therefore retains the small server-derived request and typed transport errors
used at that shared executor seam; it intentionally has no executor, claim
ledger, or connector protocol.
"""

from __future__ import annotations

from pydantic import Field, PositiveInt

from agent_runtime.execution.contracts import JsonObject, RuntimeContract


class StageCommitConnectorError(Exception):
    """Safe transport failure observed by the canonical MCP executor."""

    safe_message: str = "The connector could not apply the write."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class StageCommitTimeout(StageCommitConnectorError):
    """The transport outcome is unknown and its durable claim must not replay."""

    safe_message: str = "The connector dispatch timed out; the outcome is unknown."


class StageCommitRequest(RuntimeContract):
    """Exact approved connector arguments, constructed only from server state."""

    org_id: str
    user_id: str
    run_id: str
    conversation_id: str
    stage_id: str
    rev: PositiveInt
    decision_seq: int
    target_connector: str
    target_op: str
    body: str
    title: str = ""
    target_metadata: JsonObject = Field(default_factory=dict)
    row_key: str | None = None
    row_args: JsonObject | None = None

    def commit_key(self) -> str:
        """Stable per-approval idempotency identity for the shared claim store."""

        base = f"{self.stage_id}:{self.rev}:{self.decision_seq}"
        return f"{base}:{self.row_key}" if self.row_key is not None else base

    def tool_arguments(self) -> JsonObject:
        """Return a copy of the exact approved MCP argument object."""

        if self.row_args is not None:
            return dict(self.row_args)
        arguments: JsonObject = {"body": self.body}
        if self.title:
            arguments["title"] = self.title
        if self.target_metadata:
            arguments["target_metadata"] = dict(self.target_metadata)
        return arguments


__all__ = (
    "StageCommitConnectorError",
    "StageCommitRequest",
    "StageCommitTimeout",
)
