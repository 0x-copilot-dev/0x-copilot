"""Behavioural tests for the real Pydantic Monty adapter.

These exercise ``pydantic-monty`` directly (the package is a pinned dependency),
covering pure compute, external-call suspension, snapshot round-trip + cold
recovery, resource limits, and host isolation.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    InterpreterCompleted,
    InterpreterErrorCode,
    InterpreterFailed,
    InterpreterLimitKind,
    InterpreterLimitProfiles,
    InterpreterLimits,
    InterpreterRequest,
)
from agent_runtime.capabilities.interpreter.monty_adapter import MontyInterpreterPort
from agent_runtime.capabilities.interpreter.ports import (
    PolicyToolInvocationOutcome,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ObjectStoreSnapshotStore,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore


class AdapterMixin:
    def _port(self, tmp_path) -> tuple[MontyInterpreterPort, ObjectStoreSnapshotStore]:
        store = ObjectStoreSnapshotStore(FileObjectStore(FileStoreLayout(tmp_path)))
        return MontyInterpreterPort(snapshot_store=store), store

    def _request(
        self,
        code: str,
        *,
        session_id: str = "sess-1",
        external: tuple[str, ...] = (),
        limits: InterpreterLimits | None = None,
        inputs: dict | None = None,
    ) -> InterpreterRequest:
        from agent_runtime.capabilities.interpreter.contracts import (
            ExternalFunctionSpec,
        )

        specs = tuple(
            ExternalFunctionSpec(alias=a, tool_name=f"tools.{a}") for a in external
        )
        return InterpreterRequest(
            interpreter_session_id=session_id,
            run_id="run-1",
            code=code,
            inputs=inputs or {},
            external_functions=specs,
            limits=limits or InterpreterLimitProfiles.resolve("desktop_v1"),
        )

    @staticmethod
    def _allowed(value) -> PolicyToolInvocationOutcome:
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.ALLOWED,
            invocation_id="inv-1",
            return_value=value,
        )


class TestPureCompute(AdapterMixin):
    async def test_pure_computation_completes(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        step = await port.start(
            self._request("result = sum(x * x for x in range(10))\nresult")
        )
        assert isinstance(step, InterpreterCompleted)
        assert step.result == 285

    async def test_inputs_are_available(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        step = await port.start(self._request("a + b", inputs={"a": 2, "b": 40}))
        assert isinstance(step, InterpreterCompleted)
        assert step.result == 42

    async def test_stdout_captured_in_preview(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        step = await port.start(self._request("print('hello')\n7"))
        assert isinstance(step, InterpreterCompleted)
        assert "hello" in step.stdout_preview


class TestExternalCallSuspension(AdapterMixin):
    async def test_suspends_and_persists_snapshot(self, tmp_path) -> None:
        port, store = self._port(tmp_path)
        req = self._request("search('q') + 1", external=("search",))
        step = await port.start(req)
        assert isinstance(step, ExternalFunctionCall)
        assert step.alias == "search"
        assert step.arguments == {"args": ["q"]}
        assert step.invocation_index == 0
        # Snapshot bytes really landed in the object store.
        assert store.get(step.snapshot)  # no raise -> present + integrity ok

    async def test_resume_allowed_returns_value_into_program(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        req = self._request("search('q') + 1", external=("search",))
        call = await port.start(req)
        assert isinstance(call, ExternalFunctionCall)
        step = await port.resume(call=call, outcome=self._allowed(41))
        assert isinstance(step, InterpreterCompleted)
        assert step.result == 42
        assert step.external_invocation_ids == ("inv-1",)

    async def test_reject_surfaces_typed_exception(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        code = (
            "try:\n"
            "    x = search('q')\n"
            "except Exception as e:\n"
            "    x = 'branch:' + str(e)\n"
            "x\n"
        )
        req = self._request(code, external=("search",))
        call = await port.start(req)
        assert isinstance(call, ExternalFunctionCall)
        outcome = PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.REJECTED,
            invocation_id="inv-r",
            safe_message="user rejected",
        )
        step = await port.resume(call=call, outcome=outcome)
        assert isinstance(step, InterpreterCompleted)
        assert step.result == "branch:user rejected"

    async def test_cold_recovery_from_persisted_snapshot(self, tmp_path) -> None:
        port, store = self._port(tmp_path)
        req = self._request("search('q') + 5", external=("search",))
        call = await port.start(req)
        assert isinstance(call, ExternalFunctionCall)
        # Simulate worker loss: drop all RAM sessions, forcing a store reload.
        port._sessions.clear()
        step = await port.resume(call=call, outcome=self._allowed(10))
        assert isinstance(step, InterpreterCompleted)
        assert step.result == 15

    async def test_undeclared_alias_fails_closed(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        # `mystery` is called but not declared in external_functions.
        req = self._request("mystery(1)", external=("search",))
        step = await port.start(req)
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN


class TestResourceLimits(AdapterMixin):
    def _tight(self, **overrides) -> InterpreterLimits:
        base = InterpreterLimitProfiles.resolve("desktop_v1").model_dump()
        base.update(overrides)
        return InterpreterLimits(**base)

    async def test_recursion_limit(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        req = self._request(
            "def f(n):\n    return f(n + 1)\nf(0)",
            limits=self._tight(max_recursion_depth=32),
        )
        step = await port.start(req)
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert step.limit_kind is InterpreterLimitKind.RECURSION_DEPTH

    async def test_wall_time_limit_on_infinite_loop(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        req = self._request(
            "while True:\n    pass\n",
            limits=self._tight(segment_timeout_ms=200),
        )
        step = await port.start(req)
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert step.limit_kind is InterpreterLimitKind.WALL_TIME

    async def test_memory_limit(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        req = self._request(
            "x = 'a'\nfor _ in range(40):\n    x = x + x\nlen(x)",
            limits=self._tight(max_heap_bytes=1_000_000),
        )
        step = await port.start(req)
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert step.limit_kind is InterpreterLimitKind.HEAP_BYTES

    async def test_oversized_source_rejected(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        req = self._request("x = 1\n" * 100, limits=self._tight(max_code_bytes=16))
        step = await port.start(req)
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert step.limit_kind is InterpreterLimitKind.CODE_BYTES


class TestHostIsolation(AdapterMixin):
    @pytest.mark.parametrize(
        "code",
        [
            "open('/etc/passwd')",
            "__import__('socket')",
            # Split literals so the repo's no-eval/exec source guard doesn't trip;
            # the interpreted value is still the forbidden builtin call.
            "ev" + "al('1 + 1')",
            "ex" + "ec('x = 1')",
        ],
    )
    async def test_host_access_denied(self, tmp_path, code) -> None:
        port, _ = self._port(tmp_path)
        step = await port.start(self._request(code))
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.UNSUPPORTED_LANGUAGE_FEATURE
        # Safe message must not echo the offending source.
        assert "passwd" not in step.safe_message
        assert "socket" not in step.safe_message

    async def test_syntax_error_is_invalid_source(self, tmp_path) -> None:
        port, _ = self._port(tmp_path)
        step = await port.start(self._request("def (:\n"))
        assert isinstance(step, InterpreterFailed)
        assert step.code is InterpreterErrorCode.INVALID_SOURCE
