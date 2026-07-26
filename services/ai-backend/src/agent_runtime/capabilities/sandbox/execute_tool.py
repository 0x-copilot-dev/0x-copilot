"""The model-facing ``run_in_sandbox`` operation-gateway tool.

This is deliberately only a LangChain boundary.  It records the command and a
trusted reference-only snapshot as canonical operation arguments, then invokes
the universal operation gateway.  It does not import or call a provider
lifecycle service, acquire a provider session, or touch a local file.
The worker composition root will supply the lifecycle-runner and file-store
ports once their durable implementations are wired.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent_runtime.capabilities.operations.context import OperationRequestFactory
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.sandbox.operation_adapter import (
    SANDBOX_CAPABILITY,
    SANDBOX_EXECUTE_OPERATION,
    SandboxOperationAdapter,
)
from agent_runtime.capabilities.sandbox.contracts import SandboxError
from agent_runtime.capabilities.sandbox.snapshot import SandboxSnapshotPlanProvider
from agent_runtime.surfaces_v2.ledger_models import OperationOutcome

TOOL_NAME = SANDBOX_EXECUTE_OPERATION
TOOL_DESCRIPTION = (
    "Run a single shell command in an isolated remote sandbox using only a "
    "trusted immutable snapshot. The sandbox has no local workspace mount, "
    "no user credentials, and deny-all network egress. Results remain behind "
    "operation references; local files are unchanged unless a later reviewed "
    "workspace patch is applied."
)


@dataclass(frozen=True)
class SandboxRunIdentity:
    """Trusted run identity resolved from context for one tool invocation."""

    run_id: str
    org_id: str | None = None
    user_id: str | None = None


SandboxRunIdentityProvider = Callable[[], SandboxRunIdentity]


class RunInSandboxInput(BaseModel):
    """Model-facing schema: command only, never paths, refs, or provider data."""

    command: str = Field(
        min_length=1,
        description="Shell command to run in the isolated sandbox.",
    )


class SandboxExecuteToolFactory:
    """Build a gateway-routed sandbox tool only when the provider is honest-ready."""

    @classmethod
    def build(
        cls,
        *,
        gateway: OperationGateway,
        adapter: SandboxOperationAdapter,
        identity_provider: SandboxRunIdentityProvider,
        snapshot_provider: SandboxSnapshotPlanProvider,
    ) -> StructuredTool | None:
        """Return ``None`` when verified execution is unavailable.

        A configuration flag or a provider object is not sufficient to expose
        a model tool.  A stale already-built tool also rechecks availability
        before it writes canonical arguments, so it cannot dispatch a command
        after the provider posture has become unavailable.
        """

        if not adapter.availability.available:
            return None

        async def _run_in_sandbox(command: str) -> str:
            availability = adapter.availability
            if not availability.available:
                return json.dumps(
                    {
                        "status": "unavailable",
                        "summary": (
                            "Sandbox execution is unavailable; no command was run."
                        ),
                        "reason": availability.reason,
                    }
                )
            identity = identity_provider()
            try:
                plan = await snapshot_provider.snapshot_for(
                    run_id=identity.run_id,
                    org_id=identity.org_id,
                    user_id=identity.user_id,
                )
            except SandboxError:
                # A missing retained C1 version, failed A2 authorization, or
                # invalid immutable source must be honest to the model without
                # leaking storage/provider detail.  No gateway operation or
                # provider action has happened at this point.
                return json.dumps(
                    {
                        "status": "failed",
                        "summary": (
                            "An authorized immutable sandbox snapshot is unavailable; "
                            "no command was run."
                        ),
                    }
                )
            request = OperationRequestFactory.create(
                capability=SANDBOX_CAPABILITY,
                op=SANDBOX_EXECUTE_OPERATION,
                arguments={
                    "command": command,
                    "snapshot": plan.model_dump(mode="json"),
                },
            )
            disposition = await gateway.invoke(request, adapter)
            output: dict[str, object] = {
                "status": disposition.outcome.value,
                "operation_id": disposition.operation_id,
                "summary": disposition.agent_summary,
            }
            if disposition.outcome is OperationOutcome.SUCCEEDED:
                result = adapter.result_for(disposition.operation_id)
                if result is None:
                    # A successful operation without its immutable result ref
                    # cannot be reported as complete to the model.
                    return json.dumps(
                        {
                            "status": "failed",
                            "operation_id": disposition.operation_id,
                            "summary": "Sandbox result storage is unavailable.",
                        }
                    )
                output.update(
                    {
                        "status": "completed",
                        "summary": result.safe_summary,
                        "result_ref": result.result_ref,
                    }
                )
                if result.artifacts:
                    output["artifact_refs"] = [
                        artifact.artifact_ref for artifact in result.artifacts
                    ]
                if result.patch is not None:
                    output["patch_ref"] = result.patch.patch_ref
            elif disposition.outcome is not OperationOutcome.BLOCKED:
                output["status"] = "failed"
            return json.dumps(output)

        return StructuredTool.from_function(
            coroutine=_run_in_sandbox,
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            args_schema=RunInSandboxInput,
        )


__all__ = (
    "RunInSandboxInput",
    "SandboxExecuteToolFactory",
    "SandboxRunIdentity",
    "SandboxRunIdentityProvider",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
)
