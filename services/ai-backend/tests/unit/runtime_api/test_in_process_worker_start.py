"""The in-process worker starts on execution *topology*, not the store backend.

Regression guard for the AC2b cutover: the old guard keyed on
``settings.store.backend in {in_memory, in_memory_async}``, which silently
blocked the desktop's only run executor for the durable ``file``/``postgres``
backends. The worker now starts for single-process deployments — in-memory
dev/test and the ``single_user_desktop`` app — and stays off for multi-process
server profiles (which run a dedicated ``runtime_worker`` and would otherwise
double-claim).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.sse.event_bus import InMemoryEventBus


class InProcessWorkerStartMixin:
    """Build a minimal fake ``app`` and drive ``start_in_process_worker``."""

    @staticmethod
    def _settings(backend: str, *, start_worker: bool = True) -> RuntimeSettings:
        environ = {
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_STORE_BACKEND": backend,
            "RUNTIME_START_IN_PROCESS_WORKER": "true" if start_worker else "false",
        }
        return RuntimeSettings.load(environ=environ)

    @staticmethod
    def _app(
        *,
        settings: RuntimeSettings,
        profile_name: str | None,
    ) -> SimpleNamespace:
        # The gate only reads ``deployment.name``; a light stand-in is enough.
        deployment = (
            None if profile_name is None else SimpleNamespace(name=profile_name)
        )
        # Construction uses app.state.runtime_ports (any real ports work); the
        # gate decision reads settings.store.backend, so in-memory ports are a
        # valid stand-in for the "desktop + postgres backend" accept case too.
        ports = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
        return SimpleNamespace(
            state=SimpleNamespace(
                runtime_settings=settings,
                deployment=deployment,
                runtime_ports=ports,
                runtime_event_bus=InMemoryEventBus(),
                mcp_discovery_cache=None,
                runtime_user_policies_resolver=None,
            )
        )

    async def _start_and_probe(self, app: SimpleNamespace) -> bool:
        """Run the gate; return whether a worker task was created, then clean up."""
        await RuntimeApiAppFactory.start_in_process_worker(app)
        task = getattr(app.state, "runtime_in_process_worker_task", None)
        started = task is not None
        if task is not None:
            task.cancel()
            with pytest.raises((asyncio.CancelledError,)):
                await task
        return started


class TestInProcessWorkerStarts(InProcessWorkerStartMixin):
    async def test_starts_for_desktop_in_memory(self) -> None:
        app = self._app(
            settings=self._settings("in_memory"),
            profile_name="single_user_desktop",
        )
        assert await self._start_and_probe(app) is True

    async def test_starts_for_desktop_postgres_backend(self) -> None:
        # The durable desktop store the old guard wrongly excluded.
        app = self._app(
            settings=self._settings("postgres"),
            profile_name="single_user_desktop",
        )
        assert await self._start_and_probe(app) is True

    async def test_starts_for_desktop_file_backend(self) -> None:
        # The file store — the AC2b default — must get a run executor.
        app = self._app(
            settings=self._settings("file"),
            profile_name="single_user_desktop",
        )
        assert await self._start_and_probe(app) is True

    async def test_starts_for_dev_saas_in_memory(self) -> None:
        # `make dev` resolves to saas_multi_tenant + in_memory; the in-memory
        # clause must keep starting the worker there.
        app = self._app(
            settings=self._settings("in_memory"),
            profile_name="saas_multi_tenant",
        )
        assert await self._start_and_probe(app) is True


class TestInProcessWorkerDoesNotStart(InProcessWorkerStartMixin):
    @pytest.mark.parametrize(
        "profile_name",
        ["saas_multi_tenant", "single_tenant_managed", "single_tenant_self_hosted"],
    )
    async def test_never_starts_for_server_profiles_on_postgres(
        self, profile_name: str
    ) -> None:
        # Server profiles run a dedicated worker process; an in-process worker
        # here would double-claim queued runs.
        app = self._app(
            settings=self._settings("postgres"),
            profile_name=profile_name,
        )
        assert await self._start_and_probe(app) is False

    async def test_does_not_start_when_flag_disabled(self) -> None:
        app = self._app(
            settings=self._settings("file", start_worker=False),
            profile_name="single_user_desktop",
        )
        assert await self._start_and_probe(app) is False

    async def test_does_not_start_without_deployment(self) -> None:
        app = self._app(
            settings=self._settings("postgres"),
            profile_name=None,
        )
        assert await self._start_and_probe(app) is False
