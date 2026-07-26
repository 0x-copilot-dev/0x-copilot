"""D3 sandbox adapter tests: gateway identity, immutable snapshots, no bypass."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.sandbox.operation_adapter import (
    SANDBOX_CAPABILITY,
    SANDBOX_EXECUTE_OPERATION,
    SandboxOperationAdapter,
    SandboxOperationAvailability,
    SandboxOperationLaunch,
    SandboxPatchManifestRef,
    SandboxOperationRunResult,
    SandboxOperationRunnerPort,
    sandbox_operation_descriptor,
)
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxResolvedSnapshotSource,
    SandboxSnapshotFileStorePort,
    SandboxSnapshotPlan,
    SandboxSnapshotSource,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.surfaces_v2.ledger_models import OperationOutcome

_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440000/revisions/1"
_RESULT_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440001/revisions/1"


@dataclass
class _Store(SandboxSnapshotFileStorePort):
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


def _plan() -> SandboxSnapshotPlan:
    return SandboxSnapshotPlan.model_validate(
        {
            "entries": [
                {
                    "virtual_path": "/workspace/report.csv",
                    "source": {
                        "kind": "artifact",
                        "source_ref": _ARTIFACT,
                    },
                }
            ]
        }
    )


def _bind_context():
    return OperationContext.bind_for_run(
        identity=VerifiedOperationIdentity(
            org_id="org_sandbox",
            user_id="user_sandbox",
            conversation_id="conv_sandbox",
            run_id="run_sandbox",
        ),
        policy_snapshot=ToolUsePolicySnapshot.from_response(workspace=None, user=None),
        ledger_emitter=None,
        artifact_service=None,
        mode=OperationGatewayMode.ENFORCE,
        canonical_arguments_durable=True,
    )


def _gateway() -> OperationGateway:
    return OperationGateway(
        descriptors=OperationDescriptorRegistry(
            entries=(sandbox_operation_descriptor(),)
        )
    )


class TestSandboxOperationAdapter:
    def test_result_reference_must_be_an_immutable_artifact_revision(self) -> None:
        with pytest.raises(ValidationError, match="logical reference"):
            SandboxOperationRunResult(
                run_id="run_sandbox",
                operation_id="operation_sandbox",
                result_ref="sandbox-result://operations/operation_sandbox",
                safe_summary="Sandbox command completed.",
            )

    def test_patch_reference_cannot_create_a_second_sandbox_authority(self) -> None:
        with pytest.raises(ValidationError, match="logical reference"):
            SandboxPatchManifestRef(
                patch_ref="sandbox-patch://operations/operation_sandbox",
                baseline_snapshot_digest="a" * 64,
                manifest_digest="b" * 64,
            )

    def test_unavailable_reason_must_be_a_safe_code(self) -> None:
        with pytest.raises(ValidationError):
            SandboxOperationAvailability(available=False, reason="file:///secret")

    async def test_routes_exact_snapshot_through_gateway_with_stable_idempotency(
        self,
    ) -> None:
        runner = _Runner()
        adapter = SandboxOperationAdapter(runner=runner, snapshot_store=_Store())
        token = _bind_context()
        try:
            request = OperationRequestFactory.create(
                capability=SANDBOX_CAPABILITY,
                op=SANDBOX_EXECUTE_OPERATION,
                arguments={
                    "command": "python report.py",
                    "snapshot": _plan().model_dump(),
                },
            )
            first = await _gateway().invoke(request, adapter)
            second = await _gateway().invoke(request, adapter)
        finally:
            OperationContext.unbind(token)

        assert first.outcome is OperationOutcome.SUCCEEDED
        assert second == first
        assert len(runner.calls) == 1
        launch = runner.calls[0]
        assert launch.operation_id == request.operation_id
        assert launch.run_id == request.run_id
        assert launch.idempotency_key == SandboxOperationAdapter.idempotency_key(
            request=request, snapshot=launch.snapshot
        )
        assert launch.egress_mode == "deny_all"
        assert launch.secret_refs == ()
        assert launch.snapshot.entries[0].virtual_path == "/workspace/report.csv"
        assert adapter.result_for(request.operation_id) is not None

    async def test_rejects_local_path_before_runner_is_called(self) -> None:
        runner = _Runner()
        adapter = SandboxOperationAdapter(runner=runner, snapshot_store=_Store())
        token = _bind_context()
        try:
            request = OperationRequestFactory.create(
                capability=SANDBOX_CAPABILITY,
                op=SANDBOX_EXECUTE_OPERATION,
                arguments={
                    "command": "cat report.csv",
                    "snapshot": {
                        "entries": [
                            {
                                "virtual_path": "/Users/alice/report.csv",
                                "source": {
                                    "kind": "artifact",
                                    "source_ref": _ARTIFACT,
                                },
                            }
                        ]
                    },
                },
            )
            disposition = await _gateway().invoke(request, adapter)
        finally:
            OperationContext.unbind(token)

        assert disposition.outcome is OperationOutcome.FAILED
        assert runner.calls == []

    async def test_provider_unavailability_never_calls_lifecycle_runner(self) -> None:
        runner = _Runner(
            _availability=SandboxOperationAvailability(
                available=False, reason="isolation_unverified"
            )
        )
        adapter = SandboxOperationAdapter(runner=runner, snapshot_store=_Store())
        token = _bind_context()
        try:
            request = OperationRequestFactory.create(
                capability=SANDBOX_CAPABILITY,
                op=SANDBOX_EXECUTE_OPERATION,
                arguments={"command": "echo no", "snapshot": _plan().model_dump()},
            )
            disposition = await _gateway().invoke(request, adapter)
        finally:
            OperationContext.unbind(token)

        assert disposition.outcome is OperationOutcome.FAILED
        assert runner.calls == []
