"""The guard list must not silently fall behind ``BaseSandbox``.

`PolicyEnforcedSandboxBackend` contains the `/workspace` boundary by OVERRIDING
a fixed list of method names and calling `_guard_path` in each. That works right
up until the base class grows a new filesystem method — which then arrives
unguarded, because nothing anywhere states the list should be complete.

deepagents 0.7.1 does exactly that: it adds a concrete `delete` implemented as
`rm -rf`. Upgrading would have widened the blast radius of the single operation
where an unguarded path matters most, and no existing test would have failed.

This module is the missing statement. It reads the base class at import time, so
a dependency bump that adds an unguarded method fails HERE, at review time,
rather than in a sandbox.
"""

from __future__ import annotations

import inspect

import pytest

from agent_runtime.capabilities.sandbox.policy_backend import (
    PolicyEnforcedSandboxBackend,
)


class BaseSurfaceMixin:
    """The base class's public surface, split into what must and need not guard."""

    #: Operations that name a PATH and therefore must be contained. The async
    #: twins are listed with them: `aupload_files`/`adownload_files` delegate to
    #: the guarded sync versions on the base, which is a fine way to be guarded,
    #: so the assertion below accepts either.
    PATH_TAKING = frozenset(
        {
            "ls",
            "read",
            "write",
            "edit",
            "grep",
            "glob",
            "upload_files",
            "download_files",
            "delete",
            "als",
            "aread",
            "awrite",
            "aedit",
            "agrep",
            "aglob",
            "aupload_files",
            "adownload_files",
            "adelete",
            "ls_info",
            "glob_info",
            "grep_raw",
            "als_info",
            "aglob_info",
            "agrep_raw",
        }
    )

    #: Not path-shaped: command execution is contained by the sandbox itself,
    #: not by parsing a shell string for paths, and that is stated rather than
    #: silently assumed.
    NOT_PATH_TAKING = frozenset({"execute", "aexecute", "prepare_execution"})

    @classmethod
    def base(cls) -> type:
        return PolicyEnforcedSandboxBackend.__mro__[1]

    @classmethod
    def base_public_methods(cls) -> frozenset[str]:
        return frozenset(
            name
            for name, _ in inspect.getmembers(cls.base(), predicate=inspect.isfunction)
            if not name.startswith("_")
        )

    @classmethod
    def guarded_here(cls) -> frozenset[str]:
        return frozenset(
            name
            for name, value in vars(PolicyEnforcedSandboxBackend).items()
            if not name.startswith("_") and callable(value)
        )

    @classmethod
    def routes_through_a_guarded_method(cls, name: str) -> bool:
        """Whether the base's own implementation reaches a method we DO guard.

        `aupload_files` is `asyncio.to_thread(self.upload_files, ...)` and
        `ls_info` calls `self.ls(...)`; both land on an override. Read from
        source rather than assumed, so an upstream rewrite that stops delegating
        is caught.
        """

        method = getattr(cls.base(), name, None)
        if method is None:
            return False
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError):  # pragma: no cover - builtins
            return False
        return any(f"self.{guarded}" in source for guarded in cls.guarded_here())


class TestEveryPathTakingBaseMethodIsContained(BaseSurfaceMixin):
    def test_no_public_base_method_is_unclassified(self) -> None:
        """A method we have never considered is the actual failure mode."""

        unclassified = (
            self.base_public_methods() - self.PATH_TAKING - self.NOT_PATH_TAKING
        )

        assert not unclassified, (
            "deepagents' BaseSandbox gained public method(s) this guard has never "
            f"seen: {sorted(unclassified)}. Decide for each whether it names a path. "
            "If it does, override it here with a _guard_path call and add it to "
            "PATH_TAKING; if it does not, add it to NOT_PATH_TAKING with the reason."
        )

    @pytest.mark.parametrize("name", sorted(BaseSurfaceMixin.PATH_TAKING))
    def test_a_path_taking_method_is_guarded_or_delegates_to_one(
        self, name: str
    ) -> None:
        if name not in self.base_public_methods() and name not in self.guarded_here():
            pytest.skip(f"{name} exists on neither this version's base nor the guard")

        assert name in self.guarded_here() or self.routes_through_a_guarded_method(
            name
        ), (
            f"{name} takes a path, exists on BaseSandbox, and neither overrides "
            "_guard_path here nor delegates to a method that does — so it reaches "
            "the provider uncontained."
        )

    def test_delete_is_guarded_before_the_version_that_adds_it(self) -> None:
        """The specific 0.7.1 regression, pinned so the bump cannot reintroduce it."""

        assert "delete" in self.guarded_here()
        assert "adelete" in self.guarded_here()
