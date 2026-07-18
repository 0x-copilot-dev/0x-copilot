"""The model-facing ``run_in_sandbox`` tool (AC7 execute-only wiring).

A thin LangChain ``StructuredTool`` over :class:`RemoteExecutionService`. It runs
**one shell command per invocation** in a freshly provisioned remote sandbox and
tears the session down before returning — the whole call is wrapped in
``RemoteExecutionService.session_scope`` so provision → execute → guaranteed
teardown converge even on error or cancellation.

Why a dedicated tool instead of the deepagents composite default backend:
deepagents' ``execute`` tool is served by ``CompositeBackend.default``, and
``PolicyEnforcedSandboxBackend`` implements the **full** filesystem surface. If
it were the composite default, every unrouted path — ``/memories/``,
``/skills/``, scratch — would relocate into the ephemeral remote sandbox and be
destroyed at teardown, breaking memory/skill persistence. This tool keeps the
StateBackend/``/memories/``/``/skills/`` local and untouched: ONLY the explicit
command the model runs reaches the sandbox.

Like every other model tool, this one is registered into the normal tool set and
therefore flows through the runtime's ordinary tool-policy / approval / budget
middleware — it is not privileged. The trusted run identity comes from an
injected provider, never from the model. Built only by the registration wiring
when ``RUNTIME_ENABLE_REMOTE_SANDBOX`` + ``single_user_desktop`` are satisfied;
when the capability is off this module is never imported at runtime.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxCreateRequest,
    SandboxEgressPolicy,
    SandboxError,
    WorkspaceTransferManifest,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
)

TOOL_NAME = "run_in_sandbox"
TOOL_DESCRIPTION = (
    "Run a single shell command in an isolated, network-restricted remote "
    "sandbox and return its combined output and exit code. A fresh sandbox is "
    "provisioned for each call and destroyed immediately after, so no state "
    "persists between calls and nothing is shared with the local workspace. Use "
    "it for one-shot computation, scripts, or CLI tools that need a real shell; "
    "for reading or writing the user's files use the filesystem tools instead."
)

#: Empty-manifest sentinel: an execute-only run transfers no workspace bytes, so
#: the snapshot manifest is empty and its content hash is the all-zero digest.
_EMPTY_MANIFEST_SHA256 = "0" * 64


@dataclass(frozen=True)
class SandboxRunIdentity:
    """Trusted run identity resolved from context for one tool invocation."""

    run_id: str
    org_id: str | None = None
    user_id: str | None = None


#: Supplies the current run identity. Wired to the run context at registration;
#: the model never influences it.
SandboxRunIdentityProvider = Callable[[], SandboxRunIdentity]


class RunInSandboxInput(BaseModel):
    """Model-facing schema for :data:`TOOL_NAME` — the command only."""

    command: str = Field(
        min_length=1,
        description="Shell command to run in the isolated sandbox.",
    )


class SandboxExecuteToolFactory:
    """Builds the ``run_in_sandbox`` StructuredTool bound to a service + identity."""

    @classmethod
    def build(
        cls,
        *,
        service: RemoteExecutionService,
        identity_provider: SandboxRunIdentityProvider,
        config: RemoteSandboxConfig,
    ) -> StructuredTool:
        """Return a ``run_in_sandbox`` StructuredTool over a live service."""

        limit_profile = config.limit_profile

        async def _run_in_sandbox(command: str) -> str:
            identity = identity_provider()
            request = cls._create_request(identity.run_id, limit_profile)
            try:
                async with service.session_scope(request) as active:
                    response = await active.backend.aexecute(command)
            except SandboxError as exc:
                return json.dumps(
                    {
                        "status": "failed",
                        "error_code": exc.code.value,
                        "message": exc.message,
                    }
                )
            return json.dumps(
                {
                    "status": "completed",
                    "output": getattr(response, "output", ""),
                    "exit_code": getattr(response, "exit_code", None),
                    "truncated": bool(getattr(response, "truncated", False)),
                }
            )

        return StructuredTool.from_function(
            coroutine=_run_in_sandbox,
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            args_schema=RunInSandboxInput,
        )

    @staticmethod
    def _create_request(run_id: str, limit_profile: str) -> SandboxCreateRequest:
        """Build the minimal execute-only provisioning envelope for one call.

        No workspace bytes are transferred (empty manifest), egress stays at the
        default ``deny_all``, and the ``owner_tag`` / ``idempotency_key`` /
        ``approval_id`` are per-call run-derived ids — none of it is
        model-influenced.
        """

        manifest = WorkspaceTransferManifest(
            workspace_id=f"sbx-{run_id}",
            root_grant_id=f"sbx-{run_id}",
            entries=(),
            total_bytes=0,
            manifest_sha256=_EMPTY_MANIFEST_SHA256,
        )
        return SandboxCreateRequest(
            run_id=run_id,
            workspace_snapshot=manifest,
            egress=SandboxEgressPolicy(),
            secret_refs=(),
            limit_profile=limit_profile,
            approval_id=uuid4().hex,
            owner_tag=run_id,
            idempotency_key=uuid4().hex,
        )


__all__ = (
    "RunInSandboxInput",
    "SandboxExecuteToolFactory",
    "SandboxRunIdentity",
    "SandboxRunIdentityProvider",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
)
