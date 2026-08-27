"""The child environment is an allowlist (PRD-shell-execution §11.3, AC6.1/AC6.2).

The load-bearing test in this file is
``test_never_reads_a_name_it_was_not_told_about``: it hands the builder a mapping
that **raises if anything iterates it**, so a denylist implementation cannot pass
— a denylist must enumerate the parent environment to filter it. That is a
structural proof of the property rather than an assertion over a list of secret
names, which is only ever a regression pin (an earlier PRD draft asserted on
``COPILOT_BROKER_TOKEN``, a string that appears nowhere in this repository, and
would have been vacuously green over a leaking environment).

The end-to-end half — a novel secret in the real ``os.environ`` not reaching a
real child process — lives in ``test_executor.py``, because only a spawned
process can prove it.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from agent_runtime.capabilities.shell.environment import (
    ShellEnvironment,
    ShellEnvironmentBuilder,
)


class HostileMapping(Mapping[str, str]):
    """A parent environment that refuses to be enumerated.

    ``get``/``__getitem__`` work; every path that would let a caller discover a
    name it did not already know raises. Any implementation that scans the
    parent environment — which is what a denylist must do — fails here.
    """

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        raise AssertionError(
            "the environment builder enumerated the parent environment; "
            "an allowlist reads named keys only"
        )

    def __len__(self) -> int:
        raise AssertionError("the environment builder measured the parent environment")

    def keys(self):  # type: ignore[no-untyped-def]
        raise AssertionError("the environment builder listed the parent environment")

    def items(self):  # type: ignore[no-untyped-def]
        raise AssertionError("the environment builder listed the parent environment")

    def values(self):  # type: ignore[no-untyped-def]
        raise AssertionError("the environment builder listed the parent environment")


class EnvironmentBuilderMixin:
    """Shared roots and a build helper."""

    ROOT = Path("/workspaces/project")
    SCRATCH = Path("/home/someone/.0xcopilot/.tmp")

    def build(self, source: Mapping[str, str] | None = None) -> dict[str, str]:
        return ShellEnvironmentBuilder().build(
            bound_root=self.ROOT,
            scratch_dir=self.SCRATCH,
            source={} if source is None else source,
        )


class TestAllowlistShape(EnvironmentBuilderMixin):
    def test_never_reads_a_name_it_was_not_told_about(self) -> None:
        """The property, structurally: no enumeration of the parent environment."""

        built = ShellEnvironmentBuilder().build(
            bound_root=self.ROOT,
            scratch_dir=self.SCRATCH,
            source=HostileMapping({"HOME": "/home/someone", "LANG": "en_US.UTF-8"}),
        )

        assert built["HOME"] == "/home/someone"
        assert built["LANG"] == "en_US.UTF-8"

    def test_a_novel_secret_in_the_parent_is_absent_from_the_child(self) -> None:
        """Name-independent by construction: the name is generated per run.

        It cannot be defeated by a typo in a denylist, and it fails the moment
        anyone converts the allowlist to a denylist.
        """

        novel = f"ZZ_{uuid.uuid4().hex.upper()}"

        built = self.build({novel: "super-secret", "HOME": "/home/someone"})

        assert novel not in built
        assert "super-secret" not in built.values()

    def test_writes_exactly_the_expected_names(self) -> None:
        built = self.build({"HOME": "/home/someone"})

        assert set(built) == {
            "PATH",
            "HOME",
            "TMPDIR",
            "PWD",
            "TERM",
            "NO_COLOR",
            "CI",
            "LANG",
            "LC_ALL",
            "TZ",
        }

    @pytest.mark.parametrize("name", ShellEnvironment.NOTABLY_ABSENT)
    def test_the_named_hazards_never_reach_the_child(self, name: str) -> None:
        """Not a denylist — they are simply not on the allowlist.

        ``VIRTUAL_ENV``/``CONDA_PREFIX`` would make a command in one workspace
        install into another's interpreter; ``SHELL`` would be a way back to the
        login shell §11.4 refuses.
        """

        built = self.build({name: "/somewhere/dangerous", "HOME": "/home/someone"})

        assert name not in built


class TestPath(EnvironmentBuilderMixin):
    def test_puts_workspace_local_bins_before_the_system_ones(self) -> None:
        entries = self.build().get("PATH", "").split(os.pathsep)

        assert entries[: len(ShellEnvironment.LOCAL_BIN_DIRS)] == [
            str(self.ROOT / relative) for relative in ShellEnvironment.LOCAL_BIN_DIRS
        ]
        assert entries[len(ShellEnvironment.LOCAL_BIN_DIRS) :] == list(
            ShellEnvironment.SYSTEM_BIN_DIRS
        )

    def test_is_constructed_rather_than_inherited(self) -> None:
        """The worker's own PATH points at the bundled runtime; a command must not."""

        built = self.build({"PATH": "/opt/staged-runtime/bin"})

        assert "/opt/staged-runtime/bin" not in built["PATH"]

    def test_drops_an_entry_that_would_split_the_variable(self) -> None:
        """A bound root containing a colon would otherwise become two entries."""

        built = ShellEnvironmentBuilder().build(
            bound_root=Path("/weird:root"), scratch_dir=self.SCRATCH, source={}
        )

        assert "/weird:root" not in built["PATH"]
        assert built["PATH"].split(os.pathsep) == list(ShellEnvironment.SYSTEM_BIN_DIRS)


class TestFixedAndPassthrough(EnvironmentBuilderMixin):
    def test_fixed_values_win_over_the_parent(self) -> None:
        built = self.build({"TERM": "xterm-256color", "NO_COLOR": "", "CI": "false"})

        assert built["TERM"] == "dumb"
        assert built["NO_COLOR"] == "1"
        assert built["CI"] == "1"

    def test_locale_falls_back_deterministically(self) -> None:
        built = self.build()

        assert built["LANG"] == "C.UTF-8"
        assert built["LC_ALL"] == "C.UTF-8"
        assert built["TZ"] == "UTC"

    def test_locale_passes_through_when_the_parent_has_one(self) -> None:
        built = self.build({"LANG": "en_GB.UTF-8", "TZ": "Europe/London"})

        assert built["LANG"] == "en_GB.UTF-8"
        assert built["TZ"] == "Europe/London"

    def test_user_and_logname_are_absent_rather_than_invented(self) -> None:
        built = self.build()

        assert "USER" not in built and "LOGNAME" not in built

    def test_user_passes_through_when_present(self) -> None:
        built = self.build({"USER": "someone", "LOGNAME": "someone"})

        assert built["USER"] == "someone"

    def test_drops_a_value_the_exec_boundary_would_reject(self) -> None:
        """A NUL in an env value raises at ``execve``; a strange parent
        environment must not turn into a capability that cannot spawn."""

        built = self.build({"USER": "some\x00one", "LANG": "en\x00_GB"})

        assert "USER" not in built
        assert built["LANG"] == "C.UTF-8"


class TestHome(EnvironmentBuilderMixin):
    def test_uses_the_parents_home_in_phase_one(self) -> None:
        """Stated rather than implied: v1 gives the command the real home.

        AC6.4's scratch ``HOME`` is Phase 2. This test is the in-code record
        that Phase 1 does **not** have it, so a reader cannot mistake the
        never-list for a boundary around ``~/.ssh``.
        """

        built = self.build({"HOME": "/home/someone"})

        assert built["HOME"] == "/home/someone"

    def test_falls_back_to_the_scratch_when_there_is_no_home(self) -> None:
        built = self.build({})

        assert built["HOME"] in {str(Path.home()), str(self.SCRATCH)}

    def test_points_pwd_and_tmpdir_at_runtime_facts(self) -> None:
        built = self.build()

        assert built["PWD"] == str(self.ROOT)
        assert built["TMPDIR"] == str(self.SCRATCH)
