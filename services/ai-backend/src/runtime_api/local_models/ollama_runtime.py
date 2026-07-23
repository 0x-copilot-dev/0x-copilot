"""Host-side control of the user's Ollama daemon (PRD-P8 §4.3, D2).

Detection and process spawn are the only two things in this service that
touch the host outside an HTTP client, so they are gated by a single
deployment switch (``RUNTIME_LOCAL_MODELS_MANAGE_RUNTIME``) and wrapped in
one class. When the switch is off this controller reports "not installed"
and refuses to spawn — a containerised self-host must never claim knowledge
of a host filesystem it cannot see, and must never fork a process on it.

Every OS interaction is an injected seam (``which`` / ``exists`` / ``spawn``
/ ``clock`` / ``sleep`` / ``platform`` / ``environ``) so tests exercise the
real control flow without a real binary, a real filesystem, or a real fork.
"""

from __future__ import annotations

import asyncio
import ntpath
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import ClassVar

from runtime_api.local_models.ollama_client import LocalModelError
from runtime_api.schemas.local_models import LocalModelErrorKind


class OllamaRuntimeController:
    """Detect, and optionally start, the local Ollama daemon.

    ``manage=False`` is the default posture for every deployment except the
    desktop runtime: :meth:`installed` answers ``False`` (honest — we cannot
    look) and :meth:`start` raises a typed error. The route gate rejects
    before either is reached; this class is the defence-in-depth layer.
    """

    _BINARY = "ollama"
    _SERVE_ARG = "serve"
    _DEFAULT_START_TIMEOUT_SECONDS = 20.0
    _DEFAULT_POLL_INTERVAL_SECONDS = 0.5

    # Per-platform install locations checked when the binary is not on PATH
    # (the API process often runs with a minimal PATH — desktop supervisors
    # and launchd/systemd units do not inherit a login shell's PATH).
    _WELL_KNOWN_PATHS: Mapping[str, tuple[str, ...]] = {
        "darwin": (
            "/Applications/Ollama.app/Contents/Resources/ollama",
            "/usr/local/bin/ollama",
            "/opt/homebrew/bin/ollama",
        ),
        "linux": (
            "/usr/local/bin/ollama",
            "/usr/bin/ollama",
        ),
    }
    _WINDOWS_LOCAL_APP_DATA = "LOCALAPPDATA"
    _WINDOWS_RELATIVE_PATH = ("Programs", "Ollama", "ollama.exe")

    # Controllers are built per request, so the handle for a spawned daemon
    # would be garbage-collected while the child still runs (a "subprocess is
    # still running" ResourceWarning, and an unreaped child on POSIX). Hold
    # the handles process-wide and drop them once ``poll()`` reaps them.
    _spawned: ClassVar[list[subprocess.Popen[bytes]]] = []

    class Messages:
        """Public, deployment-safe failure messages."""

        NOT_MANAGED = "This deployment does not manage the local model runtime."
        NOT_INSTALLED = "Ollama is not installed on this machine."
        SPAWN_FAILED = "Could not start the local model runtime."

    def __init__(
        self,
        *,
        manage: bool,
        which: Callable[[str], str | None] = shutil.which,
        exists: Callable[[str], bool] = os.path.isfile,
        spawn: Callable[[Sequence[str]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        platform: str = sys.platform,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._manage = manage
        self._which = which
        self._exists = exists
        self._spawn = spawn
        self._clock = clock
        self._sleep = sleep
        self._platform = platform
        self._environ = environ if environ is not None else os.environ

    @property
    def manage(self) -> bool:
        """Whether this deployment is allowed to inspect / start the runtime."""

        return self._manage

    def installed(self) -> bool:
        """Whether an ``ollama`` binary exists on this machine.

        Always ``False`` when unmanaged: an unmanaged deployment has no
        standing to answer the question, and a confident ``False`` there
        would be read as ``not_installed`` instead of ``unknown``.
        """

        if not self._manage:
            return False
        return self.resolve_binary() is not None

    def resolve_binary(self) -> str | None:
        """Return the path to the ``ollama`` binary, or ``None`` if absent."""

        if not self._manage:
            return None
        on_path = self._which(self._BINARY)
        if on_path:
            return on_path
        for candidate in self._candidates():
            if self._exists(candidate):
                return candidate
        return None

    async def start(self) -> None:
        """Spawn ``ollama serve`` detached from this process.

        Detached means: its own session/process group, no inherited stdio, so
        it outlives the API process and can never write into our streams.
        Never raises a raw ``OSError`` — every spawn failure becomes a typed
        :class:`LocalModelError` with a safe public message.
        """

        if not self._manage:
            raise LocalModelError(
                self.Messages.NOT_MANAGED, kind=LocalModelErrorKind.TERMINAL
            )
        binary = self.resolve_binary()
        if binary is None:
            raise LocalModelError(
                self.Messages.NOT_INSTALLED,
                kind=LocalModelErrorKind.RUNTIME_UNREACHABLE,
            )
        spawn = self._spawn if self._spawn is not None else self._spawn_detached
        try:
            spawn((binary, self._SERVE_ARG))
        except (OSError, ValueError) as exc:
            raise LocalModelError(
                self.Messages.SPAWN_FAILED, kind=LocalModelErrorKind.TERMINAL
            ) from exc

    async def wait_until_running(
        self,
        probe: Callable[[], Awaitable[str | None]],
        *,
        timeout: float = _DEFAULT_START_TIMEOUT_SECONDS,
        interval: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> str | None:
        """Poll ``probe`` until it reports a version, or the timeout elapses.

        ``probe`` is the daemon-version probe (injected, so this never owns an
        HTTP client). Returns the version string, or ``None`` on timeout —
        the caller turns that into an honest ``STOPPED`` status rather than an
        exception, because a slow start is not an error.
        """

        deadline = self._clock() + max(timeout, 0.0)
        while True:
            version = await probe()
            if version is not None:
                return version
            if self._clock() >= deadline:
                return None
            await self._sleep(interval)

    # ------------------------------------------------------------------
    # Host interaction
    # ------------------------------------------------------------------

    def _candidates(self) -> tuple[str, ...]:
        """Well-known install paths for the current platform."""

        if self._platform.startswith("win"):
            root = self._environ.get(self._WINDOWS_LOCAL_APP_DATA, "").strip()
            if not root:
                return ()
            # ``ntpath`` (not ``os.path``) so the separator is correct even
            # when the classifier is exercised from a POSIX test host.
            return (ntpath.join(root, *self._WINDOWS_RELATIVE_PATH),)
        for prefix, paths in self._WELL_KNOWN_PATHS.items():
            if self._platform.startswith(prefix):
                return paths
        return ()

    def _spawn_detached(self, command: Sequence[str]) -> None:
        """Start ``command`` in its own session with no inherited stdio."""

        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if self._platform.startswith("win"):
            # DETACHED_PROCESS + a new group: no console, no Ctrl-C inheritance.
            kwargs["creationflags"] = int(
                getattr(subprocess, "DETACHED_PROCESS", 0)
            ) | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        # argv is built from a resolved binary path + a literal flag — never
        # from request data — and ``shell`` is left off.
        process = subprocess.Popen(list(command), **kwargs)  # noqa: S603
        cls = type(self)
        cls._spawned = [held for held in cls._spawned if held.poll() is None]
        cls._spawned.append(process)


__all__ = ["OllamaRuntimeController"]
