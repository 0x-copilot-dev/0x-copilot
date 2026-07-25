"""D2 canaries for authoritative built-in and code-mode gateway adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from agent_runtime.capabilities.interpreter.contracts import (
    ExternalFunctionCall,
    ExternalFunctionSpec,
    InterpreterCompleted,
    SnapshotRef,
)
from agent_runtime.capabilities.interpreter.code_mode_tool import (
    CodeModeToolFactory,
    RunIdentity,
)
from agent_runtime.capabilities.interpreter.policy_invoker import (
    GatewayPolicyToolInvoker,
    HitlPolicyToolInvoker,
)
from agent_runtime.capabilities.interpreter.ports import PolicyInvocationContext
from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinOperationAdapter,
    BuiltinOperationDescriptorError,
)
from agent_runtime.capabilities.operations.builtin_catalog import (
    BuiltinOperationCatalog,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    OperationGatewayMode,
    ProposedEffect,
)
from agent_runtime.capabilities.tools.builtin.ask_a_question import AskAQuestionTool
from agent_runtime.surfaces_v2.ledger_ids import OperationIdCodec
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from tests.unit.agent_runtime.capabilities.operations.helpers import (
    BoundContextMixin,
    RecordingEmitter,
)


@dataclass
class _Budget:
    calls: int = 0

    async def charge(self, **_kwargs: object) -> bool:
        self.calls += 1
        return True


@dataclass
class _Approval:
    calls: int = 0

    async def request_approval(self, **_kwargs: object) -> bool:
        self.calls += 1
        return True


@dataclass
class _Dispatcher:
    calls: int = 0

    async def dispatch(self, **_kwargs: object) -> object:
        self.calls += 1
        return {"answer": 42}


@dataclass
class _CodeService:
    calls: int = 0

    async def run(self, *_args: object, **_kwargs: object) -> InterpreterCompleted:
        self.calls += 1
        return InterpreterCompleted(result={"value": 7})


@dataclass
class _ProposalBuilder:
    calls: int = 0

    async def build_proposal(self, **_kwargs: object) -> ProposedEffect:
        self.calls += 1
        return ProposedEffect(
            stage_id="stg_00000000-0000-4000-8000-000000000001",
            proposal_ref=(
                "proposal://stg_00000000-0000-4000-8000-000000000001/revisions/1"
            ),
            safe_summary="Code-mode child is staged for review.",
        )


def _call(alias: str) -> ExternalFunctionCall:
    return ExternalFunctionCall(
        interpreter_session_id="interpreter-1",
        invocation_index=0,
        alias=alias,
        arguments={"value": "safe"},
        snapshot=SnapshotRef(
            sha256="a" * 64,
            size=1,
            adapter="test",
            abi_version="1",
            source_sha256="b" * 64,
            limit_profile_hash="test",
            invocation_index=0,
        ),
        source_sha256="b" * 64,
    )


def _context(spec: ExternalFunctionSpec) -> PolicyInvocationContext:
    return PolicyInvocationContext(
        run_id="run-1",
        interpreter_session_id="interpreter-1",
        org_id="org-1",
        user_id="user-1",
        spec=spec,
    )


class TestBuiltinGatewayAdaptation(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_question_runs_once_through_authoritative_gateway(self) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        calls = 0

        def _interrupt(_payload: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"decision": "approved", "answer": "yes"}

        try:
            result = await AskAQuestionTool(
                runtime_context=type("Runtime", (), {"run_id": "run-1"})(),
                interrupt_handler=_interrupt,
            ).ainvoke({"question": "Continue?"})
        finally:
            OperationContext.unbind(token)

        assert result == {
            "ok": True,
            "decision": "approved",
            "answer": "yes",
            "selected": [],
            "free_text": None,
        }
        assert calls == 1
        assert [event[0] for event in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
            LedgerEventType.OPERATION_COMPLETED,
        ]

    def test_missing_builtin_catalog_entry_is_a_fail_closed_canary(self) -> None:
        with pytest.raises(BuiltinOperationDescriptorError, match="absent"):
            BuiltinOperationAdapter(
                tool_name="planted_unregistered_builtin",
                catalog=BuiltinOperationCatalog(entries=()),
            )


class TestCodeModeGatewayChildren(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_internal_child_has_parent_operation_and_dispatches_once(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        budget = _Budget()
        approval = _Approval()
        dispatcher = _Dispatcher()
        spec = ExternalFunctionSpec(alias="ask", tool_name="ask_a_question")
        try:
            parent_operation_id = OperationIdCodec.format(uuid4())
            with OperationContext.operation_scope(parent_operation_id):
                outcome = await GatewayPolicyToolInvoker(
                    legacy=HitlPolicyToolInvoker(
                        budget=budget,
                        approval=approval,
                        dispatcher=dispatcher,
                    )
                ).invoke(call=_call("ask"), context=_context(spec))
        finally:
            OperationContext.unbind(token)

        assert outcome.status == "allowed"
        assert outcome.return_value == {"answer": 42}
        assert budget.calls == approval.calls == dispatcher.calls == 1
        requested = emitter.events[0][1]
        assert requested["parent_operation_id"] == parent_operation_id
        assert requested["capability"] == "builtin"
        assert requested["op"] == "ask_a_question"

    @pytest.mark.asyncio
    async def test_unknown_external_child_never_reaches_dispatcher(self) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        budget = _Budget()
        approval = _Approval()
        dispatcher = _Dispatcher()
        spec = ExternalFunctionSpec(alias="unknown", tool_name="unknown_external")
        try:
            parent_operation_id = OperationIdCodec.format(uuid4())
            with OperationContext.operation_scope(parent_operation_id):
                outcome = await GatewayPolicyToolInvoker(
                    legacy=HitlPolicyToolInvoker(
                        budget=budget,
                        approval=approval,
                        dispatcher=dispatcher,
                    )
                ).invoke(call=_call("unknown"), context=_context(spec))
        finally:
            OperationContext.unbind(token)

        assert outcome.status == "denied"
        assert budget.calls == approval.calls == 0
        assert dispatcher.calls == 0
        assert emitter.events[0][1]["parent_operation_id"] == parent_operation_id
        assert emitter.events[-1][0] is LedgerEventType.OPERATION_FAILED

    @pytest.mark.asyncio
    async def test_effectful_child_builds_a_proposal_without_inline_approval_or_dispatch(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        budget = _Budget()
        approval = _Approval()
        dispatcher = _Dispatcher()
        builder = _ProposalBuilder()
        spec = ExternalFunctionSpec(alias="rows", tool_name="stage_rowset_write")
        try:
            parent_operation_id = OperationIdCodec.format(uuid4())
            with OperationContext.operation_scope(parent_operation_id):
                outcome = await GatewayPolicyToolInvoker(
                    legacy=HitlPolicyToolInvoker(
                        budget=budget,
                        approval=approval,
                        dispatcher=dispatcher,
                    ),
                    proposal_builder=builder,
                ).invoke(call=_call("rows"), context=_context(spec))
        finally:
            OperationContext.unbind(token)

        assert outcome.status == "denied"
        assert "staged" in (outcome.safe_message or "")
        assert budget.calls == builder.calls == 1
        assert approval.calls == dispatcher.calls == 0
        assert [event[0] for event in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
            LedgerEventType.OPERATION_COMPLETED,
        ]

    @pytest.mark.asyncio
    async def test_run_code_mode_is_an_operation_without_implicit_artifact(
        self,
    ) -> None:
        emitter = RecordingEmitter()
        token = self.bind(
            emitter=emitter,
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        service = _CodeService()
        try:
            tool = CodeModeToolFactory.build(
                service=service,  # type: ignore[arg-type]
                identity_provider=lambda: RunIdentity(run_id="run-1"),
            )
            result = await tool.ainvoke({"code": "1 + 2"})
        finally:
            OperationContext.unbind(token)

        assert '"status": "completed"' in result
        assert service.calls == 1
        assert [event[0] for event in emitter.events] == [
            LedgerEventType.OPERATION_REQUESTED,
            LedgerEventType.OPERATION_CLASSIFIED,
            LedgerEventType.OPERATION_COMPLETED,
        ]
        assert all("artifact" not in payload for _, payload, _ in emitter.events)
