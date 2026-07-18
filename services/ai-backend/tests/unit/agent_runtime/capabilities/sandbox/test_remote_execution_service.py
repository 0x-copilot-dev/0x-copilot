"""Lifecycle tests: create/teardown, TTL/leak detection, event emission."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_runtime.capabilities.sandbox.contracts import SandboxError, _utcnow
from agent_runtime.capabilities.sandbox.ports import SandboxEvent
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
    SandboxEventName,
)
from tests.unit.agent_runtime.capabilities.sandbox.contracts_helpers import (  # noqa: F401
    active_config,
)
from tests.unit.agent_runtime.capabilities.sandbox.fakes import (
    FailingTerminateProvider,
    FakeSandboxProvider,
    make_request,
)


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
