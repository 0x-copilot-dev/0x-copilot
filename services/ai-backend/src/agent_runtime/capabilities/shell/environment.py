"""The child environment, built by allowlist (PRD-shell-execution §11.3).

**This is an allowlist, not a filter.** Nothing is removed from ``os.environ``;
``os.environ`` is never the starting point. The builder reads a fixed tuple of
names and writes a fixed tuple of names, and it never iterates the parent
environment at all — which is the property, not the list. A denylist is the
wrong default here because our secret set grows with every provider we add: the
worker process holds the provider keys, the loopback broker bearer
(``DESKTOP_WORKSPACE_BROKER_TOKEN``, with ``DESKTOP_BROKER_TOKEN`` as the legacy
fallback) and ``ENTERPRISE_SERVICE_TOKEN``, and ``env | grep -i key`` would print
all of it.

Security invariant §5 is what is at stake: provider keys ride
``AgentRuntimeContext.provider_keys`` with ``exclude=True, repr=False`` so they
never appear in a payload, an event or a repr. Inheriting ``os.environ`` for one
child process would defeat that in a single call, because a command is the one
call shape with no path for the filesystem controls to key on.

The test that guards this is deliberately **name-independent** (AC6.2): it puts
a freshly generated, never-mentioned variable into the parent environment and
asserts it is absent from the child's. A test that asserts on a list of known
secret names is only a regression pin — an earlier draft of the PRD asserted on
``COPILOT_BROKER_TOKEN``, a string that appears nowhere in this repository, and
would have passed over an environment leaking the real token.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from typing import Final


class ShellEnvironment:
    """The names and values that make up a command's environment.

    Constants live here rather than on the builder so a guardrail test can
    assert on the allowlist itself without constructing anything.
    """

    #: Read from the parent environment when present, otherwise defaulted or
    #: omitted. This tuple is the entire read surface: no other name in the
    #: worker's environment can reach a child process.
    PASSTHROUGH: Final = ("LANG", "LC_ALL", "TZ", "USER", "LOGNAME")

    #: Passthrough names that get a deterministic value when the parent has
    #: none. ``USER``/``LOGNAME`` are absent rather than invented — a wrong
    #: username is worse than a missing one.
    PASSTHROUGH_DEFAULTS: Final = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }

    #: Written unconditionally, from runtime facts rather than from the parent.
    PATH: Final = "PATH"
    HOME: Final = "HOME"
    TMPDIR: Final = "TMPDIR"
    PWD: Final = "PWD"

    #: Fixed values. ``TERM=dumb`` and ``NO_COLOR=1`` suppress most ANSI at the
    #: source (§13); ``CI=1`` makes most test runners non-interactive and
    #: deterministic, which matters because stdin is closed.
    FIXED: Final = {"TERM": "dumb", "NO_COLOR": "1", "CI": "1"}

    #: Prepended to ``PATH``, relative to the bound root, so a project's own
    #: toolchain wins over the system one — which is what makes ``pytest``
    #: resolve to the workspace's ``.venv`` rather than to nothing. The cost is
    #: stated rather than hidden: a repository can shadow a system binary this
    #: way. That is not a new exposure, because §11.5 already says a command
    #: runs as the user with the user's permissions and no OS isolation.
    LOCAL_BIN_DIRS: Final = (".venv/bin", "venv/bin", "node_modules/.bin", "bin")

    #: The system half of ``PATH``. A constructed list, not the worker's own
    #: ``PATH`` verbatim: the worker's ``PATH`` is staged by the desktop runtime
    #: and points at bundled interpreters we do not want a command resolving
    #: against. The Homebrew prefixes are listed unconditionally; a ``PATH``
    #: entry that does not exist costs nothing and probing the filesystem here
    #: would make the built environment depend on the host it was built on.
    SYSTEM_BIN_DIRS: Final = (
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    )

    #: Never copied, and not because they are on a denylist — they are simply
    #: not on the allowlist. Named here only so the reason survives: an
    #: inherited ``VIRTUAL_ENV``/``CONDA_PREFIX`` makes a command in workspace A
    #: install into workspace B's interpreter.
    NOTABLY_ABSENT: Final = ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "SHELL")


class ShellEnvironmentBuilder:
    """Builds one child environment from runtime facts plus a fixed allowlist.

    Stateless and total: it raises nothing, and the request cannot influence it
    because the request never reaches it. Its only inputs are the bound root
    (chosen by the runtime from the writable grants, never by the model), the
    agent's own scratch directory, and the parent mapping it is allowed to read
    five names out of.
    """

    def build(
        self,
        *,
        bound_root: Path,
        scratch_dir: Path,
        source: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Return the complete environment for one command.

        ``source`` defaults to ``os.environ``. It is read strictly by
        ``.get(name)`` over :attr:`ShellEnvironment.PASSTHROUGH` plus ``HOME``;
        it is never iterated, so a variable this module has not been told about
        has no path into the result.
        """

        parent = source if source is not None else os.environ
        env: dict[str, str] = {
            ShellEnvironment.PATH: self._build_path(bound_root),
            ShellEnvironment.HOME: self._resolve_home(parent, scratch_dir),
            ShellEnvironment.TMPDIR: str(scratch_dir),
            ShellEnvironment.PWD: str(bound_root),
        }
        env.update(ShellEnvironment.FIXED)
        for name in ShellEnvironment.PASSTHROUGH:
            value = self._clean(parent.get(name))
            if value is None:
                value = ShellEnvironment.PASSTHROUGH_DEFAULTS.get(name)
            if value is not None:
                env[name] = value
        return env

    def _build_path(self, bound_root: Path) -> str:
        """Workspace-local bin directories first, then a constructed system PATH.

        Any entry containing the path separator is dropped: a bound root with a
        colon in its name would otherwise split into two ``PATH`` entries, one
        of which is a directory nobody chose.
        """

        entries = [
            str(bound_root / relative) for relative in ShellEnvironment.LOCAL_BIN_DIRS
        ]
        entries.extend(ShellEnvironment.SYSTEM_BIN_DIRS)
        return os.pathsep.join(
            entry for entry in entries if os.pathsep not in entry and entry
        )

    def _resolve_home(self, parent: Mapping[str, str], scratch_dir: Path) -> str:
        """v1: the user's real home. Phase 2 replaces this with a per-run scratch.

        Stated plainly rather than left implicit: with the real ``HOME``, a
        command can reach ``~/.ssh``, ``~/.aws`` and every dotfile the user
        owns, by expansion. That is a documented residual risk of Phase 1
        (§11.5), not an oversight, and the never-list is defence in depth
        against the plausible accident rather than a boundary.

        The scratch directory is the fallback only when the parent has no
        ``HOME`` and the platform cannot resolve one — a command with no
        ``HOME`` at all breaks tools in confusing ways.
        """

        explicit = self._clean(parent.get(ShellEnvironment.HOME))
        if explicit:
            return explicit
        try:
            return str(Path.home())
        except RuntimeError:
            return str(scratch_dir)

    @staticmethod
    def _clean(value: str | None) -> str | None:
        """Drop an unusable passthrough value rather than passing it to ``execve``.

        A NUL in an environment value raises at the exec boundary, which would
        turn a strange-but-harmless parent environment into a capability that
        cannot spawn anything.
        """

        if value is None or "\x00" in value:
            return None
        return value
