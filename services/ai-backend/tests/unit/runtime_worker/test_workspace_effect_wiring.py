"""Composition and no-fallthrough guards for C3 enforce mode."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.workspace.contracts import (
    WorkspaceBaseEntry,
    WorkspaceBaseMatch,
)
from agent_runtime.capabilities.workspace.deep_backend import (
    WorkspaceGatewayBackend,
    WorkspaceTombstoneBackend,
)
from agent_runtime.capabilities.workspace.effects import WorkspaceGrantBinding
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.execution.deep_agent_builder import (
    WORKSPACE_STAGED_WRITE_GUIDANCE,
)
from agent_runtime.execution.factory import (
    _composed_deep_backend,
    _instructions_with_workspace,
    _workspace_write_permissions,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.workspace_overlay_store import (
    InMemoryWorkspaceOverlayStore,
)
from runtime_api.schemas import RunRecord, RuntimeRunCommand
from runtime_worker.handlers.approval import RuntimeApprovalHandler
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.workspace_effect_storage import (
    InMemoryWorkspaceHostSessionRegistry,
    WorkspaceHostSession,
)


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "SURFACES_V2": "true",
            "OPERATION_GATEWAY_MODE": OperationGatewayMode.ENFORCE.value,
            "WORKSPACE_EFFECT_MODE": OperationGatewayMode.ENFORCE.value,
        }
    )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-c3",
        org_id="org-c3",
        roles={"employee"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5.4-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run-c3",
        trace_id="trace-c3",
    )


def _run() -> RunRecord:
    context = _context()
    return RunRecord(
        run_id="run-c3",
        conversation_id="conv-c3",
        org_id="org-c3",
        user_id="user-c3",
        user_message_id="msg-c3",
        trace_id="trace-c3",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=context,
    )


def _command() -> RuntimeRunCommand:
    context = _context()
    return RuntimeRunCommand(
        run_id=context.run_id or "run-c3",
        conversation_id="conv-c3",
        org_id=context.org_id,
        user_id=context.user_id,
        trace_id=context.trace_id,
        runtime_context=context,
    )


def _scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org-c3",
        user_id="user-c3",
        conversation_id="conv-c3",
        run_id="run-c3",
        owner_ref="principal://users/user-c3",
    )


class EmptyReadBase:
    async def stat(self, _virtual_path: str) -> WorkspaceBaseEntry | None:
        return None

    async def read(
        self,
        _virtual_path: str,
        *,
        start: int | None = None,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        del start, end

        async def _stream() -> AsyncIterator[bytes]:
            if False:  # pragma: no cover - typed empty async iterator.
                yield b""

        return _stream()

    async def list(self, _virtual_path: str) -> Sequence[WorkspaceBaseEntry]:
        return ()

    async def glob(self, _pattern: str) -> Sequence[WorkspaceBaseEntry]:
        return ()

    async def grep(
        self, _query: str, _paths: Sequence[str] | None = None
    ) -> Sequence[WorkspaceBaseMatch]:
        return ()


class UnusedAuthority:
    pass


def _handler(
    *,
    sessions: InMemoryWorkspaceHostSessionRegistry | None,
) -> tuple[RuntimeRunHandler, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    publication = InMemoryArtifactPublicationCoordinator()
    return (
        RuntimeRunHandler(
            persistence=store,
            event_store=store,
            queue=store,
            settings=_settings(),
            artifact_blob_store=InMemoryArtifactBlobStore(publication),
            artifact_reference_store=InMemoryArtifactReferenceStore(publication),
            workspace_host_sessions=sessions,
            workspace_overlay_store=InMemoryWorkspaceOverlayStore(),
        ),
        store,
    )


async def test_enforce_without_host_authority_mounts_tombstone() -> None:
    handler, _store = _handler(sessions=InMemoryWorkspaceHostSessionRegistry())
    run = _run()
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None

    backend = await handler._workspace_backend_for_run(
        _command(),
        run=run,
        mcp_gateway_services=services,
    )

    assert isinstance(backend, WorkspaceTombstoneBackend)
    result = await backend.awrite("finance/report.csv", "x")
    assert result.error is not None
    assert "no local file was changed" in result.error


async def test_enforce_stale_legacy_approval_resumes_into_tombstone() -> None:
    store = InMemoryRuntimeApiStore()
    handler = RuntimeApprovalHandler(
        persistence=store,
        event_store=store,
        settings=_settings(),
    )

    backend = await handler._workspace_backend_for_resume(_run())

    assert isinstance(backend, WorkspaceTombstoneBackend)
    result = await backend.awrite("finance/report.csv", "x")
    assert result.error is not None
    assert "no local file was changed" in result.error


async def test_enforce_backend_reuses_real_a4_stager_and_has_no_host_writer() -> None:
    sessions = InMemoryWorkspaceHostSessionRegistry()
    sessions.bind(
        scope=_scope(),
        session=WorkspaceHostSession(
            grants=(
                WorkspaceGrantBinding(
                    mount_name="finance",
                    grant_id="grant-finance",
                    mount_label="Finance",
                    mode="read_write",
                ),
            ),
            base_read=EmptyReadBase(),
            host_session_ref=f"whs_{'x' * 43}",
            authority=UnusedAuthority(),  # type: ignore[arg-type]
        ),
    )
    handler, _store = _handler(sessions=sessions)
    run = _run()
    services = handler._build_mcp_operation_gateway_services(run)
    assert services is not None

    backend = await handler._workspace_backend_for_run(
        _command(),
        run=run,
        mcp_gateway_services=services,
    )

    assert isinstance(backend, WorkspaceGatewayBackend)
    assert backend._adapter._services.stager is services.stager
    assert not hasattr(backend, "client")
    assert not hasattr(backend, "apply")
    assert not hasattr(backend._adapter, "apply")


async def test_tombstone_route_cannot_fall_through_to_state_backend() -> None:
    composite = _composed_deep_backend(
        None,
        workspace_backend=WorkspaceTombstoneBackend(),
    )
    assert composite is not None

    result = await composite.awrite("/workspace/finance/report.csv", "x")

    assert result.error is not None
    assert "no local file was changed" in result.error
    assert "/workspace/" in composite.routes


def test_staged_workspace_never_installs_generic_filesystem_interrupt() -> None:
    assert _workspace_write_permissions(True, effect_staged=True) == ()
    assert _workspace_write_permissions(False, effect_staged=True) == ()
    assert _workspace_write_permissions(True) == ()

    instructions = _instructions_with_workspace(
        instructions="BASE",
        workspace_active=True,
        workspace_writable=True,
        workspace_effect_staging=True,
    )
    assert WORKSPACE_STAGED_WRITE_GUIDANCE in instructions
    assert "do NOT immediately modify the host file" in instructions
