"""Unit tests for the code-routine tool adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.capabilities.tools.code_sandbox import (
    InProcessCodeSandbox,
    SandboxResult,
)
from agent_runtime.capabilities.tools.code_tool_adapter import (
    CodeToolAdapter,
    CodeToolBundle,
    CodeToolInvocationEnvelope,
)
from agent_runtime.execution.contracts import AgentRuntimeContext


def _ctx(run_id: str = "run_test") -> AgentRuntimeContext:
    """Build a minimal runtime context for adapter tests."""
    from agent_runtime.execution.contracts import ModelConfig

    return AgentRuntimeContext(
        user_id="user_alpha",
        org_id="org_alpha",
        roles=frozenset({"engineer"}),
        permission_scopes=frozenset(),
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5-mini",
            max_input_tokens=128_000,
            timeout_seconds=30.0,
            temperature=0.0,
        ),
        run_id=run_id,
    )


@dataclass
class FakeFetcher:
    """In-memory tool_id -> bundle map. Substitutes for the P10-A2 internal route."""

    bundles: dict[str, CodeToolBundle] = field(default_factory=dict)

    async def fetch(self, *, tool_id: str) -> CodeToolBundle | None:
        return self.bundles.get(tool_id)


@dataclass
class RecordingWriter:
    """Records every ``runtime_tool_invocations`` write the adapter performs."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    async def record(
        self,
        *,
        tool_id: str,
        tool_name: str,
        run_id: str,
        org_id: str,
        user_id: str,
        call_id: str,
        args: Any,
        result: SandboxResult,
    ) -> None:
        self.rows.append(
            {
                "tool_id": tool_id,
                "tool_name": tool_name,
                "run_id": run_id,
                "org_id": org_id,
                "user_id": user_id,
                "call_id": call_id,
                "args": dict(args),
                "status": result.status,
                "error_kind": result.error_kind,
                "latency_ms": result.latency_ms,
            }
        )


def _echo_bundle(tool_id: str = "tool_echo") -> CodeToolBundle:
    """Build an ``echo args`` bundle used across happy-path tests."""
    return CodeToolBundle(
        tool_id=tool_id,
        name="echo",
        code="def run(args):\n    return {'echoed': args}\n",
        entry="run",
        timeout_s=1.0,
    )


class TestForwardsArgs:
    """The adapter must surface the args dict into the sandbox unchanged."""

    def test_args_roundtrip(self) -> None:
        bundle = _echo_bundle()
        fetcher = FakeFetcher(bundles={bundle.tool_id: bundle})
        writer = RecordingWriter()
        adapter = CodeToolAdapter(
            runtime_context=_ctx(),
            sandbox=InProcessCodeSandbox(max_timeout_s=2.0),
            fetcher=fetcher,
            invocation_writer=writer,
        )
        envelope = asyncio.run(
            adapter.ainvoke(tool_id=bundle.tool_id, args={"n": 42, "tag": "x"})
        )
        assert isinstance(envelope, CodeToolInvocationEnvelope)
        assert envelope.status == "ok"
        assert envelope.result == {"echoed": {"n": 42, "tag": "x"}}
        # Tool-result payload shape: ok=True branch.
        payload = envelope.to_tool_result_payload()
        assert payload["ok"] is True
        assert payload["result"] == {"echoed": {"n": 42, "tag": "x"}}


class TestSurfacesSandboxResult:
    """SandboxResult error kinds must surface into the envelope."""

    def test_timeout_surfaces(self) -> None:
        bundle = CodeToolBundle(
            tool_id="tool_slow",
            name="slow",
            code=(
                "async def run(args):\n"
                "    await args['sleeper']()\n"
                "    return {'never': True}\n"
            ),
            entry="run",
            timeout_s=0.05,
        )
        fetcher = FakeFetcher(bundles={bundle.tool_id: bundle})
        writer = RecordingWriter()
        adapter = CodeToolAdapter(
            runtime_context=_ctx(),
            sandbox=InProcessCodeSandbox(max_timeout_s=0.05),
            fetcher=fetcher,
            invocation_writer=writer,
        )

        async def sleeper() -> None:
            await asyncio.sleep(1.0)

        envelope = asyncio.run(
            adapter.ainvoke(tool_id=bundle.tool_id, args={"sleeper": sleeper})
        )
        assert envelope.status == "error"
        assert envelope.error_kind == "timeout"
        payload = envelope.to_tool_result_payload()
        assert payload["ok"] is False
        assert payload["error_kind"] == "timeout"

    def test_unknown_tool_surfaces(self) -> None:
        fetcher = FakeFetcher(bundles={})
        writer = RecordingWriter()
        adapter = CodeToolAdapter(
            runtime_context=_ctx(),
            sandbox=InProcessCodeSandbox(),
            fetcher=fetcher,
            invocation_writer=writer,
        )
        envelope = asyncio.run(adapter.ainvoke(tool_id="tool_missing", args={}))
        assert envelope.status == "error"
        assert envelope.error_kind == "schema_invalid"
        # Still recorded — every call writes exactly one row, even unknown ones.
        assert len(writer.rows) == 1


class TestEmitsExactlyOneInvocationRow:
    """Per tools-prd §3.2: exactly one ``runtime_tool_invocations`` row per call."""

    def test_one_row_on_success(self) -> None:
        bundle = _echo_bundle()
        fetcher = FakeFetcher(bundles={bundle.tool_id: bundle})
        writer = RecordingWriter()
        adapter = CodeToolAdapter(
            runtime_context=_ctx(run_id="run_one"),
            sandbox=InProcessCodeSandbox(),
            fetcher=fetcher,
            invocation_writer=writer,
        )
        asyncio.run(adapter.ainvoke(tool_id=bundle.tool_id, args={"k": 1}))
        assert len(writer.rows) == 1
        row = writer.rows[0]
        assert row["tool_id"] == bundle.tool_id
        assert row["tool_name"] == bundle.name
        assert row["run_id"] == "run_one"
        assert row["org_id"] == "org_alpha"
        assert row["user_id"] == "user_alpha"
        assert row["status"] == "ok"
        assert row["error_kind"] is None
        assert row["args"] == {"k": 1}
        assert row["call_id"].startswith("codecall_")

    def test_one_row_on_failure(self) -> None:
        bundle = CodeToolBundle(
            tool_id="tool_crash",
            name="crash",
            code="def run(args):\n    raise RuntimeError('x')\n",
            entry="run",
            timeout_s=0.5,
        )
        fetcher = FakeFetcher(bundles={bundle.tool_id: bundle})
        writer = RecordingWriter()
        adapter = CodeToolAdapter(
            runtime_context=_ctx(),
            sandbox=InProcessCodeSandbox(),
            fetcher=fetcher,
            invocation_writer=writer,
        )
        envelope = asyncio.run(adapter.ainvoke(tool_id=bundle.tool_id, args={}))
        assert envelope.status == "error"
        assert envelope.error_kind == "sandbox_crash"
        assert len(writer.rows) == 1
        assert writer.rows[0]["status"] == "error"
        assert writer.rows[0]["error_kind"] == "sandbox_crash"

    def test_one_row_per_repeated_call(self) -> None:
        bundle = _echo_bundle()
        fetcher = FakeFetcher(bundles={bundle.tool_id: bundle})
        writer = RecordingWriter()
        adapter = CodeToolAdapter(
            runtime_context=_ctx(),
            sandbox=InProcessCodeSandbox(),
            fetcher=fetcher,
            invocation_writer=writer,
        )
        for i in range(3):
            asyncio.run(adapter.ainvoke(tool_id=bundle.tool_id, args={"i": i}))
        assert len(writer.rows) == 3
        # Each row has a distinct call_id.
        assert len({row["call_id"] for row in writer.rows}) == 3
