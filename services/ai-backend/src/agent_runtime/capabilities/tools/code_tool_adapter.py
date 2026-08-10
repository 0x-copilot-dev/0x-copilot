"""Adapter from a Tool.kind=\"code\" catalog row to a runtime tool callable.

Wraps a ``CodeSandboxPort`` and exposes the standard runtime tool surface
so the existing tool-call envelope path (the same one MCP tools and
built-ins flow through) carries the invocation. No new emit path, no
new audit table, no new Purpose attribution — the executor itself does
not call an LLM.

The adapter resolves a tool's code body via a small ``CodeFetcher``
``Protocol``. The fetcher is injected (in tests we use a fake; in
production it will hit the backend's ``/internal/v1/tools/{id}/code``
internal endpoint once P10-A2 ships that route). Keeping the fetcher
behind a protocol lets us land this without coupling to a route that
doesn't exist yet.

The invocation writer is also injected via the
``ToolInvocationRowWriter`` ``Protocol`` so tests can assert "exactly one
``runtime_tool_invocations`` row per call" without touching the real
event store.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent_runtime.capabilities.tools.code_sandbox import (
    CodeSandboxPort,
    SandboxResult,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract


# ---------------------------------------------------------------------------
# Wire shape coming in (subset of Tool.kind="code" — tools-prd §3.1)
# ---------------------------------------------------------------------------


class CodeToolBundle(RuntimeContract):
    """Resolved code-routine bundle ready to execute.

    Fields mirror the ``code_ref`` shape from ``packages/api-types/src/routines.ts``
    + the ``Tool`` row in ``packages/api-types/src/index.ts``. A
    fetcher returns this after resolving ``repo_ref`` to a code blob.
    """

    tool_id: str
    name: str
    code: str
    entry: str
    timeout_s: float = 30.0


# ---------------------------------------------------------------------------
# Ports (substitution boundaries)
# ---------------------------------------------------------------------------


class CodeFetcher(Protocol):
    """Resolve a tool_id to a concrete ``CodeToolBundle``.

    Production: hits the backend's internal tool-bundle route once
    P10-A2 lands. Tests: fake.
    """

    async def fetch(self, *, tool_id: str) -> CodeToolBundle | None: ...


class ToolInvocationRowWriter(Protocol):
    """Persist one ``runtime_tool_invocations`` row per call.

    The real implementation lives in the runtime_worker tool observation
    pipeline. We do NOT recreate that pipeline here — the adapter just
    calls into the writer the worker already uses. In tests we substitute
    a recorder so we can assert "exactly one row per call".
    """

    async def record(
        self,
        *,
        tool_id: str,
        tool_name: str,
        run_id: str,
        org_id: str,
        user_id: str,
        call_id: str,
        args: Mapping[str, Any],
        result: SandboxResult,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Envelope returned to the runtime (one shape: matches the existing tool-call
# envelope contract enough that the LangGraph layer can stream it via the
# normal path).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeToolInvocationEnvelope:
    """Adapter-side return shape — caller serializes via the standard path."""

    tool_id: str
    tool_name: str
    call_id: str
    status: str  # "ok" | "error" — matches ToolInvocation.status
    error_kind: str | None
    result: dict[str, Any] | None
    error_message: str | None
    latency_ms: int

    def to_tool_result_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable payload for the LLM-facing tool result.

        The runtime tool-call envelope's ``tool_result.payload`` is a
        ``JsonObject`` keyed on what the model needs to see. For an "ok"
        call we surface the dict result; for an error we surface a typed
        ``ok=False`` + ``error_kind`` + ``message`` shape so the model
        can decide whether to retry / give up.
        """
        if self.status == "ok" and self.result is not None:
            return {"ok": True, "result": dict(self.result)}
        return {
            "ok": False,
            "error_kind": self.error_kind,
            "message": self.error_message,
        }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeToolAdapter:
    """Wrap ``CodeSandboxPort`` into a callable tool keyed on ``tool_id``.

    Constructed once per runtime context. ``ainvoke(tool_id, args)`` runs
    a single code-routine call: fetch → sandbox → envelope → record.
    """

    runtime_context: AgentRuntimeContext
    sandbox: CodeSandboxPort
    fetcher: CodeFetcher
    invocation_writer: ToolInvocationRowWriter
    call_id_factory: Callable[[], str] = lambda: f"codecall_{secrets.token_hex(8)}"

    async def ainvoke(
        self,
        *,
        tool_id: str,
        args: Mapping[str, Any] | None = None,
    ) -> CodeToolInvocationEnvelope:
        """Execute the code-routine identified by ``tool_id`` with ``args``."""
        call_id = self.call_id_factory()
        bundle = await self._resolve_bundle(tool_id=tool_id)
        if bundle is None:
            envelope = self._not_found_envelope(tool_id=tool_id, call_id=call_id)
            await self._record(envelope=envelope, args=args or {})
            return envelope

        result = await self.sandbox.execute(
            code=bundle.code,
            entry=bundle.entry,
            args=args or {},
            timeout_s=bundle.timeout_s,
        )
        envelope = CodeToolInvocationEnvelope(
            tool_id=bundle.tool_id,
            tool_name=bundle.name,
            call_id=call_id,
            status=result.status,
            error_kind=result.error_kind,
            result=result.result,
            error_message=result.error_message,
            latency_ms=result.latency_ms,
        )
        await self._record(envelope=envelope, args=args or {})
        return envelope

    async def __call__(
        self,
        tool_id: str,
        args: Mapping[str, Any] | None = None,
    ) -> CodeToolInvocationEnvelope:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(tool_id=tool_id, args=args)

    async def _resolve_bundle(self, *, tool_id: str) -> CodeToolBundle | None:
        """Fetch the code bundle for ``tool_id``. ``None`` => unknown tool."""
        result = self.fetcher.fetch(tool_id=tool_id)
        if isinstance(result, Awaitable):
            return await result
        return result

    @staticmethod
    def _not_found_envelope(
        *, tool_id: str, call_id: str
    ) -> CodeToolInvocationEnvelope:
        """Build the envelope returned when the fetcher resolves to ``None``."""
        return CodeToolInvocationEnvelope(
            tool_id=tool_id,
            tool_name="<unknown>",
            call_id=call_id,
            status="error",
            error_kind="schema_invalid",
            result=None,
            error_message=f"code-routine tool '{tool_id}' not found",
            latency_ms=0,
        )

    async def _record(
        self,
        *,
        envelope: CodeToolInvocationEnvelope,
        args: Mapping[str, Any],
    ) -> None:
        """Write exactly one ``runtime_tool_invocations`` row for this call."""
        sandbox_result = SandboxResult(
            status="ok" if envelope.status == "ok" else "error",
            result=envelope.result,
            error_kind=envelope.error_kind,  # type: ignore[arg-type]
            error_message=envelope.error_message,
            latency_ms=envelope.latency_ms,
        )
        await self.invocation_writer.record(
            tool_id=envelope.tool_id,
            tool_name=envelope.tool_name,
            run_id=self.runtime_context.run_id,
            org_id=self.runtime_context.org_id,
            user_id=self.runtime_context.user_id,
            call_id=envelope.call_id,
            args=dict(args),
            result=sandbox_result,
        )
