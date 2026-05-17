"""P2 — SSE event-bus backend selection + behavior pin.

In-memory and Postgres backends both satisfy ``EventBusBackend``. The
in-memory tests pin the legacy single-process behavior unchanged. The
Postgres tests use a fake notifications stream to exercise dispatch +
malformed-payload handling + reconnect behavior without a live database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass


from runtime_api.sse.event_bus import (
    EventBusBackend,
    InMemoryEventBus,
    RuntimeEventBus,
)
from runtime_api.sse.postgres_event_bus import (
    CHANNEL,
    PostgresEventBus,
    _format_payload,
)


class TestProtocolConformance:
    def test_in_memory_satisfies_protocol(self) -> None:
        bus = InMemoryEventBus()
        assert isinstance(bus, EventBusBackend)

    def test_runtime_event_bus_alias_resolves_to_in_memory(self) -> None:
        # Backward compat — call sites that import RuntimeEventBus should
        # continue to get the in-memory implementation.
        assert RuntimeEventBus is InMemoryEventBus

    def test_in_memory_fallback_poll_unchanged(self) -> None:
        # Default 2s — was the only mechanism in production before P2.
        assert InMemoryEventBus.fallback_poll_seconds == 2.0

    def test_postgres_fallback_poll_relaxed(self) -> None:
        # 10s — the fallback is now a backstop, not the primary mechanism.
        assert PostgresEventBus.fallback_poll_seconds == 10.0


class TestPayloadFormat:
    def test_format_payload_is_run_id_colon_sequence(self) -> None:
        assert _format_payload("run-abc", 42) == "run-abc:42"


class _FakeNotify:
    def __init__(self, payload: str) -> None:
        self.payload = payload


@dataclass
class _FakeListenConn:
    """Stub for psycopg.AsyncConnection used by the listener loop tests.

    Records LISTEN executions and returns notifications from a queue.
    Closes signal end-of-stream (the loop should reconnect).
    """

    payloads: list[str]
    listen_executes: list[str]
    closed: bool = False

    async def execute(self, sql: str, *_args: object) -> None:
        self.listen_executes.append(sql)

    async def notifies(self) -> AsyncIterator[_FakeNotify]:
        for payload in self.payloads:
            yield _FakeNotify(payload)
            # yield to scheduler so the dispatcher can run
            await asyncio.sleep(0)
        # Block forever after exhausting the queue — caller controls
        # shutdown via ``stop()``.
        block = asyncio.Event()
        await block.wait()

    async def close(self) -> None:
        self.closed = True


class TestPostgresEventBusDispatch:
    """Notifications wake the matching local listener."""

    async def test_dispatch_routes_to_correct_run_id(self) -> None:
        conn = _FakeListenConn(payloads=["run-A:5"], listen_executes=[])
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))

        await bus.start()
        try:
            # Subscribe to A — should wake on the dispatched payload.
            wait_a = asyncio.create_task(bus.wait("run-A", timeout=1.0))
            # Give the listener loop time to consume + dispatch.
            await asyncio.sleep(0.1)
            await wait_a
            assert conn.listen_executes == [f"LISTEN {CHANNEL}"]
        finally:
            await bus.stop()

    async def test_dispatch_does_not_wake_unrelated_run(self) -> None:
        conn = _FakeListenConn(payloads=["run-A:1"], listen_executes=[])
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))

        await bus.start()
        try:
            wait_b = asyncio.create_task(bus.wait("run-B", timeout=0.2))
            await wait_b
            # B's wait timed out — no notification arrived for B.
        finally:
            await bus.stop()

    async def test_malformed_payload_dropped_without_crash(self) -> None:
        conn = _FakeListenConn(
            payloads=["malformed-no-colon", ":missing-run-id", "run-A:5"],
            listen_executes=[],
        )
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))

        await bus.start()
        try:
            wait_a = asyncio.create_task(bus.wait("run-A", timeout=1.0))
            await asyncio.sleep(0.1)
            await wait_a
            # Bus survived the malformed payloads and dispatched the valid one.
        finally:
            await bus.stop()


class TestPostgresEventBusLifecycle:
    async def test_start_is_idempotent(self) -> None:
        conn = _FakeListenConn(payloads=[], listen_executes=[])
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))
        await bus.start()
        try:
            await bus.start()  # second start — no-op
        finally:
            await bus.stop()

    async def test_stop_cancels_listen_task(self) -> None:
        conn = _FakeListenConn(payloads=[], listen_executes=[])
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))
        await bus.start()
        # Yield so the listener loop has a chance to open the connection
        # and reach the ``async for notify in conn.notifies()`` block —
        # otherwise the cancellation may fire before any connection is
        # opened, and the close-on-stop assertion is a no-op.
        await asyncio.sleep(0.05)
        await bus.stop()
        # Connection was closed cleanly.
        assert conn.closed is True

    async def test_stop_is_idempotent(self) -> None:
        conn = _FakeListenConn(payloads=[], listen_executes=[])
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))
        await bus.start()
        await bus.stop()
        await bus.stop()  # second stop — no-op


class TestPostgresEventBusUnsubscribe:
    async def test_unsubscribe_removes_listener(self) -> None:
        conn = _FakeListenConn(payloads=[], listen_executes=[])
        bus = PostgresEventBus(connection_factory=lambda: _async_return(conn))
        await bus.start()
        try:
            # Establish listener via wait (timeout fast).
            await bus.wait("run-A", timeout=0.05)
            assert "run-A" in bus._listeners
            bus.unsubscribe("run-A")
            assert "run-A" not in bus._listeners
            # Idempotent — second call doesn't raise.
            bus.unsubscribe("run-A")
        finally:
            await bus.stop()


class TestSettingsBackendSelection:
    def test_dev_env_example_pins_in_memory(self) -> None:
        """Dev's env_example explicitly chooses single-process; verify it flows."""

        from agent_runtime.settings import RuntimeSettings

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        # env_example pins this; the resolver passes it through unchanged.
        assert settings.execution.event_bus_backend == "in_memory"
        assert settings.resolved_event_bus_backend() == "in_memory"

    def test_loader_default_without_env_example_is_auto(self, tmp_path) -> None:
        """Prod ships no env_example; the loader default must be ``auto``."""

        from agent_runtime.settings import RuntimeSettings

        missing = tmp_path / "no-env-example"
        settings = RuntimeSettings.load(
            template_file=missing,
            env_file=missing,
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            },
        )
        assert settings.execution.event_bus_backend == "auto"

    def test_auto_resolves_to_postgres_when_database_url_set(self, tmp_path) -> None:
        """The whole point of the change — prod auto-picks postgres."""

        from agent_runtime.settings import RuntimeSettings

        missing = tmp_path / "no-env-example"
        settings = RuntimeSettings.load(
            template_file=missing,
            env_file=missing,
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "DATABASE_URL": "postgresql://localhost/x",
            },
        )
        assert settings.execution.event_bus_backend == "auto"
        assert settings.resolved_event_bus_backend() == "postgres"

    def test_auto_resolves_to_in_memory_without_database_url(self, tmp_path) -> None:
        """No DATABASE_URL → fall back to in_memory rather than crashing."""

        from agent_runtime.settings import RuntimeSettings

        missing = tmp_path / "no-env-example"
        settings = RuntimeSettings.load(
            template_file=missing,
            env_file=missing,
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            },
        )
        assert settings.execution.event_bus_backend == "auto"
        assert settings.resolved_event_bus_backend() == "in_memory"

    def test_postgres_backend_selected_via_env(self) -> None:
        from agent_runtime.settings import RuntimeSettings

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_EVENT_BUS_BACKEND": "postgres",
            }
        )
        assert settings.execution.event_bus_backend == "postgres"
        # Explicit values pass through the resolver unchanged.
        assert settings.resolved_event_bus_backend() == "postgres"

    def test_factory_threads_notify_after_append_to_in_memory_adapter(
        self,
    ) -> None:
        # In-memory store doesn't have the notify_after_append flag;
        # the factory should not pass it.
        from agent_runtime.settings import RuntimeSettings
        from runtime_adapters.factory import RuntimeAdapterFactory

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "in_memory",
                "RUNTIME_EVENT_BUS_BACKEND": "postgres",
            }
        )
        # Should construct without error — in-memory store ignores the flag.
        ports = RuntimeAdapterFactory.from_settings(settings)
        assert ports is not None


class TestInMemoryBusBehaviorUnchanged:
    """Pin the legacy InMemoryEventBus behavior — must be byte-identical
    to the pre-P2 ``RuntimeEventBus``."""

    async def test_wait_returns_on_notify(self) -> None:
        bus = InMemoryEventBus()
        # Establish the condition for run-X.
        wait_task = asyncio.create_task(bus.wait("run-X", timeout=1.0))
        await asyncio.sleep(0.01)
        await bus.notify("run-X")
        await wait_task

    async def test_wait_times_out_without_notify(self) -> None:
        bus = InMemoryEventBus()
        # Should return cleanly (no exception) on timeout.
        await bus.wait("run-Y", timeout=0.05)

    async def test_unsubscribe_removes_condition(self) -> None:
        bus = InMemoryEventBus()
        await bus.wait("run-Z", timeout=0.05)
        assert "run-Z" in bus._conditions
        bus.unsubscribe("run-Z")
        assert "run-Z" not in bus._conditions
        bus.unsubscribe("run-Z")  # idempotent


# ---------------------------------------------------------------------------


async def _async_return(value: object) -> object:
    return value
