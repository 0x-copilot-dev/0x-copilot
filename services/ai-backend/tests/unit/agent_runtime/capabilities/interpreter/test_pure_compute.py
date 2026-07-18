"""Pure-compute posture: code mode has no external-tool surface, fails closed."""

from __future__ import annotations

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    InterpreterErrorCode,
    SnapshotRef,
)
from agent_runtime.capabilities.interpreter.ports import (
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
)
from agent_runtime.capabilities.interpreter.pure_compute import (
    ClosedPolicyInvoker,
    PureComputeResolver,
)
from agent_runtime.capabilities.interpreter.service import (
    InterpreterService,
)


class TestPureComputeResolver:
    def test_resolves_nothing(self) -> None:
        resolver = PureComputeResolver()
        assert resolver.resolve("any_alias") is None
        assert resolver.resolve("tools.web_search") is None


class TestClosedPolicyInvoker:
    async def test_denies_never_allows(self) -> None:
        outcome = await ClosedPolicyInvoker().invoke(
            call=_call(),
            context=PolicyInvocationContext(
                run_id="run-1",
                interpreter_session_id="sess-1",
                spec=_spec(),
            ),
        )
        assert outcome.status == PolicyToolInvocationOutcome.DENIED
        assert outcome.status != PolicyToolInvocationOutcome.ALLOWED
        assert outcome.error_code is InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN


class _FakePort:
    """Port whose ``start`` is never reached — resolution fails first."""

    async def start(self, request: object) -> object:  # pragma: no cover - defensive
        raise AssertionError("start must not run when an alias fails to resolve")

    async def resume(self, **_: object) -> object:  # pragma: no cover - defensive
        raise AssertionError("resume must not run")

    async def cancel(self, **_: object) -> None:  # pragma: no cover - defensive
        return None


class TestServiceFailsClosedOnExternalFunctions:
    async def test_declared_external_function_is_rejected(self) -> None:
        from agent_runtime.capabilities.interpreter.contracts import RunCodeModeInput

        service = InterpreterService(
            port=_FakePort(),
            policy_invoker=ClosedPolicyInvoker(),
            resolver=PureComputeResolver(),
        )
        outcome = await service.run(
            RunCodeModeInput(code="x = 1", external_functions=("web_search",)),
            run_id="run-1",
        )
        # No alias resolves, so the program never starts and no tool runs.
        assert outcome.code is InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN


def _spec() -> object:
    from agent_runtime.capabilities.interpreter.contracts import ExternalFunctionSpec

    return ExternalFunctionSpec(alias="web_search", tool_name="tools.web_search")


def _call() -> ExternalFunctionCall:
    return ExternalFunctionCall(
        interpreter_session_id="sess-1",
        alias="web_search",
        arguments={},
        invocation_index=1,
        source_sha256="0" * 64,
        snapshot=SnapshotRef(
            sha256="0" * 64,
            size=0,
            adapter="monty",
            abi_version="1",
            source_sha256="0" * 64,
            limit_profile_hash="h",
            invocation_index=1,
        ),
    )
