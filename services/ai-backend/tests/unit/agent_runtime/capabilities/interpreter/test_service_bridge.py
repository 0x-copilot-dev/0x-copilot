"""The external-call bridge: every Monty external call goes through the shared
policy seam (approval + budget) and emits ordered events.

This is the AC6 acceptance test that a programmatic tool call does **not** bypass
the normal permission/approval/budget path. It uses the real Monty adapter and a
recording :class:`PolicyToolInvoker` that itself consults an approval gate and a
budget gate, so the test proves each is consulted once per external call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterCompleted,
    InterpreterErrorCode,
    InterpreterFailed,
    InterpreterLimitKind,
    InterpreterLimitProfiles,
    InterpreterLimits,
    RunCodeModeInput,
)
from agent_runtime.capabilities.interpreter.monty_adapter import MontyInterpreterPort
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
class RecordingApprovalGate:
    """Stands in for the four-mode approval engine (not yet wired on the direct
    path). Records each consult; can be set to reject."""

    reject: bool = False
    calls: list[str] = field(default_factory=list)

    def decide(self, *, tool_name: str) -> bool:
        self.calls.append(tool_name)
        return not self.reject


@dataclass
class RecordingBudgetGate:
    """Stands in for the per-tool budget guard. Records each check; can deny."""

    deny: bool = False
    calls: list[str] = field(default_factory=list)

    def check(self, *, tool_name: str) -> bool:
        self.calls.append(tool_name)
        return not self.deny


@dataclass
class BridgePolicyToolInvoker:
    """Concrete PolicyToolInvoker composing budget + approval + dispatch.

    Mirrors what the production seam must do: budget first, then approval, then
    the real tool. Returns typed outcomes for denied/rejected so no side effect
    is faked.
    """

    approval: RecordingApprovalGate
    budget: RecordingBudgetGate
    return_values: dict[str, object] = field(default_factory=dict)
    dispatched: list[tuple[str, dict]] = field(default_factory=list)
    _seq: int = 0

    async def invoke(
        self, *, call: ExternalFunctionCall, context: PolicyInvocationContext
    ) -> PolicyToolInvocationOutcome:
        self._seq += 1
        inv_id = f"inv-{self._seq}"
        tool_name = context.spec.tool_name
        if not self.budget.check(tool_name=tool_name):
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.DENIED,
                invocation_id=inv_id,
                error_code=InterpreterErrorCode.EXTERNAL_FUNCTION_DENIED,
                safe_message="budget exhausted",
            )
        if not self.approval.decide(tool_name=tool_name):
            return PolicyToolInvocationOutcome(
                status=PolicyToolInvocationOutcome.REJECTED,
                invocation_id=inv_id,
                safe_message="approval rejected",
            )
        self.dispatched.append((call.alias, dict(call.arguments)))
        return PolicyToolInvocationOutcome(
            status=PolicyToolInvocationOutcome.ALLOWED,
            invocation_id=inv_id,
            return_value=self.return_values.get(call.alias, 0),
        )


@dataclass
class RecordingEventSink:
    events: list[tuple[str, dict]] = field(default_factory=list)

    async def emit(self, *, name: str, payload: dict) -> None:
        self.events.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


@dataclass
class DictResolver:
    mapping: dict[str, str]

    def resolve(self, alias: str) -> ExternalFunctionSpec | None:
        tool = self.mapping.get(alias)
        if tool is None:
            return None
        return ExternalFunctionSpec(alias=alias, tool_name=tool)


# --- harness ----------------------------------------------------------------


class ServiceMixin:
    def _service(
        self,
        tmp_path,
        *,
        invoker: BridgePolicyToolInvoker,
        resolver: DictResolver,
        sink: RecordingEventSink,
        limits: InterpreterLimits | None = None,
    ) -> InterpreterService:
        store = ObjectStoreSnapshotStore(FileObjectStore(FileStoreLayout(tmp_path)))
        port = MontyInterpreterPort(snapshot_store=store)
        config = InterpreterServiceConfig(limits_override=limits)
        return InterpreterService(
            port=port,
            policy_invoker=invoker,
            resolver=resolver,
            config=config,
            event_sink=sink,
        )

    def _bridge(
        self, *, reject=False, deny=False, values=None
    ) -> BridgePolicyToolInvoker:
        return BridgePolicyToolInvoker(
            approval=RecordingApprovalGate(reject=reject),
            budget=RecordingBudgetGate(deny=deny),
            return_values=values or {},
        )


# --- tests ------------------------------------------------------------------


class TestExternalCallBridge(ServiceMixin):
    async def test_external_call_routes_through_budget_approval_and_events(
        self, tmp_path
    ) -> None:
        invoker = self._bridge(values={"search": 41})
        sink = RecordingEventSink()
        resolver = DictResolver({"search": "tools.search_web"})
        service = self._service(tmp_path, invoker=invoker, resolver=resolver, sink=sink)

        result = await service.run(
            RunCodeModeInput(
                code="search('hello') + 1", external_functions=("search",)
            ),
            run_id="run-1",
        )

        assert isinstance(result, InterpreterCompleted)
        assert result.result == 42
        # Budget + approval each consulted exactly once, for the real tool name.
        assert invoker.budget.calls == ["tools.search_web"]
        assert invoker.approval.calls == ["tools.search_web"]
        assert invoker.dispatched == [("search", {"args": ["hello"]})]
        # Ordered lifecycle events, including the per-call approval boundary.
        names = sink.names()
        assert names[0] == InterpreterEvents.STARTED
        assert InterpreterEvents.EXTERNAL_CALL_REQUESTED in names
        assert InterpreterEvents.SUSPENDED_FOR_APPROVAL in names
        assert InterpreterEvents.RESUMED in names
        assert names[-1] == InterpreterEvents.COMPLETED

    async def test_loop_charges_budget_and_approval_per_iteration(
        self, tmp_path
    ) -> None:
        invoker = self._bridge(values={"tick": 1})
        resolver = DictResolver({"tick": "tools.tick"})
        service = self._service(
            tmp_path, invoker=invoker, resolver=resolver, sink=RecordingEventSink()
        )
        result = await service.run(
            RunCodeModeInput(
                code="total = 0\nfor _ in range(3):\n    total = total + tick()\ntotal",
                external_functions=("tick",),
            ),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterCompleted)
        assert result.result == 3
        # One budget check and one approval per loop iteration — no bypass.
        assert invoker.budget.calls == ["tools.tick"] * 3
        assert invoker.approval.calls == ["tools.tick"] * 3

    async def test_rejected_approval_does_not_dispatch_and_program_branches(
        self, tmp_path
    ) -> None:
        invoker = self._bridge(reject=True)
        resolver = DictResolver({"search": "tools.search_web"})
        service = self._service(
            tmp_path, invoker=invoker, resolver=resolver, sink=RecordingEventSink()
        )
        code = (
            "try:\n"
            "    v = search('q')\n"
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
        assert invoker.approval.calls == ["tools.search_web"]  # consulted
        assert invoker.dispatched == []  # but the tool never ran

    async def test_budget_denied_surfaces_and_no_dispatch(self, tmp_path) -> None:
        invoker = self._bridge(deny=True)
        resolver = DictResolver({"search": "tools.search_web"})
        service = self._service(
            tmp_path, invoker=invoker, resolver=resolver, sink=RecordingEventSink()
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
        assert invoker.budget.calls == ["tools.search_web"]
        assert invoker.approval.calls == []  # budget denied before approval
        assert invoker.dispatched == []

    async def test_unknown_alias_fails_closed_before_run(self, tmp_path) -> None:
        invoker = self._bridge()
        resolver = DictResolver({})  # resolves nothing
        service = self._service(
            tmp_path, invoker=invoker, resolver=resolver, sink=RecordingEventSink()
        )
        result = await service.run(
            RunCodeModeInput(code="search('q')", external_functions=("search",)),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterFailed)
        assert result.code is InterpreterErrorCode.EXTERNAL_FUNCTION_UNKNOWN

    async def test_external_call_ceiling_enforced(self, tmp_path) -> None:
        invoker = self._bridge(values={"tick": 1})
        resolver = DictResolver({"tick": "tools.tick"})
        # Lower the external-call ceiling to 2 via an explicit limits override.
        tight = InterpreterLimitProfiles.resolve("desktop_v1").model_dump()
        tight["max_external_calls"] = 2
        service = self._service(
            tmp_path,
            invoker=invoker,
            resolver=resolver,
            sink=RecordingEventSink(),
            limits=InterpreterLimits(**tight),
        )
        result = await service.run(
            RunCodeModeInput(
                code="for _ in range(10):\n    tick()\n0",
                external_functions=("tick",),
            ),
            run_id="run-1",
        )
        assert isinstance(result, InterpreterFailed)
        assert result.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert result.limit_kind is InterpreterLimitKind.EXTERNAL_CALLS
