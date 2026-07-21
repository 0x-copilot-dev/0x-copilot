"""Run-executor observability: a no-worker state is a red light, not a hang.

The AC2b bug (the desktop file store started no run executor) was invisible
because its only symptom was a ~68s SSE hang. These tests guard the two signals
that now make that state explicit:

* ``RuntimeApiAppFactory.classify_run_executor`` reports ``running`` /
  ``external`` / ``absent`` from the live worker-task state, fail-closed (a
  dead task never reads ``running``).
* ``/readyz`` fails closed (503) when a single-process deployment has no live
  executor (``absent``); ``running`` and ``external`` are ready.
* ``/v1/health`` carries the state additively and STAYS 200 — the desktop and
  self-host liveness probes depend on that.
* ``start_in_process_worker`` logs every start/skip decision with its deciding
  signals, escalating to WARNING when a single-process deployment is left
  executor-less.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_runtime.deployment.profile import DeploymentProfileLoader
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import (
    RUN_EXECUTOR_ABSENT,
    RUN_EXECUTOR_EXTERNAL,
    RUN_EXECUTOR_RUNNING,
    RuntimeApiAppFactory,
)
from runtime_api.sse.event_bus import InMemoryEventBus


def _settings(backend: str, *, start_worker: bool = True) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_STORE_BACKEND": backend,
            "RUNTIME_START_IN_PROCESS_WORKER": "true" if start_worker else "false",
        }
    )


def _deployment(name: str):
    # A real resolved DeploymentProfile (the classifier only reads ``.name``).
    return DeploymentProfileLoader.load(env={"ENTERPRISE_DEPLOYMENT_PROFILE": name})


def _classify_app(*, task, settings: RuntimeSettings, deployment) -> SimpleNamespace:
    """Minimal ``app`` stand-in exposing exactly what the classifier reads."""

    return SimpleNamespace(
        state=SimpleNamespace(
            runtime_in_process_worker_task=task,
            runtime_settings=settings,
            deployment=deployment,
        )
    )


class TestClassifyRunExecutor:
    async def test_running_when_task_alive_on_desktop_file_store(self) -> None:
        # The AC2b topology done right: file store + a live in-process executor.
        task = asyncio.create_task(asyncio.sleep(3600))
        app = _classify_app(
            task=task,
            settings=_settings("file"),
            deployment=_deployment("single_user_desktop"),
        )
        try:
            assert (
                RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_RUNNING
            )
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def test_running_for_dev_in_memory(self) -> None:
        # `make dev` == saas_multi_tenant + in_memory: still single-process.
        task = asyncio.create_task(asyncio.sleep(3600))
        app = _classify_app(
            task=task,
            settings=_settings("in_memory"),
            deployment=_deployment("saas_multi_tenant"),
        )
        try:
            assert (
                RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_RUNNING
            )
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def test_absent_when_gate_skipped_on_desktop(self) -> None:
        # single_user_desktop is single-process; NO task means the only run
        # executor never started — the exact AC2b red-light state.
        app = _classify_app(
            task=None,
            settings=_settings("file"),
            deployment=_deployment("single_user_desktop"),
        )
        assert RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_ABSENT

    async def test_absent_when_dev_in_memory_has_no_task(self) -> None:
        app = _classify_app(
            task=None,
            settings=_settings("in_memory"),
            deployment=_deployment("saas_multi_tenant"),
        )
        assert RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_ABSENT

    async def test_external_for_server_profile_on_postgres(self) -> None:
        # Multi-process: a dedicated runtime_worker owns the executor, so this
        # API process is intentionally executor-less and still ready.
        app = _classify_app(
            task=None,
            settings=_settings("postgres"),
            deployment=_deployment("saas_multi_tenant"),
        )
        assert RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_EXTERNAL

    async def test_absent_when_task_dead_fail_closed(self) -> None:
        # A finished (crashed / exited) task must NEVER read running — the
        # dead-executor state degrades to the same red light as no task at all.
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        assert task.done()
        app = _classify_app(
            task=task,
            settings=_settings("file"),
            deployment=_deployment("single_user_desktop"),
        )
        assert RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_ABSENT

    async def test_dead_task_on_server_profile_reads_external(self) -> None:
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        app = _classify_app(
            task=task,
            settings=_settings("postgres"),
            deployment=_deployment("saas_multi_tenant"),
        )
        assert RuntimeApiAppFactory.classify_run_executor(app) == RUN_EXECUTOR_EXTERNAL


class TestRunExecutorReadinessChecker:
    def test_ok_false_when_absent(self) -> None:
        app = _classify_app(
            task=None,
            settings=_settings("file"),
            deployment=_deployment("single_user_desktop"),
        )
        result = RuntimeApiAppFactory._run_executor_readiness_checker(app)()
        assert result.name == "run_executor"
        assert result.ok is False
        assert result.detail == RUN_EXECUTOR_ABSENT

    def test_ok_true_when_external(self) -> None:
        app = _classify_app(
            task=None,
            settings=_settings("postgres"),
            deployment=_deployment("saas_multi_tenant"),
        )
        result = RuntimeApiAppFactory._run_executor_readiness_checker(app)()
        assert result.ok is True
        assert result.detail == RUN_EXECUTOR_EXTERNAL


def _build_app(*, backend: str, profile: str):
    ports = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
    return RuntimeApiAppFactory.create_app(
        ports=ports,
        settings=_settings(backend),
        deployment=_deployment(profile),
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
    )


class TestReadyzAndHealthEndpoints:
    """End-to-end over the wired app (plain TestClient: no lifespan → no worker,
    which is exactly the executor-less state we assert on)."""

    def test_readyz_503_but_health_200_when_absent_on_desktop(self) -> None:
        app = _build_app(backend="file", profile="single_user_desktop")
        client = TestClient(app)

        ready = client.get("/readyz")
        assert ready.status_code == 503
        body = ready.json()
        assert body["status"] == "not_ready"
        run_check = next(c for c in body["checks"] if c["name"] == "run_executor")
        assert run_check["ok"] is False
        assert run_check["detail"] == RUN_EXECUTOR_ABSENT

        # Liveness must stay 200 (desktop-runtime + self-host healthchecks
        # depend on it) while still exposing the absent state.
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["run_executor"] == RUN_EXECUTOR_ABSENT

    def test_running_when_worker_task_alive(self) -> None:
        app = _build_app(backend="file", profile="single_user_desktop")
        # Stand in for a live in-process worker task (classifier reads .done()).
        app.state.runtime_in_process_worker_task = SimpleNamespace(done=lambda: False)
        client = TestClient(app)

        assert client.get("/v1/health").json()["run_executor"] == RUN_EXECUTOR_RUNNING
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"

    def test_external_and_ready_for_server_profile(self) -> None:
        app = _build_app(backend="postgres", profile="saas_multi_tenant")
        client = TestClient(app)

        assert client.get("/v1/health").json()["run_executor"] == RUN_EXECUTOR_EXTERNAL
        assert client.get("/readyz").status_code == 200

    def test_health_preserves_existing_fields(self) -> None:
        app = _build_app(backend="file", profile="single_user_desktop")
        body = TestClient(app).get("/v1/health").json()
        # Backward-compatible: the pre-existing fields are byte-identical.
        assert body["service"] == "ai-backend"
        assert body["deployment_profile"] == "single_user_desktop"
        assert "feature_toggles_hash" in body
        assert body["run_executor"] == RUN_EXECUTOR_ABSENT


def _worker_app(*, settings: RuntimeSettings, profile: str | None) -> SimpleNamespace:
    """Fake app that ``start_in_process_worker`` can consume end-to-end."""

    deployment = None if profile is None else _deployment(profile)
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


def _decision_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "run_executor_decision"]


class TestStartDecisionLogging:
    async def test_logs_started_info_on_desktop(self, caplog) -> None:
        app = _worker_app(
            settings=_settings("file"),
            profile="single_user_desktop",
        )
        with caplog.at_level(logging.INFO):
            await RuntimeApiAppFactory.start_in_process_worker(app)
        task = app.state.runtime_in_process_worker_task
        try:
            records = _decision_records(caplog)
            assert len(records) == 1
            rec = records[0]
            assert rec.levelno == logging.INFO
            meta = rec.log_event["metadata"]
            assert meta["started"] is True
            assert meta["reason"] == "started"
            assert meta["run_executor"] == RUN_EXECUTOR_RUNNING
            assert meta["deployment"] == "single_user_desktop"
            assert meta["store_backend"] == "file"
            assert meta["start_in_process_worker"] is True
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def test_logs_absent_warning_when_flag_disabled_on_desktop(
        self, caplog
    ) -> None:
        # Desktop with the worker flag off is a misconfiguration: no dedicated
        # worker exists to pick up the slack, so this MUST be a red light.
        app = _worker_app(
            settings=_settings("file", start_worker=False),
            profile="single_user_desktop",
        )
        with caplog.at_level(logging.INFO):
            await RuntimeApiAppFactory.start_in_process_worker(app)
        assert getattr(app.state, "runtime_in_process_worker_task", None) is None
        records = _decision_records(caplog)
        assert len(records) == 1
        rec = records[0]
        assert rec.levelno == logging.WARNING
        meta = rec.log_event["metadata"]
        assert meta["started"] is False
        assert meta["reason"] == "start_in_process_worker_disabled"
        assert meta["run_executor"] == RUN_EXECUTOR_ABSENT
        assert meta["deployment"] == "single_user_desktop"
        assert meta["store_backend"] == "file"

    async def test_logs_external_info_for_server_profile(self, caplog) -> None:
        app = _worker_app(
            settings=_settings("postgres"),
            profile="saas_multi_tenant",
        )
        with caplog.at_level(logging.INFO):
            await RuntimeApiAppFactory.start_in_process_worker(app)
        assert getattr(app.state, "runtime_in_process_worker_task", None) is None
        records = _decision_records(caplog)
        assert len(records) == 1
        rec = records[0]
        assert rec.levelno == logging.INFO
        meta = rec.log_event["metadata"]
        assert meta["started"] is False
        assert meta["reason"] == "multi_process_topology"
        assert meta["run_executor"] == RUN_EXECUTOR_EXTERNAL
