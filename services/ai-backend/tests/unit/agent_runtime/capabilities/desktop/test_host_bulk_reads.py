"""`ls` inside a folder the user attached must not ask (the live FS-H report).

WHY THIS IS A SEPARATE FILE FROM THE RULE TESTS. deepagents splits filesystem
tools into two scopes and only one of them is decided by the rules:

* ``exact`` (`read_file`, `write_file`, `edit_file`) — `_check_fs_permission`
  against the named path. That is what `test_host_filesystem.py` covers.
* ``bulk`` (`ls`, `glob`, `grep`) — the path is a search ROOT, and
  `_make_bulk_when_predicate` fires whenever that subtree OVERLAPS an
  interrupt-mode rule. It reads interrupt rules ONLY: not the allow rules, not
  rule order.

Rule 4's anchor is ``/``, and everything overlaps ``/``. So every `ls` fired on
every path, including inside a folder just attached, and the consent card could
name ``/workspace`` — a mount the user has never heard of. No change to the
rules could fix that; the predicate would not have consulted them.

These tests drive the REAL composition (`_with_host_bulk_read_scope` →
deepagents' own generated config → the `when` deepagents will call), because a
test of our containment helper alone is exactly the test that stayed green
while the packaged app kept asking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostBulkReadScope,
)
from agent_runtime.execution.factory import _with_host_bulk_read_scope

GRANTED = "/Users/ada/Projects"
UNGRANTED = "/Users/ada/Downloads"


class BulkInterruptMixin:
    """Builds the interrupt map the deep agent is actually handed."""

    @staticmethod
    def overrides(*roots: GrantedRoot, existing: dict | None = None) -> dict:
        backend = SimpleNamespace(granted_roots=roots or (GrantedRoot(path=GRANTED),))
        return _with_host_bulk_read_scope(existing or {}, backend)

    @classmethod
    def asks(cls, tool: str, *, roots: tuple = (), **args: object) -> bool:
        config = cls.overrides(*roots)[tool]
        when = config["when"] if isinstance(config, dict) else config.when
        return bool(when(SimpleNamespace(tool_call={"name": tool, "args": args})))


class TestAnAttachedFolderGoesQuiet(BulkInterruptMixin):
    """The report: "an `ls` inside an attached folder still asked"."""

    @pytest.mark.parametrize(
        "path", [GRANTED, f"{GRANTED}/sub", f"{GRANTED}/sub/deeper", "/workspace"]
    )
    def test_a_bulk_read_inside_granted_ground_is_silent(self, path: str) -> None:
        assert self.asks("ls", path=path) is False

    def test_the_agents_own_namespaces_are_silent_too(self) -> None:
        """`ls("/workspace")` is how the agent finds what is attached.

        It was the very call that produced the live consent card, so it is the
        one that must not prompt.
        """

        for path in ("/workspace", "/memories", "/skills"):
            assert self.asks("ls", path=path) is False, path


class TestTheBoundOnGoingQuiet(BulkInterruptMixin):
    """Suppression is CONTAINMENT. Everything else keeps asking."""

    @pytest.mark.parametrize("path", ["/", "/Users", "/Users/ada", UNGRANTED])
    def test_a_parent_of_a_grant_still_asks(self, path: str) -> None:
        """`/Users` is not covered by a grant on `/Users/ada/Projects`.

        Listing it would surface the user's ungranted siblings, so an overlap
        test would have been the wrong question in the other direction.
        """

        assert self.asks("ls", path=path) is True

    def test_a_pathless_bulk_call_still_asks(self) -> None:
        """It cannot be localized, so it could touch anything."""

        assert self.asks("grep") is True
        assert self.asks("ls") is True

    @pytest.mark.parametrize("alias", [".", "", "./"])
    def test_a_current_directory_alias_still_asks(self, alias: str) -> None:
        """`validate_path` maps these to the whole accessible tree.

        Without normalising them an agent could pass `path="."` and read the
        machine with no prompt at all.
        """

        assert self.asks("ls", path=alias) is True

    def test_a_glob_pattern_cannot_redirect_the_search_out(self) -> None:
        """`glob(pattern="/secrets/**", path=<granted>)` sweeps `/secrets`.

        deepagents guards this; a replacement predicate that forgot to would be
        a silent hole, which is the whole risk of overriding its config.
        """

        assert self.asks("glob", path=GRANTED, pattern="*.csv") is False
        assert self.asks("glob", path=GRANTED, pattern="/secrets/**") is True
        assert self.asks("glob", path=GRANTED, pattern="../../etc") is True

    def test_attaching_one_folder_does_not_quiet_another(self) -> None:
        roots = (GrantedRoot(path=GRANTED),)
        assert self.asks("ls", path="/Users/ada/ProjectsSecret", roots=roots) is True


class TestWhatThisOverrideRefusesToTouch(BulkInterruptMixin):
    def test_a_policy_supplied_entry_always_wins(self) -> None:
        """A tool the tool-use policy gated is not ours to relax.

        That entry is a deliberate decision by an admin; silently replacing it
        with a laxer predicate would make the policy surface a lie.
        """

        sentinel = {"allowed_decisions": ["approve"], "when": lambda _req: True}
        merged = self.overrides(existing={"ls": sentinel})
        assert merged["ls"] is sentinel

    def test_no_workspace_backend_changes_nothing(self) -> None:
        """Off the desktop path the map must be byte-identical."""

        assert _with_host_bulk_read_scope({"call_mcp_tool": True}, None) == {
            "call_mcp_tool": True
        }


class TestContainmentFailsClosed:
    """The helper answers `True` only to SUPPRESS, so unsure must be `False`."""

    @pytest.mark.parametrize(
        "path", ["relative/path", "..", "../escape", "", "Users/ada/Projects"]
    )
    def test_anything_it_cannot_reason_about_is_not_confined(self, path: str) -> None:
        scope = HostBulkReadScope.build((GrantedRoot(path=GRANTED),))
        assert scope.confines(path) is False

    def test_an_empty_scope_confines_nothing(self) -> None:
        assert HostBulkReadScope(prefixes=()).confines(GRANTED) is False

    @pytest.mark.parametrize("pattern", [None, 42, "/abs", "../up"])
    def test_a_pattern_that_might_escape_is_treated_as_escaping(
        self, pattern: object
    ) -> None:
        assert HostBulkReadScope.pattern_stays_inside(pattern) is False
