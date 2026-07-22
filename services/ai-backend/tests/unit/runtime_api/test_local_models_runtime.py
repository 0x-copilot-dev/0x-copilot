"""Unit tests for OllamaRuntimeController + PRD-P8 §4.2 status derivation.

Every OS seam is injected — no real ``shutil.which``, no real filesystem, no
real fork. Covers binary detection (PATH + per-platform well-known paths),
the manage-off posture, detached spawn, spawn-failure typing, the bounded
start poll, all five derivation rows, and the Pydantic/TypeScript mirror
(PRD-P8 §9: the drift hole between the two shapes).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from runtime_api.local_models import (
    HfGgufResolver,
    LocalModelError,
    OllamaClient,
    OllamaRuntimeController,
)
from runtime_api.local_models.service import LocalModelService
from runtime_api.schemas.local_models import (
    LocalModelErrorKind,
    LocalModelPullEvent,
    LocalModelsStatus,
    LocalRuntimeState,
)

_MAC_APP_BINARY = "/Applications/Ollama.app/Contents/Resources/ollama"
_LINUX_BINARY = "/usr/local/bin/ollama"
_WINDOWS_BINARY = r"C:\Users\p\AppData\Local\Programs\Ollama\ollama.exe"


class RuntimeControllerMixin:
    """Builders for a fully-injected controller and its recorded side effects."""

    @staticmethod
    def _clock() -> dict[str, float]:
        return {"t": 0.0}

    @classmethod
    def _controller(
        cls,
        *,
        manage: bool = True,
        on_path: str | None = None,
        present: Sequence[str] = (),
        spawn_error: Exception | None = None,
        spawned: list[Sequence[str]] | None = None,
        platform: str = "darwin",
        environ: dict[str, str] | None = None,
        clock: dict[str, float] | None = None,
        interval_advance: float = 1.0,
    ) -> OllamaRuntimeController:
        ticker = clock if clock is not None else cls._clock()
        existing = set(present)

        def spawn(command: Sequence[str]) -> None:
            if spawn_error is not None:
                raise spawn_error
            if spawned is not None:
                spawned.append(tuple(command))

        async def sleep(_seconds: float) -> None:
            ticker["t"] += interval_advance

        return OllamaRuntimeController(
            manage=manage,
            which=lambda _name: on_path,
            exists=lambda path: path in existing,
            spawn=spawn,
            clock=lambda: ticker["t"],
            sleep=sleep,
            platform=platform,
            environ=environ if environ is not None else {},
        )

    @staticmethod
    def _probe(versions: list[str | None]):
        """A probe returning each queued value once, then repeating the last."""

        async def probe() -> str | None:
            return versions.pop(0) if len(versions) > 1 else versions[0]

        return probe


class TestBinaryDetection(RuntimeControllerMixin):
    def test_finds_binary_on_path(self) -> None:
        controller = self._controller(on_path="/opt/bin/ollama")
        assert controller.installed() is True
        assert controller.resolve_binary() == "/opt/bin/ollama"

    def test_finds_macos_app_bundle_when_not_on_path(self) -> None:
        controller = self._controller(platform="darwin", present=[_MAC_APP_BINARY])
        assert controller.installed() is True
        assert controller.resolve_binary() == _MAC_APP_BINARY

    def test_finds_linux_well_known_path(self) -> None:
        controller = self._controller(platform="linux", present=[_LINUX_BINARY])
        assert controller.resolve_binary() == _LINUX_BINARY

    def test_finds_windows_local_app_data_path(self) -> None:
        controller = self._controller(
            platform="win32",
            present=[_WINDOWS_BINARY],
            environ={"LOCALAPPDATA": r"C:\Users\p\AppData\Local"},
        )
        assert controller.resolve_binary() == _WINDOWS_BINARY

    def test_reports_absent_when_nothing_found(self) -> None:
        assert self._controller(platform="linux").installed() is False

    def test_macos_paths_are_not_probed_on_linux(self) -> None:
        controller = self._controller(platform="linux", present=[_MAC_APP_BINARY])
        assert controller.installed() is False


class TestManageOffPosture(RuntimeControllerMixin):
    def test_installed_is_false_even_when_binary_exists(self) -> None:
        controller = self._controller(manage=False, on_path="/opt/bin/ollama")
        assert controller.installed() is False
        assert controller.resolve_binary() is None

    def test_start_raises_typed_error(self) -> None:
        controller = self._controller(manage=False, on_path="/opt/bin/ollama")
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(controller.start())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL
        assert (
            excinfo.value.public_message == OllamaRuntimeController.Messages.NOT_MANAGED
        )

    def test_start_does_not_spawn(self) -> None:
        spawned: list[Sequence[str]] = []
        controller = self._controller(
            manage=False, on_path="/opt/bin/ollama", spawned=spawned
        )
        with pytest.raises(LocalModelError):
            asyncio.run(controller.start())
        assert spawned == []


class TestStart(RuntimeControllerMixin):
    def test_spawns_serve_with_resolved_binary(self) -> None:
        spawned: list[Sequence[str]] = []
        controller = self._controller(on_path="/opt/bin/ollama", spawned=spawned)
        asyncio.run(controller.start())
        assert spawned == [("/opt/bin/ollama", "serve")]

    def test_missing_binary_is_runtime_unreachable(self) -> None:
        controller = self._controller(platform="linux")
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(controller.start())
        assert excinfo.value.kind is LocalModelErrorKind.RUNTIME_UNREACHABLE
        assert (
            excinfo.value.public_message
            == OllamaRuntimeController.Messages.NOT_INSTALLED
        )

    def test_os_error_becomes_typed_error_not_raw_oserror(self) -> None:
        controller = self._controller(
            on_path="/opt/bin/ollama",
            spawn_error=PermissionError("EACCES /opt/bin/ollama"),
        )
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(controller.start())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL
        assert (
            excinfo.value.public_message
            == OllamaRuntimeController.Messages.SPAWN_FAILED
        )
        # The OS's own text is internal detail and must not travel.
        assert "EACCES" not in excinfo.value.public_message


class TestWaitUntilRunning(RuntimeControllerMixin):
    def test_returns_version_once_probe_answers(self) -> None:
        controller = self._controller()
        probe = self._probe([None, None, "0.5.1"])
        assert (
            asyncio.run(controller.wait_until_running(probe, timeout=10.0)) == "0.5.1"
        )

    def test_returns_none_on_timeout(self) -> None:
        controller = self._controller()
        probe = self._probe([None])
        assert asyncio.run(controller.wait_until_running(probe, timeout=3.0)) is None

    def test_probes_at_least_once_with_zero_timeout(self) -> None:
        calls = {"n": 0}

        async def probe() -> str | None:
            calls["n"] += 1
            return None

        controller = self._controller()
        assert asyncio.run(controller.wait_until_running(probe, timeout=0.0)) is None
        assert calls["n"] == 1


class DerivationMixin:
    """Builds a LocalModelService whose Ollama daemon is a MockTransport."""

    @staticmethod
    def _service(
        *,
        version: str | None,
        controller: OllamaRuntimeController,
        version_sequence: list[str | None] | None = None,
    ) -> LocalModelService:
        """``version_sequence`` lets the daemon change answer between probes."""

        def next_version() -> str | None:
            if version_sequence is None:
                return version
            if len(version_sequence) > 1:
                return version_sequence.pop(0)
            return version_sequence[0]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path != "/api/version":
                return httpx.Response(500)
            current = next_version()
            if current is None:
                return httpx.Response(500)
            return httpx.Response(200, json={"version": current})

        return LocalModelService(
            ollama=OllamaClient(
                base_url="http://localhost:11434",
                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ),
            hf=HfGgufResolver(
                client=httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda r: httpx.Response(404))
                )
            ),
            runtime=controller,
            start_timeout_seconds=5.0,
        )


class TestStatusDerivation(RuntimeControllerMixin, DerivationMixin):
    """PRD-P8 §4.2 — all five rows."""

    def test_disabled_is_unknown_and_not_running(self) -> None:
        service = self._service(version="0.5.1", controller=self._controller())
        status = asyncio.run(service.status(enabled=False))
        assert status.runtime_state is LocalRuntimeState.UNKNOWN
        assert status.ollama_running is False
        assert status.ollama_version is None
        assert status.runtime_managed is False

    def test_version_present_is_running(self) -> None:
        service = self._service(version="0.5.1", controller=self._controller())
        status = asyncio.run(service.status(enabled=True))
        assert status.runtime_state is LocalRuntimeState.RUNNING
        assert status.ollama_running is True
        assert status.ollama_version == "0.5.1"
        assert status.runtime_managed is True

    def test_manage_off_is_unknown_not_not_installed(self) -> None:
        service = self._service(
            version=None, controller=self._controller(manage=False, on_path=None)
        )
        status = asyncio.run(service.status(enabled=True))
        assert status.runtime_state is LocalRuntimeState.UNKNOWN
        assert status.runtime_managed is False

    def test_binary_present_but_silent_is_stopped(self) -> None:
        service = self._service(
            version=None, controller=self._controller(on_path="/opt/bin/ollama")
        )
        status = asyncio.run(service.status(enabled=True))
        assert status.runtime_state is LocalRuntimeState.STOPPED
        assert status.runtime_managed is True

    def test_no_binary_is_not_installed(self) -> None:
        service = self._service(
            version=None, controller=self._controller(platform="linux")
        )
        status = asyncio.run(service.status(enabled=True))
        assert status.runtime_state is LocalRuntimeState.NOT_INSTALLED

    def test_managed_is_false_when_feature_disabled(self) -> None:
        service = self._service(
            version=None, controller=self._controller(on_path="/opt/bin/ollama")
        )
        status = asyncio.run(service.status(enabled=False))
        assert status.runtime_managed is False


class ApiTypesMirrorMixin:
    """Reads the TypeScript mirror so drift fails here, not in a client."""

    _PACKAGE = Path(__file__).resolve().parents[5] / "packages/api-types/src"
    _MIRROR = _PACKAGE / "localModels.ts"
    _BARREL = _PACKAGE / "index.ts"

    @classmethod
    def _source(cls) -> str:
        return cls._MIRROR.read_text(encoding="utf-8")

    @classmethod
    def _interface_fields(cls, name: str) -> set[str]:
        """Property names of ``export interface <name> { … }``.

        Compares the whole field *set*, not a handful of substrings: a field
        added on one side only (either direction) is the drift this exists to
        catch, and a spot-check cannot see a deletion.
        """

        match = re.search(
            rf"export interface {name} \{{(.*?)\n\}}", cls._source(), re.S
        )
        assert match is not None, f"{name} is missing from {cls._MIRROR.name}"
        body = re.sub(r"/\*\*.*?\*/", "", match.group(1), flags=re.S)
        return set(re.findall(r"readonly (\w+)\??:", body))

    @classmethod
    def _barrel_exports(cls, module: str) -> set[str]:
        """Names re-exported from ``./<module>`` by the package entry point."""

        source = cls._BARREL.read_text(encoding="utf-8")
        # ``[^{}]`` so the body cannot run across the many other
        # ``export type { … } from "./…"`` blocks in the barrel.
        match = re.search(rf'export type \{{([^{{}}]*)\}} from "\./{module}";', source)
        assert match is not None, f"index.ts re-exports nothing from ./{module}"
        return {name.strip() for name in match.group(1).split(",") if name.strip()}

    @classmethod
    def _union_members(cls, name: str) -> set[str]:
        """String-literal members of ``export type <name> = "a" | "b";``.

        Matched across the whole declaration so a Prettier reflow (one line vs
        several) cannot turn a real contract change into a passing test — or a
        cosmetic reformat into a failing one.
        """

        match = re.search(rf"export type {name}\s*=\s*([^;]+);", cls._source())
        assert match is not None, f"{name} is missing from {cls._MIRROR.name}"
        return set(re.findall(r'"([^"]+)"', match.group(1)))


class TestApiTypesMirror(ApiTypesMirrorMixin):
    """PRD-P8 §9 — the Pydantic and TypeScript shapes must agree."""

    def test_runtime_state_members_match(self) -> None:
        assert self._union_members("LocalRuntimeState") == {
            state.value for state in LocalRuntimeState
        }

    def test_error_kind_members_match(self) -> None:
        assert self._union_members("LocalModelErrorKind") == {
            kind.value for kind in LocalModelErrorKind
        }

    def test_new_status_fields_are_optional_in_typescript(self) -> None:
        """D3 — additive only: a required field would be a breaking change."""

        source = self._source()
        assert "readonly runtime_state?: LocalRuntimeState;" in source
        assert "readonly runtime_managed?: boolean;" in source

    def test_pull_event_carries_optional_error_kind(self) -> None:
        assert "readonly error_kind?: LocalModelErrorKind | null;" in self._source()

    def test_status_field_sets_match(self) -> None:
        assert self._interface_fields("LocalModelsStatus") == set(
            LocalModelsStatus.model_fields
        )

    def test_pull_event_field_sets_match(self) -> None:
        assert self._interface_fields("LocalModelPullEvent") == set(
            LocalModelPullEvent.model_fields
        )

    def test_new_types_are_reachable_from_the_package_entry_point(self) -> None:
        """Consumers import from ``@0x-copilot/api-types``, not the file.

        ``package.json`` points ``types`` at ``src/index.ts``, so a type that
        exists in ``localModels.ts`` but is not re-exported there is invisible
        to every app and package — and nothing in this repo's typechecks fails,
        because no consumer can reference it yet.
        """

        exported = self._barrel_exports("localModels")
        assert {"LocalRuntimeState", "LocalModelErrorKind"} <= exported


class TestStartRuntime(RuntimeControllerMixin, DerivationMixin):
    def test_already_running_is_idempotent_and_never_spawns(self) -> None:
        spawned: list[Sequence[str]] = []
        controller = self._controller(on_path="/opt/bin/ollama", spawned=spawned)
        service = self._service(version="0.5.1", controller=controller)
        status = asyncio.run(service.start_runtime())
        assert status.runtime_state is LocalRuntimeState.RUNNING
        assert spawned == []

    def test_spawns_then_reports_running_when_daemon_comes_up(self) -> None:
        spawned: list[Sequence[str]] = []
        controller = self._controller(on_path="/opt/bin/ollama", spawned=spawned)
        # Probe 1 (the idempotency check) sees a dead daemon; the post-spawn
        # poll sees it answer on the second attempt.
        service = self._service(
            version=None,
            controller=controller,
            version_sequence=[None, None, "0.5.1"],
        )
        status = asyncio.run(service.start_runtime())
        assert spawned == [("/opt/bin/ollama", "serve")]
        assert status.runtime_state is LocalRuntimeState.RUNNING
        assert status.ollama_version == "0.5.1"

    def test_daemon_never_comes_up_reports_stopped_without_raising(self) -> None:
        controller = self._controller(on_path="/opt/bin/ollama")
        service = self._service(version=None, controller=controller)
        status = asyncio.run(service.start_runtime())
        assert status.runtime_state is LocalRuntimeState.STOPPED
        assert status.ollama_running is False

    def test_spawn_failure_propagates_typed_error(self) -> None:
        controller = self._controller(
            on_path="/opt/bin/ollama", spawn_error=OSError("fork failed: ENOMEM")
        )
        service = self._service(version=None, controller=controller)
        with pytest.raises(LocalModelError) as excinfo:
            asyncio.run(service.start_runtime())
        assert excinfo.value.kind is LocalModelErrorKind.TERMINAL
        assert "ENOMEM" not in excinfo.value.public_message

    def test_default_service_runtime_is_unmanaged(self) -> None:
        """A service wired without a controller must not claim host knowledge."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        service = LocalModelService(
            ollama=OllamaClient(
                base_url="http://localhost:11434",
                client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            ),
            hf=HfGgufResolver(
                client=httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda r: httpx.Response(404))
                )
            ),
        )
        status = asyncio.run(service.status(enabled=True))
        assert status.runtime_state is LocalRuntimeState.UNKNOWN
        assert status.runtime_managed is False
