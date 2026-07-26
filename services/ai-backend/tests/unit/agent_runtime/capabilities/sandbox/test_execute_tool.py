"""The model tool creates a canonical gateway operation; it never owns execution."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.sandbox.execute_tool import (
    TOOL_NAME,
    SandboxExecuteToolFactory,
    SandboxRunIdentity,
)
from agent_runtime.capabilities.sandbox.operation_adapter import (
    SandboxOperationAdapter,
    SandboxOperationAvailability,
    SandboxOperationLaunch,
    SandboxOperationRunResult,
    SandboxOperationRunnerPort,
    sandbox_operation_descriptor,
)
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxResolvedSnapshotSource,
    SandboxSnapshotFileStorePort,
    SandboxSnapshotPlan,
    SandboxSnapshotPlanProvider,
    SandboxSnapshotSource,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot

_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440000/revisions/1"
_RESULT_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440001/revisions/1"


@dataclass
class _SnapshotProvider(SandboxSnapshotPlanProvider):
    calls: list[tuple[str, str | None, str | None]] = field(default_factory=list)

    async def snapshot_for(
        self, *, run_id: str, org_id: str | None, user_id: str | None
    ) -> SandboxSnapshotPlan:
        self.calls.append((run_id, org_id, user_id))
        return SandboxSnapshotPlan.model_validate(
            {
                "entries": [
                    {
                        "virtual_path": "/workspace/input.txt",
                        "source": {"kind": "artifact", "source_ref": _ARTIFACT},
                    }
                ]
            }
        )


class _SnapshotStore(SandboxSnapshotFileStorePort):
    async def resolve(
        self, *, source: SandboxSnapshotSource
    ) -> SandboxResolvedSnapshotSource | None:
        return SandboxResolvedSnapshotSource(
            kind=source.kind,
            source_ref=source.source_ref,
            content_ref="artifact-blob://sha256/" + "a" * 64,
            content_digest="a" * 64,
            size_bytes=3,
        )

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        del content_ref
        if False:  # pragma: no cover - structural protocol implementation.
            yield b""


@dataclass
class _Runner(SandboxOperationRunnerPort):
    _availability: SandboxOperationAvailability = field(
        default_factory=lambda: SandboxOperationAvailability(available=True)
    )
    calls: list[SandboxOperationLaunch] = field(default_factory=list)

    @property
    def availability(self) -> SandboxOperationAvailability:
        return self._availability

    async def run(
        self, *, request: SandboxOperationLaunch
    ) -> SandboxOperationRunResult:
        self.calls.append(request)
        return SandboxOperationRunResult(
            run_id=request.run_id,
            operation_id=request.operation_id,
            result_ref=_RESULT_ARTIFACT,
            safe_summary="Sandbox command completed.",
        )


def _bind_context():
    return OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id="org_1",
            user_id="user_1",
            conversation_id="conv_1",
            run_id="run_1",
        ),
        policy_snapshot=ToolUsePolicySnapshot.from_response(workspace=None, user=None),
        ledger_emitter=None,
        artifact_service=None,
        mode=OperationGatewayMode.ENFORCE,
        canonical_arguments_durable=True,
    )


def _build_tool(runner: _Runner, snapshot_provider: _SnapshotProvider):
    adapter = SandboxOperationAdapter(runner=runner, snapshot_store=_SnapshotStore())
    return SandboxExecuteToolFactory.build(
        gateway=OperationGateway(
            descriptors=OperationDescriptorRegistry(
                entries=(sandbox_operation_descriptor(),)
            )
        ),
        adapter=adapter,
        identity_provider=lambda: SandboxRunIdentity(
            run_id="run_1", org_id="org_1", user_id="user_1"
        ),
        snapshot_provider=snapshot_provider,
    )


class TestRunInSandbox:
    def test_tool_identity(self) -> None:
        tool = _build_tool(_Runner(), _SnapshotProvider())
        assert tool is not None
        assert tool.name == TOOL_NAME
        assert set(tool.args_schema.model_fields) == {"command"}

    async def test_creates_gateway_operation_and_returns_only_result_ref(self) -> None:
        runner = _Runner()
        snapshots = _SnapshotProvider()
        tool = _build_tool(runner, snapshots)
        assert tool is not None
        token = _bind_context()
        try:
            payload = json.loads(await tool.ainvoke({"command": "echo hi"}))
        finally:
            OperationContext.unbind(token)

        assert payload["status"] == "completed"
        assert payload["result_ref"] == _RESULT_ARTIFACT
        assert "output" not in payload
        assert len(runner.calls) == 1
        assert runner.calls[0].command == "echo hi"
        assert runner.calls[0].egress_mode == "deny_all"
        assert snapshots.calls == [("run_1", "org_1", "user_1")]

    async def test_stale_tool_reports_unavailable_without_creating_an_operation(
        self,
    ) -> None:
        runner = _Runner()
        tool = _build_tool(runner, _SnapshotProvider())
        assert tool is not None
        runner._availability = SandboxOperationAvailability(
            available=False, reason="provider_unavailable"
        )

        payload = json.loads(await tool.ainvoke({"command": "echo should-not-run"}))

        assert payload == {
            "status": "unavailable",
            "summary": "Sandbox execution is unavailable; no command was run.",
            "reason": "provider_unavailable",
        }
        assert runner.calls == []

    def test_tool_is_absent_when_provider_cannot_verify_posture(self) -> None:
        runner = _Runner(
            _availability=SandboxOperationAvailability(
                available=False, reason="isolation_unverified"
            )
        )

        assert _build_tool(runner, _SnapshotProvider()) is None
