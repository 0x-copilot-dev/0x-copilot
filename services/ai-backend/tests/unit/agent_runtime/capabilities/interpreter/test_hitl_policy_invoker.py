"""Option B: the production ``HitlPolicyToolInvoker`` bridge.

Two levels of coverage:

* **Unit** — the invoker composes budget -> approval -> dispatch and returns the
  right typed outcome for every branch, dispatching *only* on approval.
* **End-to-end through the real Monty adapter + service** — a declared external
  call routes through budget, a real LangGraph-shaped approval interrupt (faked
  synchronously), and dispatch, and the tool's value lands back in the running
  program. Rejection and budget-denial branch the program without a side effect;
  an undeclared alias fails closed before the program runs.

The collaborators (``InterruptApprovalGate``, ``LangChainToolDispatcher``,
``AuthorizedToolResolver``) are exercised directly too.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterCompleted,
    InterpreterErrorCode,
    InterpreterFailed,
    RunCodeModeInput,
    SnapshotRef,
)
from agent_runtime.capabilities.interpreter.monty_adapter import MontyInterpreterPort
from agent_runtime.capabilities.interpreter.policy_invoker import (
    AuthorizedToolResolver,
    ExternalToolDispatchError,
    HitlPolicyToolInvoker,
    InterruptApprovalGate,
    LangChainToolDispatcher,
)
from agent_runtime.capabilities.interpreter.ports import (
    PolicyInvocationContext,
    PolicyToolInvocationOutcome,
)
from agent_runtime.capabilities.interpreter.service import (
    InterpreterEvents,
    InterpreterService,
    InterpreterServiceConfig,
)
from agent_runtime.capabilities.interpreter.snapshot_store import (
    ObjectStoreSnapshotStore,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore


# --- fakes -----------------------------------------------------------------


@dataclass
class FakeBudget:
    admit: bool = True
    calls: list[str] = field(default_factory=list)

    async def charge(self, *, tool_name, arguments, context) -> bool:
        del arguments, context
        self.calls.append(tool_name)
        return self.admit


@dataclass
class FakeApproval:
    approve: bool = True
    calls: list[str] = field(default_factory=list)

    async def request_approval(self, *, spec, call, context) -> bool:
        del call, context
        self.calls.append(spec.tool_name)
        return self.approve


@dataclass
class FakeDispatcher:
    return_values: dict[str, object] = field(default_factory=dict)
    raise_for: set[str] = field(default_factory=set)
    dispatched: list[tuple[str, dict]] = field(default_factory=list)

    async def dispatch(self, *, spec, arguments, context):
        del context
        if spec.tool_name in self.raise_for:
            raise ExternalToolDispatchError("the external tool call failed")
        self.dispatched.append((spec.tool_name, dict(arguments)))
        return self.return_values.get(spec.tool_name, 0)


def _spec(
    alias: str = "search", tool: str = "tools.search_web"
) -> ExternalFunctionSpec:
    return ExternalFunctionSpec(alias=alias, tool_name=tool)


def _context(spec: ExternalFunctionSpec) -> PolicyInvocationContext:
    return PolicyInvocationContext(
        run_id="run-1",
        interpreter_session_id="sess-1",
        org_id="org-1",
        user_id="user-1",
        spec=spec,
    )


def _call(alias: str = "search", args: dict | None = None) -> ExternalFunctionCall:
    return ExternalFunctionCall(
        interpreter_session_id="sess-1",
        invocation_index=0,
        alias=alias,
        arguments=args or {"args": ["hello"]},
        snapshot=SnapshotRef(
            sha256="a" * 64,
            size=1,
            adapter="monty",
            abi_version="1",
            source_sha256="b" * 64,
            limit_profile_hash="h",
            invocation_index=0,
        ),
        source_sha256="b" * 64,
    )


# --- unit: the invoker composition -----------------------------------------


class TestHitlInvokerComposition:
    async def test_allowed_charges_approves_then_dispatches(self) -> None:
        budget = FakeBudget()
        approval = FakeApproval()
        dispatcher = FakeDispatcher(return_values={"tools.search_web": 42})
        invoker = HitlPolicyToolInvoker(
            budget=budget, approval=approval, dispatcher=dispatcher
        )
        spec = _spec()
        outcome = await invoker.invoke(call=_call(), context=_context(spec))

        assert outcome.status == PolicyToolInvocationOutcome.ALLOWED
        assert outcome.return_value == 42
        assert budget.calls == ["tools.search_web"]
        assert approval.calls == ["tools.search_web"]
        assert dispatcher.dispatched == [("tools.search_web", {"args": ["hello"]})]

    async def test_budget_denied_short_circuits_before_approval(self) -> None:
        budget = FakeBudget(admit=False)
        approval = FakeApproval()
        dispatcher = FakeDispatcher()
        invoker = HitlPolicyToolInvoker(
            budget=budget, approval=approval, dispatcher=dispatcher
        )
        spec = _spec()
        outcome = await invoker.invoke(call=_call(), context=_context(spec))

        assert outcome.status == PolicyToolInvocationOutcome.DENIED
        assert outcome.error_code is InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED
        assert approval.calls == []  # never consulted
        assert dispatcher.dispatched == []  # never dispatched

    async def test_rejected_approval_does_not_dispatch(self) -> None:
        budget = FakeBudget()
        approval = FakeApproval(approve=False)
        dispatcher = FakeDispatcher()
        invoker = HitlPolicyToolInvoker(
            budget=budget, approval=approval, dispatcher=dispatcher
        )
        spec = _spec()
        outcome = await invoker.invoke(call=_call(), context=_context(spec))

        assert outcome.status == PolicyToolInvocationOutcome.REJECTED
        assert budget.calls == ["tools.search_web"]  # budget still charged
        assert dispatcher.dispatched == []

    async def test_dispatch_error_surfaces_as_error_outcome(self) -> None:
        budget = FakeBudget()
        approval = FakeApproval()
        dispatcher = FakeDispatcher(raise_for={"tools.search_web"})
        invoker = HitlPolicyToolInvoker(
            budget=budget, approval=approval, dispatcher=dispatcher
        )
        spec = _spec()
        outcome = await invoker.invoke(call=_call(), context=_context(spec))

        assert outcome.status == PolicyToolInvocationOutcome.ERROR
        assert outcome.safe_message == "the external tool call failed"


# --- unit: the interrupt approval gate -------------------------------------


class TestInterruptApprovalGate:
    async def test_emits_interrupt_payload_and_reads_approved_decision(self) -> None:
        captured: dict = {}

        def _handler(payload: dict) -> object:
            captured.update(payload)
            return {"decision": "approved"}

        gate = InterruptApprovalGate(interrupt_handler=_handler)
        spec = _spec()
        approved = await gate.request_approval(
            spec=spec, call=_call(), context=_context(spec)
        )

        assert approved is True
        # The payload carries the approval discriminator + tool name, no internals.
        assert captured["approval_kind"] == InterruptApprovalGate.APPROVAL_KIND
        assert captured["tool_name"] == "tools.search_web"
        assert captured["invocation_index"] == 0
        assert "snapshot" not in captured and "code" not in captured

    async def test_rejected_decision(self) -> None:
        gate = InterruptApprovalGate(
            interrupt_handler=lambda _payload: {"decision": "rejected"}
        )
        spec = _spec()
        assert (
            await gate.request_approval(spec=spec, call=_call(), context=_context(spec))
            is False
        )

    async def test_batch_shape_decision(self) -> None:
        gate = InterruptApprovalGate(
            interrupt_handler=lambda _payload: {"decisions": [{"type": "approve"}]}
        )
        spec = _spec()
        assert (
            await gate.request_approval(spec=spec, call=_call(), context=_context(spec))
            is True
        )

    async def test_unrecognised_resume_fails_closed(self) -> None:
        gate = InterruptApprovalGate(interrupt_handler=lambda _payload: object())
        spec = _spec()
        assert (
            await gate.request_approval(spec=spec, call=_call(), context=_context(spec))
            is False
        )


# --- unit: the dispatcher + resolver ---------------------------------------


class TestLangChainToolDispatcher:
    async def test_dispatches_to_named_tool(self) -> None:
        async def _echo(value: str) -> str:
            return f"got:{value}"

        tool = StructuredTool.from_function(
            coroutine=_echo, name="tools.echo", description="echo"
        )
        dispatcher = LangChainToolDispatcher({"tools.echo": tool})
        spec = _spec(alias="echo", tool="tools.echo")
        result = await dispatcher.dispatch(
            spec=spec, arguments={"value": "hi"}, context=_context(spec)
        )
        assert result == "got:hi"

    async def test_unknown_tool_raises_typed_error(self) -> None:
        dispatcher = LangChainToolDispatcher({})
        spec = _spec(alias="nope", tool="tools.nope")
        try:
            await dispatcher.dispatch(spec=spec, arguments={}, context=_context(spec))
        except ExternalToolDispatchError as exc:
            assert exc.safe_message == "the external tool is not available"
        else:  # pragma: no cover - must raise
            raise AssertionError("expected ExternalToolDispatchError")

    async def test_tool_failure_is_wrapped(self) -> None:
        async def _boom() -> str:
            raise ValueError("secret internal detail")

        tool = StructuredTool.from_function(
            coroutine=_boom, name="tools.boom", description="boom"
        )
        dispatcher = LangChainToolDispatcher({"tools.boom": tool})
        spec = _spec(alias="boom", tool="tools.boom")
        try:
            await dispatcher.dispatch(spec=spec, arguments={}, context=_context(spec))
        except ExternalToolDispatchError as exc:
            # Safe message only — the ValueError text must not leak.
            assert exc.safe_message == "the external tool call failed"
            assert "secret" not in exc.safe_message
        else:  # pragma: no cover
            raise AssertionError("expected ExternalToolDispatchError")


class TestAuthorizedToolResolver:
    def test_resolves_authorized_alias(self) -> None:
        resolver = AuthorizedToolResolver({"search": object()})
        spec = resolver.resolve("search")
        assert spec is not None
        assert spec.alias == "search"
        assert spec.tool_name == "search"

    def test_unknown_alias_resolves_none(self) -> None:
        resolver = AuthorizedToolResolver({"search": object()})
        assert resolver.resolve("delete_everything") is None


# --- end-to-end through the real Monty adapter -----------------------------


@dataclass
class _EventSink:
    events: list[tuple[str, dict]] = field(default_factory=list)

    async def emit(self, *, name: str, payload: dict) -> None:
        self.events.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class BridgeMixin:
    def _service(
        self, tmp_path, *, budget, approval, dispatcher, sink=None
    ) -> InterpreterService:
        store = ObjectStoreSnapshotStore(FileObjectStore(FileStoreLayout(tmp_path)))
        port = MontyInterpreterPort(snapshot_store=store)
        invoker = HitlPolicyToolInvoker(
            budget=budget, approval=approval, dispatcher=dispatcher
        )
        # The resolver authorizes the aliases the model may declare.
        resolver = AuthorizedToolResolver({"search": object(), "tick": object()})
        return InterpreterService(
            port=port,
            policy_invoker=invoker,
            resolver=resolver,
            config=InterpreterServiceConfig(),
            event_sink=sink or _EventSink(),
        )


class TestEndToEndThroughMonty(BridgeMixin):
    async def test_external_call_routes_approval_dispatch_resume(
        self, tmp_path
    ) -> None:
        budget = FakeBudget()
        approval = FakeApproval()
        dispatcher = FakeDispatcher(return_values={"search": 41})
        sink = _EventSink()
        service = self._service(
            tmp_path,
            budget=budget,
            approval=approval,
            dispatcher=dispatcher,
            sink=sink,
        )
        result = await service.run(
            RunCodeModeInput(
                code="search('hello') + 1", external_functions=("search",)
            ),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterCompleted)
        # The dispatched tool value (41) was injected and the program continued.
        assert result.result == 42
        # Budget then approval then dispatch, each once, for the resolved tool name.
        assert budget.calls == ["search"]
        assert approval.calls == ["search"]
        assert dispatcher.dispatched == [("search", {"args": ["hello"]})]
        names = sink.names()
        assert names[0] == InterpreterEvents.STARTED
        assert InterpreterEvents.SUSPENDED_FOR_APPROVAL in names
        assert names[-1] == InterpreterEvents.COMPLETED

    async def test_rejection_branches_program_without_dispatch(self, tmp_path) -> None:
        dispatcher = FakeDispatcher()
        service = self._service(
            tmp_path,
            budget=FakeBudget(),
            approval=FakeApproval(approve=False),
            dispatcher=dispatcher,
        )
        code = (
            "try:\n"
            "    search('q')\n"
            "    out = 'ran'\n"
            "except Exception:\n"
            "    out = 'rejected'\n"
            "out\n"
        )
        result = await service.run(
            RunCodeModeInput(code=code, external_functions=("search",)),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterCompleted)
        assert result.result == "rejected"
        assert dispatcher.dispatched == []  # tool never ran

    async def test_budget_denied_surfaces_without_dispatch(self, tmp_path) -> None:
        approval = FakeApproval()
        dispatcher = FakeDispatcher()
        service = self._service(
            tmp_path,
            budget=FakeBudget(admit=False),
            approval=approval,
            dispatcher=dispatcher,
        )
        code = (
            "try:\n"
            "    search('q')\n"
            "    out = 'ran'\n"
            "except Exception:\n"
            "    out = 'denied'\n"
            "out\n"
        )
        result = await service.run(
            RunCodeModeInput(code=code, external_functions=("search",)),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterCompleted)
        assert result.result == "denied"
        assert approval.calls == []  # budget denied before approval
        assert dispatcher.dispatched == []

    async def test_undeclared_alias_fails_closed(self, tmp_path) -> None:
        service = self._service(
            tmp_path,
            budget=FakeBudget(),
            approval=FakeApproval(),
            dispatcher=FakeDispatcher(),
        )
        result = await service.run(
            # 'wipe' is not in the resolver's authorized set.
            RunCodeModeInput(code="wipe()", external_functions=("wipe",)),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterFailed)
        assert result.code is InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN
