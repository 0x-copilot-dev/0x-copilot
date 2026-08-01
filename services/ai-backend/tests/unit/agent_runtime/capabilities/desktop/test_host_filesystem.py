"""The host filesystem rule set, judged by deepagents' OWN matcher.

Every assertion here runs the rules through `_check_fs_permission` — the
function deepagents actually calls at tool time — rather than through a local
reimplementation. That distinction is the whole point of the file: the previous
generation of these tests asserted our own predicate and stayed green while the
packaged app answered `ls ~/Downloads` with an empty listing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _check_fs_permission,
)

from agent_runtime.capabilities.desktop.agent_scratch import AgentScratchRoot
from agent_runtime.capabilities.desktop.host_filesystem import (
    VIRTUAL_NAMESPACES,
    GrantedRoot,
    HostFilesystemRules,
)

GRANTED = "/Users/ada/Projects"
UNGRANTED = "/Users/ada/Downloads"
#: The `.copilot` scratch PRD-FS-12 D7 removed. Named here (rather than imported
#: from the module, which no longer defines it) so the tests below can prove it
#: is gone without keeping the constant alive.
DROPPED_SCRATCH_DIR = ".copilot"


class RuleSetMixin:
    """Builds real `FilesystemPermission` objects from our plain dicts."""

    @staticmethod
    def rules(
        *roots: GrantedRoot, scratch: AgentScratchRoot | None = None
    ) -> list[FilesystemPermission]:
        return [
            FilesystemPermission(**rule)  # type: ignore[arg-type]
            for rule in HostFilesystemRules.build(roots, scratch=scratch)
        ]

    def verdict(
        self,
        path: str,
        *,
        operation: str = "read",
        roots: tuple = (),
        scratch: AgentScratchRoot | None = None,
    ) -> str:
        return _check_fs_permission(
            self.rules(*roots, scratch=scratch), operation, path
        )


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

    def test_a_writable_grant_is_writable(self) -> None:
        """The grant's MODE decides, which is what `writable` was always for.

        It carried no meaning while D7 routed every host write through the
        staged overlay — a lane that has never run on a desktop install,
        because it needs a C2 attestation only a signed packaged build can
        produce and this app ships as an unpackaged CLI payload. "Writes are
        audited" therefore meant "writes never happen".

        The consent question D7 protected is not dropped, it MOVED: the user
        answers read-only vs read-and-write when attaching the folder, once,
        knowing what they decide — rather than through per-write cards that are
        indistinguishable from read cards and get clicked through unread.
        """

        roots = (GrantedRoot(path=GRANTED, writable=True),)
        assert self.verdict(f"{GRANTED}/notes.md", roots=roots) == "allow"
        assert (
            self.verdict(f"{GRANTED}/notes.md", operation="write", roots=roots)
            == "allow"
        )

    def test_a_read_only_grant_still_refuses_writes(self) -> None:
        """The other half of the same answer — and why the question is worth asking."""

        roots = (GrantedRoot(path=GRANTED, writable=False),)
        assert self.verdict(f"{GRANTED}/notes.md", roots=roots) == "allow"
        assert (
            self.verdict(f"{GRANTED}/notes.md", operation="write", roots=roots)
            == "deny"
        )

    def test_an_ungranted_write_is_denied_not_merely_asked(self) -> None:
        assert self.verdict(f"{UNGRANTED}/x", operation="write") == "deny"

    def test_granting_one_folder_does_not_grant_its_siblings(self) -> None:
        roots = (GrantedRoot(path=GRANTED),)
        assert self.verdict(UNGRANTED, roots=roots) == "interrupt"
        assert self.verdict("/Users/ada/ProjectsSecret/x", roots=roots) == "interrupt"

    def test_a_granted_root_no_longer_sites_a_scratch_directory(self) -> None:
        """D7: nothing is written into the folder the user attached.

        `GrantedRoot` used to expose `scratch_path` and the rule set used to
        emit a write allow for `<root>/.copilot`. Both are gone: the agent's
        working area moved to `$COPILOT_HOME/.tmp`, which is ours.
        """

        root = GrantedRoot(path=GRANTED, writable=True)
        assert not hasattr(root, "scratch_path")
        assert not any(
            DROPPED_SCRATCH_DIR in path
            for rule in HostFilesystemRules.build((root,))
            for path in rule["paths"]  # type: ignore[union-attr]
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

    @pytest.mark.parametrize("prefix", VIRTUAL_NAMESPACES)
    def test_listing_the_namespace_itself_never_prompts_either(
        self, prefix: str
    ) -> None:
        """`/workspace/**` matches the CONTENTS, not the directory.

        Every one of these roots interrupted while every path beneath it
        allowed, and the two live symptoms both came out of that gap: an
        attached folder that "still asks" (the agent's first move is to list
        `/workspace` and see what it has), and a consent card whose only
        detail was `PATH /workspace` — a virtual mount the user has never
        heard of and cannot judge.

        Parametrized over the constant so a namespace added later cannot be
        added in the broken form.
        """

        bare = prefix.rstrip("/")
        assert self.verdict(bare) == "allow"
        assert self.verdict(bare, operation="write") == "allow"

    @pytest.mark.parametrize(
        "path", ["/workspacex", "/workspace-other", "/memories2", "/skillsets"]
    )
    def test_a_lookalike_neighbour_still_asks(self, path: str) -> None:
        """The bare form must be an exact segment, not a prefix.

        Allowing `/workspace` by prefix would hand over `/workspaces` and
        anything else that merely starts with the same letters — a real risk
        for `/skills` and `/drafts`, which are ordinary English words a user
        could plausibly have a folder named after.
        """

        assert self.verdict(path) == "interrupt"
        assert self.verdict(path, operation="write") == "deny"


class TestTmpIsNotAnAgentNamespace(RuleSetMixin):
    """`/tmp/` was listed as a virtual namespace. It is not one.

    Rule 1 allows every `VIRTUAL_NAMESPACES` prefix for read AND write on the
    grounds that the composite routes those paths to the agent's own backends.
    `/tmp/` is not a route, and `HostPathClassifier` classifies it as a HOST
    path — so the entry was an unqualified read+write allow over the machine's
    real `/tmp`, inside a rule set whose stated contract is that every host
    write is denied outright.
    """

    def test_the_real_host_tmp_asks_like_any_other_ungranted_folder(self) -> None:
        assert self.verdict("/tmp/x.txt") == "interrupt"
        assert self.verdict("/tmp/nested/deep.txt") == "interrupt"

    def test_the_real_host_tmp_is_not_writable(self) -> None:
        assert self.verdict("/tmp/x.txt", operation="write") == "deny"


class TestGrantedRootValidation:
    def test_a_relative_root_is_refused(self) -> None:
        with pytest.raises(ValueError, match="POSIX-absolute"):
            GrantedRoot(path="Users/ada/Projects")

    def test_a_traversal_root_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"'\.\.'"):
            GrantedRoot(path="/Users/ada/../ada/Projects")


class TestGrantsInEitherPlatformsGrammar(RuleSetMixin):
    r"""A grant has to be spelled the way a tool call is, or it buys nothing.

    `__post_init__` demands a POSIX-absolute path. A grant lane that handed it
    `C:\Users\ada\Projects` would raise; one that stripped the drive letter
    would produce a rule matching a DIFFERENT folder. `from_host_path` is the
    single conversion, and these pin that the rule it produces matches what the
    tool layer actually asks about.
    """

    WINDOWS = "C:\\Users\\ada\\Projects"

    def test_a_windows_grant_stops_the_windows_folder_from_asking(self) -> None:
        roots = (GrantedRoot.from_host_path(self.WINDOWS),)
        # The spelling a tool call carries once the translator has run.
        assert self.verdict("/C:/Users/ada/Projects", roots=roots) == "allow"
        assert self.verdict("/C:/Users/ada/Projects/notes.md", roots=roots) == "allow"

    def test_a_windows_grant_does_not_open_its_siblings(self) -> None:
        roots = (GrantedRoot.from_host_path(self.WINDOWS),)
        assert self.verdict("/C:/Users/ada/Downloads", roots=roots) == "interrupt"
        assert (
            self.verdict("/C:/Users/ada/ProjectsSecret/x", roots=roots) == "interrupt"
        )

    def test_a_windows_grant_does_not_open_the_posix_path_of_the_same_name(
        self,
    ) -> None:
        roots = (GrantedRoot.from_host_path(self.WINDOWS),)
        assert self.verdict("/Users/ada/Projects/notes.md", roots=roots) == "interrupt"

    def test_a_unc_grant_is_spelled_single_rooted(self) -> None:
        root = GrantedRoot.from_host_path("\\\\server\\share\\reports")
        assert root.path == "/UNC:/server/share/reports"
        assert not root.path.startswith("//")
        assert self.verdict(f"{root.path}/q4.csv", roots=(root,)) == "allow"

    def test_a_posix_grant_is_unchanged_by_the_conversion(self) -> None:
        assert GrantedRoot.from_host_path(GRANTED) == GrantedRoot(path=GRANTED)

    def test_a_windows_grant_gets_no_writable_scratch_inside_it(self) -> None:
        """D7 again, in the platform where this rule was easiest to get wrong.

        The `.copilot` lane sited the scratch at `<root>/.copilot` and had to
        spell it in the canonical POSIX encoding so the matcher could see it.
        That machinery is gone with the location: a Windows grant now opens no
        direct host write at all — not the scratch that used to live inside it,
        and not (as ever) the user's own content.

        Note WHICH layer each half is asserted against. `.copilot` is a hidden
        segment, so rule 5 cannot see it and the rule set has no verdict to
        give: the honest statement here is that no rule NAMES it, and the
        refusal itself belongs to `HostFilesystemFloor` (see
        `test_host_floor` and `test_workspace_effect_wiring`). Asserting a
        `deny` verdict for it would have been asserting the unmatched default.
        """

        root = GrantedRoot.from_host_path(self.WINDOWS)
        assert not any(
            DROPPED_SCRATCH_DIR in path
            for rule in HostFilesystemRules.build((root,))
            for path in rule["paths"]  # type: ignore[union-attr]
        )
        # A Windows grant reaches the SAME verdict as a POSIX one — that is the
        # parity this class exists to hold. `from_host_path` defaults to
        # writable, so the write inside it is allowed, and a sibling outside it
        # is not.
        assert (
            self.verdict(
                "/C:/Users/ada/Projects/out.csv", operation="write", roots=(root,)
            )
            == "allow"
        )
        assert (
            self.verdict(
                "/C:/Users/ada/Secrets/out.csv", operation="write", roots=(root,)
            )
            == "deny"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "C:\\Users\\ada\\..\\etc",
            "\\\\.\\PhysicalDrive0",
            "C:\\Users\\ada\\NUL",
            "C:relative",
            "~/Projects",
            "\\\\server",
            "/memories",
            "relative/dir",
        ],
    )
    def test_a_path_that_names_no_host_folder_is_never_granted(self, path: str) -> None:
        """An unusable grant must degrade to "still asks", never to a wider rule."""

        with pytest.raises(ValueError, match="not a host folder"):
            GrantedRoot.from_host_path(path)


class TestAttachedFolderStopsAsking(RuleSetMixin):
    """The user's question: once granted, does it still ask? It must not.

    Before granted roots were threaded through, `HostFilesystemRules.build` was
    called with `roots=()`, so the allow tier produced no rules at all and every
    read of an attached folder fell to the catch-all interrupt. Attaching a
    folder bought the user nothing. These pin that it now buys silence.
    """

    def test_reads_in_an_attached_folder_are_allowed_by_the_rules(self) -> None:
        """Named for what it actually observes.

        It was called `test_ls_in_an_attached_folder_does_not_prompt`, which is
        a claim it cannot make: whether `ls` prompts is decided by
        `_make_bulk_when_predicate`, not by `_check_fs_permission`. That
        predicate fires whenever the call's subtree overlaps an interrupt
        anchor, and rule 4's anchor is `/`, so `ls` over an ATTACHED folder does
        still ask today. Only the exact-scope tools (`read_file`) go silent.
        `test_host_floor.TestBulkOpsAreDelegatedBecauseConsentAlreadyFires` pins
        that behaviour against deepagents' own predicate.
        """

        roots = (GrantedRoot(path=GRANTED),)
        assert self.verdict(GRANTED, roots=roots) == "allow"
        assert self.verdict(f"{GRANTED}/sub/deep/file.txt", roots=roots) == "allow"

    def test_a_second_attached_folder_also_stops_asking(self) -> None:
        other = "/Users/ada/Reports"
        roots = (GrantedRoot(path=GRANTED), GrantedRoot(path=other))
        assert self.verdict(f"{GRANTED}/a.txt", roots=roots) == "allow"
        assert self.verdict(f"{other}/b.txt", roots=roots) == "allow"
        # ...and attaching two folders still does not open a third.
        assert self.verdict(UNGRANTED, roots=roots) == "interrupt"

    def test_a_writable_grant_opens_that_folder_and_only_that_folder(self) -> None:
        """Attaching writable opens a direct write path INSIDE the grant only.

        The boundary is the grant, not the operation: everything outside stays
        `deny` and is never even a question, so a widened grant cannot leak into
        a neighbour.
        """

        roots = (GrantedRoot(path=GRANTED, writable=True),)
        assert (
            self.verdict(f"{GRANTED}/out.csv", operation="write", roots=roots)
            == "allow"
        )
        assert (
            self.verdict(f"{UNGRANTED}/out.csv", operation="write", roots=roots)
            == "deny"
        )
        assert (
            self.verdict(f"{GRANTED}Secret/out.csv", operation="write", roots=roots)
            == "deny"
        )
        # The grant covers its whole subtree, and the scratch is a SEPARATE
        # allowance that needs no grant — so composing the two changes neither.
        scratch = AgentScratchRoot(Path("/Users/ada/.0xcopilot/.tmp"))
        assert (
            self.verdict(
                f"{GRANTED}/subdir/out.csv",
                operation="write",
                roots=roots,
                scratch=scratch,
            )
            == "allow"
        )
        assert (
            self.verdict(
                f"{UNGRANTED}/out.csv",
                operation="write",
                roots=roots,
                scratch=scratch,
            )
            == "deny"
        )
