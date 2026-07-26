"""Lifecycle tests: create/teardown, TTL/leak detection, event emission."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from agent_runtime.capabilities.sandbox._file_records import SandboxFileRecordError
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    _utcnow,
)
from agent_runtime.capabilities.sandbox.ports import SandboxEvent
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.cleanup_store import FileSandboxCleanupStore
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
    SandboxEventName,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    RawSnapshotEntry,
    WorkspaceManifestBuilder,
)
from tests.unit.agent_runtime.capabilities.sandbox.contracts_helpers import (  # noqa: F401
    active_config,
)
from tests.unit.agent_runtime.capabilities.sandbox.fakes import (
    FailingTerminateProvider,
    FakeSandboxProvider,
    make_request,
)
from runtime_worker.sandbox_composition import FileSandboxRecoveryReaper


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[SandboxEvent] = []

    def emit(self, event: SandboxEvent) -> None:
        self.events.append(event)


def _service(provider=None, sink=None):
    config = active_config()
    provider = provider or FakeSandboxProvider()
    registry = SandboxProviderRegistry.from_config(
        config,
        overrides={config.provider: provider},  # type: ignore[dict-item]
    )
    return (
        RemoteExecutionService(
            registry=registry,
            config=config,
            session_store=InMemorySandboxSessionStore(),
            event_sink=sink,
        ),
        provider,
    )


class _FailingSessionStore:
    async def upsert(self, _session) -> None:
        raise OSError("simulated disk failure")

    async def get(self, _session_id):
        return None

    async def list_non_terminal(self):
        return ()

    async def delete(self, _session_id) -> None:
        return None


class _FailOnceTerminateProvider(FakeSandboxProvider):
    """Fails teardown once, then lets a restarted reaper drain the duty."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_terminate = True

    async def terminate(self, provider_session_ref: str) -> None:
        self.terminated_refs.append(provider_session_ref)
        if self.fail_terminate:
            raise OSError("simulated teardown outage")
        session = self._by_ref.get(provider_session_ref)  # noqa: SLF001 - fake state
        if session is not None:
            self._by_ref[provider_session_ref] = session.with_state("deleted")  # noqa: SLF001 - fake state


async def test_create_persists_cleanup_duty_before_session_projection(
    tmp_path: Path,
) -> None:
    """A post-create session-write failure leaves durable provider teardown data."""

    config = active_config()
    provider = FakeSandboxProvider()
    registry = SandboxProviderRegistry.from_config(
        config,
        overrides={config.provider: provider},  # type: ignore[dict-item]
    )
    cleanup = FileSandboxCleanupStore(layout=FileStoreLayout(tmp_path))
    service = RemoteExecutionService(
        registry=registry,
        config=config,
        session_store=_FailingSessionStore(),  # type: ignore[arg-type]
        cleanup_store=cleanup,
    )

    with pytest.raises(SandboxError, match="could not be recorded safely"):
        await service.create(make_request())

    duty = await cleanup.get("sandbox:run-1")
    assert duty is not None
    assert duty.run_id == "run-1"
    assert duty.provider_session_ref == "fake-idem-1"
    assert duty.state == "cleanup_pending"
    # The durable duty is intentional: provider termination was not attempted
    # merely because the session index write failed.
    assert provider.terminated_refs == []


async def test_primary_cleanup_write_and_immediate_teardown_failure_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider ref stays recoverable when both immediate steps fail.

    The primary cleanup category is faulted after provider creation.  The
    independent recovery journal must be durable before the service attempts
    teardown, and a freshly composed worker must later drain that journal.
    """

    config = active_config()
    provider = _FailOnceTerminateProvider()
    registry = SandboxProviderRegistry.from_config(
        config,
        overrides={config.provider: provider},  # type: ignore[dict-item]
    )
    store = FileRuntimeApiStore(tmp_path / "agent-data")
    cleanup = FileSandboxCleanupStore(layout=store.layout)

    def fail_primary_write(*_args, **_kwargs) -> None:
        raise SandboxFileRecordError("simulated primary cleanup persistence failure")

    monkeypatch.setattr(cleanup._records, "write", fail_primary_write)  # noqa: SLF001 - fault injection
    service = RemoteExecutionService(
        registry=registry,
        config=config,
        session_store=InMemorySandboxSessionStore(),
        cleanup_store=cleanup,
    )

    with pytest.raises(SandboxError) as excinfo:
        await service.create(make_request())
    assert excinfo.value.code is SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE
    duty = await cleanup.get("sandbox:run-1")
    assert duty is not None
    assert duty.provider_session_ref == "fake-idem-1"
    assert duty.state == "cleanup_pending"

    provider.fail_terminate = False
    restarted = FileSandboxRecoveryReaper.compose(
        file_store=FileRuntimeApiStore(tmp_path / "agent-data"),
        env={
            "ENTERPRISE_DEPLOYMENT_PROFILE": "single_user_desktop",
            "RUNTIME_ENABLE_REMOTE_SANDBOX": "true",
            "RUNTIME_SANDBOX_PROVIDER": "langsmith",
            "RUNTIME_SANDBOX_REGION": "test-region",
        },
        provider_overrides={config.provider: provider},  # type: ignore[dict-item]
    )
    assert restarted is not None
    assert await restarted.run_once() == ("sandbox:run-1",)
    recovered = await FileSandboxCleanupStore(
        layout=FileStoreLayout(tmp_path / "agent-data")
    ).get("sandbox:run-1")
    assert recovered is not None and recovered.state == "cleaned"
    assert provider.terminated_refs == ["fake-idem-1", "fake-idem-1"]


class TestCreateTeardown:
    async def test_create_emits_events_and_persists_session(self) -> None:
        sink = _RecordingSink()
        service, _ = _service(sink=sink)
        active = await service.create(make_request())
        assert active.session.session_id == "run-1"
        assert active.backend.id == "fake-idem-1"
        names = [e.name for e in sink.events]
        assert SandboxEventName.PROVISION_STARTED in names
        assert SandboxEventName.PROVISIONED in names
        provisioned = next(
            e for e in sink.events if e.name == SandboxEventName.PROVISIONED
        )
        assert provisioned.provider == "langsmith"
        assert provisioned.provider_session_ref == "fake-idem-1"

    async def test_execute_through_protocol(self) -> None:
        service, _ = _service()
        active = await service.create(make_request())
        response = active.backend.execute("echo:hello")
        assert response.output == "hello"
        assert response.exit_code == 0

    async def test_teardown_is_idempotent(self) -> None:
        service, provider = _service()
        await service.create(make_request())
        first = await service.teardown("run-1")
        second = await service.teardown("run-1")
        assert first is not None and first.cleanup_state == "deleted"
        assert second is not None and second.cleanup_state == "deleted"
        # Terminate called exactly once (second call short-circuits on deleted).
        assert provider.terminated_refs == ["fake-idem-1"]

    async def test_session_scope_tears_down_on_error(self) -> None:
        service, provider = _service()
        try:
            async with service.session_scope(make_request()) as active:
                assert active.session.cleanup_state == "active"
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert provider.terminated_refs == ["fake-idem-1"]

    async def test_failing_terminate_marks_cleanup_pending(self) -> None:
        service, _ = _service(provider=FailingTerminateProvider())
        await service.create(make_request())
        result = await service.teardown("run-1")
        assert result is not None
        assert result.cleanup_state == "cleanup_pending"

    async def test_provider_never_receives_workspace_grant_or_host_path(self) -> None:
        service, provider = _service()
        from agent_runtime.capabilities.sandbox.contracts import ArtifactRef

        manifest = WorkspaceManifestBuilder.build(
            workspace_id="/Users/alice/Finance",
            root_grant_id="grant-private-do-not-send",
            raw_entries=[
                RawSnapshotEntry(
                    path="report.csv",
                    sha256="a" * 64,
                    size_bytes=3,
                    payload_ref=ArtifactRef(
                        artifact_id="artifact_1", sha256="a" * 64, size_bytes=3
                    ),
                )
            ],
            limits=active_config().resolve_limits(),
        )
        request = make_request()
        request = request.model_copy(
            update={
                "snapshot": WorkspaceManifestBuilder.to_sandbox_snapshot(
                    manifest, snapshot_id="snapshot:run-1"
                )
            }
        )
        await service.create(request)
        received = provider.create_requests[-1].model_dump_json()
        assert "/Users/alice" not in received
        assert "grant-private" not in received
        assert "root_grant_id" not in received


class TestLeakDetection:
    async def test_detect_and_reap_expired(self) -> None:
        service, provider = _service()
        await service.create(make_request())
        future = _utcnow() + timedelta(hours=1)
        leaked = await service.detect_leaks(now=future)
        assert [s.session_id for s in leaked] == ["run-1"]
        swept = await service.reap(now=future)
        assert swept == ("run-1",)
        assert provider.terminated_refs == ["fake-idem-1"]
        # Nothing left to reap.
        assert await service.reap(now=future) == ()

    async def test_active_session_not_leaked(self) -> None:
        service, _ = _service()
        await service.create(make_request())
        assert await service.detect_leaks(now=_utcnow()) == ()


class TestCreateFailure:
    async def test_egress_request_fails_closed(self) -> None:
        service, _ = _service()
        with pytest.raises(SandboxError):
            await service.create(make_request(egress_mode="allowlist"))
