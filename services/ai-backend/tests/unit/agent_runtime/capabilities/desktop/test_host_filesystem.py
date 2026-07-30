"""The host filesystem rule set, judged by deepagents' OWN matcher.

Every assertion here runs the rules through `_check_fs_permission` — the
function deepagents actually calls at tool time — rather than through a local
reimplementation. That distinction is the whole point of the file: the previous
generation of these tests asserted our own predicate and stayed green while the
packaged app answered `ls ~/Downloads` with an empty listing.
"""

from __future__ import annotations

import pytest
from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _check_fs_permission,
)

from agent_runtime.capabilities.desktop.host_filesystem import (
    SCRATCH_DIR_NAME,
    GrantedRoot,
    HostFilesystemRules,
)

GRANTED = "/Users/ada/Projects"
UNGRANTED = "/Users/ada/Downloads"


class RuleSetMixin:
    """Builds real `FilesystemPermission` objects from our plain dicts."""

    @staticmethod
    def rules(*roots: GrantedRoot) -> list[FilesystemPermission]:
        return [
            FilesystemPermission(**rule)  # type: ignore[arg-type]
            for rule in HostFilesystemRules.build(roots)
        ]

    def verdict(self, path: str, *, operation: str = "read", roots: tuple = ()) -> str:
        return _check_fs_permission(self.rules(*roots), operation, path)


class TestTheDefectThisReplaces(RuleSetMixin):
    def test_an_ungranted_path_asks_instead_of_being_allowed(self) -> None:
        """The live bug: this used to be answered by memory as an empty listing."""

        assert self.verdict(UNGRANTED) == "interrupt"

    def test_it_asks_even_when_nothing_has_been_granted_yet(self) -> None:
        """First-run is exactly when the user has no grants — and wants one."""

        assert self.verdict(UNGRANTED, roots=()) == "interrupt"

    @pytest.mark.parametrize(
        "path",
        ["/", "/etc/passwd", "/private/var/tmp/x", "/Volumes/ext/x", "/Users/ada"],
    )
    def test_no_absolute_path_falls_through_to_the_allow_default(
        self, path: str
    ) -> None:
        """`_check_fs_permission` returns "allow" when NO rule matches.

        That default is the hazard the catch-all rule exists to close. Asserted
        against real shapes rather than inferred from the rule list, because a
        regression here is silent by construction.
        """

        assert self.verdict(path) != "allow", path


class TestGrantedRoots(RuleSetMixin):
    def test_a_granted_root_reads_without_prompting(self) -> None:
        roots = (GrantedRoot(path=GRANTED),)
        assert self.verdict(f"{GRANTED}/notes.md", roots=roots) == "allow"
        assert self.verdict(GRANTED, roots=roots) == "allow"

    def test_a_granted_root_is_writable_when_the_grant_says_so(self) -> None:
        roots = (GrantedRoot(path=GRANTED, writable=True),)
        assert self.verdict(f"{GRANTED}/out.csv", operation="write", roots=roots) == (
            "allow"
        )

    def test_a_read_only_grant_does_not_silently_become_writable(self) -> None:
        """A read-only grant must not be widened by the rule set.

        The write ASKS rather than being denied outright — the user may say yes
        — but it must never be silently allowed.
        """

        roots = (GrantedRoot(path=GRANTED, writable=False),)
        assert self.verdict(f"{GRANTED}/out.csv", operation="read", roots=roots) == (
            "allow"
        )
        assert (
            self.verdict(f"{GRANTED}/out.csv", operation="write", roots=roots)
            == "interrupt"
        )

    def test_granting_one_folder_does_not_grant_its_siblings(self) -> None:
        roots = (GrantedRoot(path=GRANTED),)
        assert self.verdict(UNGRANTED, roots=roots) == "interrupt"
        assert self.verdict("/Users/ada/ProjectsSecret/x", roots=roots) == "interrupt"

    def test_the_scratch_dir_lives_inside_the_granted_root(self) -> None:
        root = GrantedRoot(path=GRANTED)
        assert root.scratch_path == f"{GRANTED}/{SCRATCH_DIR_NAME}"
        assert (
            self.verdict(
                f"{root.scratch_path}/notes.json",
                operation="write",
                roots=(root,),
            )
            == "allow"
        )


class TestTheAgentsOwnNamespaces(RuleSetMixin):
    @pytest.mark.parametrize(
        "path",
        [
            "/memories/user/profile.json",
            "/drafts/reply.md",
            "/skills/csv/SKILL.md",
            "/subagents/researcher/out.json",
            "/large_tool_results/abc",
            "/workspace/proj/file.txt",
        ],
    )
    def test_agent_bookkeeping_never_prompts_the_user(self, path: str) -> None:
        """Ordinary memory/draft IO must not surface a consent card."""

        assert self.verdict(path) == "allow"
        assert self.verdict(path, operation="write") == "allow"


class TestGrantedRootValidation:
    def test_a_relative_root_is_refused(self) -> None:
        with pytest.raises(ValueError, match="POSIX-absolute"):
            GrantedRoot(path="Users/ada/Projects")

    def test_a_traversal_root_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"'\.\.'"):
            GrantedRoot(path="/Users/ada/../ada/Projects")
