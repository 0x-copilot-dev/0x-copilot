"""`$COPILOT_HOME/.tmp` — the layout, the naming rule, and the matching trap.

PRD-FS-12. Three things here are load-bearing enough to have their own class:

* **the dotted-segment trap (§5).** `.tmp` is hidden and deepagents matches
  under `wcmatch` with no `DOTGLOB`, so `**` cannot see it — and unmatched means
  ALLOW. That combination is what let `~/.ssh/id_rsa` through once already.
  Every assertion about the rule runs deepagents' own `_check_fs_permission` and
  `globmatch`, never a local reimplementation, because a regression here is
  invisible to any test that asserts our own predicate.
* **D4 — the directory is named by `conversation_id`, never the chat title.**
  Proved by feeding real titles in and requiring a raise, not a sanitised path.
* **D8 — no timer cleanup.** A negative decision, so it is pinned negatively:
  the module must expose no sweeper, no TTL and no age-out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _check_fs_permission,
)
from wcmatch import glob as wcglob

from agent_runtime.capabilities.desktop.agent_scratch import (
    COPILOT_HOME_ENV,
    SCRATCH_DIR_NAME,
    AgentScratchRoot,
    ScratchIdError,
    agent_scratch_root,
    copilot_home,
    safe_segment,
)
from agent_runtime.capabilities.desktop.host_filesystem import (
    GrantedRoot,
    HostFilesystemRules,
)
from agent_runtime.capabilities.desktop.host_floor import HostFilesystemFloor

CONVERSATION = "6f1f2a4c9b7d4e0fa1c2d3e4f5061728"
RUN = "run-01HQ2Z"
#: Deliberately doubly hidden, exactly like the real thing: `.0xcopilot/.tmp`.
#: A test that used a visible root would pass with a glob rule and prove nothing.
HIDDEN_HOME = "/Users/ada/.0xcopilot"


def _scratch(root: str = f"{HIDDEN_HOME}/{SCRATCH_DIR_NAME}") -> AgentScratchRoot:
    return AgentScratchRoot(Path(root))


def _rules(
    scratch: AgentScratchRoot | None, *roots: GrantedRoot
) -> list[FilesystemPermission]:
    """The REAL rule objects the factory hands deepagents."""

    return [
        FilesystemPermission(**rule)  # type: ignore[arg-type]
        for rule in HostFilesystemRules.build(roots, scratch=scratch)
    ]


def _matching_rule(
    rules: list[FilesystemPermission], operation: str, path: str
) -> FilesystemPermission | None:
    """The first rule deepagents MATCHES, or `None` when nothing matches.

    `_check_fs_permission` collapses "no rule matched" into `"allow"`, which is
    the whole trap: a test that reads its return value cannot tell a deliberate
    allow from the unmatched default. Anything under `$COPILOT_HOME` is affected
    — `.0xcopilot` is itself a dotted segment, so `/**` cannot see the app's own
    state directory either. Tests that care whether a RULE decided look here;
    the residue is decided by `HostFilesystemFloor` and asserted through
    `_effective_write`.
    """

    flags = wcglob.BRACE | wcglob.GLOBSTAR
    for rule in rules:
        if operation not in rule.operations:
            continue
        if any(wcglob.globmatch(path, pattern, flags=flags) for pattern in rule.paths):
            return rule
    return None


def _effective_write(
    scratch: AgentScratchRoot | None, path: str, *roots: GrantedRoot
) -> str:
    """The verdict the PRODUCTION stack actually reaches for a write.

    Composes both real layers the way `factory._host_default_backend` does —
    deepagents' rule check at the tool layer, and the floor beneath the real
    filesystem backend — because neither is total on its own and asserting
    either alone is how this program shipped a green suite over an open lane.
    """

    verdict = _check_fs_permission(_rules(scratch, *roots), "write", path)
    floor = HostFilesystemFloor(object(), roots=roots, scratch=scratch)
    if verdict == "allow" and not floor.permits_write(path):
        return "deny"
    return verdict


class TestTheDottedSegmentTrap:
    """§5 — the reason this rule is written with a literal path.

    Every assertion about the RULE goes through `_matching_rule`, never through
    `_check_fs_permission`'s return value. That is not pedantry: on today's
    doubly-hidden scratch path a broken rule and a working rule produce the
    SAME `"allow"`, because the unmatched default forges it. A mutation that
    replaced the literal with `/**/.tmp` passed every outcome-shaped assertion
    written here first — these are the ones that caught it.
    """

    SCRATCH = _scratch()
    FILE = f"{HIDDEN_HOME}/{SCRATCH_DIR_NAME}/{CONVERSATION}/{RUN}/tool-results/a.txt"

    def test_the_scratch_allow_is_a_decision_not_the_unmatched_default(self) -> None:
        """A RULE must match the doubly-hidden scratch path and say allow."""

        for operation in ("read", "write"):
            matched = _matching_rule(_rules(self.SCRATCH), operation, self.FILE)
            assert matched is not None, f"no rule matched for {operation}"
            assert matched.mode == "allow"

    def test_the_scratch_root_itself_is_covered_not_only_its_children(self) -> None:
        """`<root>/**` does not match `<root>` under GLOBSTAR.

        Hence the bare path in the same rule. (Whether `ls` *prompts* is a
        different question, decided by the bulk interrupt predicate rather than
        by an allow rule — `test_host_floor` pins that against deepagents.)
        """

        matched = _matching_rule(_rules(self.SCRATCH), "read", self.SCRATCH.posix)

        assert matched is not None
        assert matched.mode == "allow"

    def test_a_visible_scratch_is_kept_writable_by_this_rule_alone(self) -> None:
        """Where the rule is the ONLY thing standing between agent and deny.

        Rename `SCRATCH_DIR_NAME` to something visible — a plausible "why is
        this hidden?" edit — and rule 5 (`write` → deny on `/**`) starts
        matching the scratch. The before/after here is the whole justification
        for the rule existing on a configuration where the floor already covers
        the hidden case.
        """

        visible = _scratch("/Users/ada/copilot-state/tmp")
        target = f"{visible.posix}/{CONVERSATION}/notes.json"

        assert _check_fs_permission(_rules(None), "write", target) == "deny"
        assert _check_fs_permission(_rules(visible), "write", target) == "allow"

    def test_the_scratch_allow_precedes_the_catch_all_deny(self) -> None:
        """Ordering is the security property; first match wins.

        Also what survives an upstream `DOTGLOB`: the day `/**` starts matching
        hidden paths, rules 4 and 5 become total and would deny every scratch
        write unless this allow is ahead of them.
        """

        rules = _rules(self.SCRATCH)
        scratch_index = next(
            index
            for index, rule in enumerate(rules)
            if any(SCRATCH_DIR_NAME in path for path in rule.paths)
        )
        deny_index = next(
            index
            for index, rule in enumerate(rules)
            if rule.mode == "deny" and rule.paths == ["/**"]
        )

        assert scratch_index < deny_index

    def test_a_glob_catch_all_would_not_have_matched_it(self) -> None:
        """The trap itself, asserted against deepagents' own flags.

        `/**` — the pattern the catch-all rules use, and the obvious
        "simplification" for this one — cannot see a hidden segment at all. If
        this ever starts returning True, `DOTGLOB` arrived upstream and the
        floor's whole reason for existing should be revisited.
        """

        flags = wcglob.BRACE | wcglob.GLOBSTAR

        assert wcglob.globmatch(self.FILE, "/**", flags=flags) is False
        assert wcglob.globmatch("/Users/ada/.ssh/id_rsa", "/**", flags=flags) is False
        # ...while a VISIBLE path is matched, which is why the hole is silent.
        assert (
            wcglob.globmatch("/Users/ada/Downloads/a.csv", "/**", flags=flags) is True
        )

    def test_a_wildcard_anywhere_before_the_scratch_would_break_it(self) -> None:
        """Pins the shape, not just the outcome.

        The outcome test above passes for a rule that happens to match today;
        this one fails the moment a `*` is introduced anywhere ahead of `.tmp`,
        which is the edit that silently reintroduces the hole.
        """

        paths = [path for rule in self.SCRATCH.allow_rules() for path in rule["paths"]]
        assert paths, "the scratch must contribute at least one rule path"
        for path in paths:
            head = path[: -len("/**")] if path.endswith("/**") else path
            assert "*" not in head, f"wildcard ahead of the scratch: {path!r}"
            assert head.endswith(f"/{SCRATCH_DIR_NAME}"), path

    def test_a_home_directory_with_glob_characters_stays_literal(self) -> None:
        """`escape` is why a `[`/`{` in the user's path is not a wildcard.

        Unescaped, `a[d]a{x,y}` is a character class plus brace alternation —
        it matches the literal directory `adax`, and does NOT match the actual
        home it was built from. So dropping `escape` both opens a folder that
        was never the scratch and closes the one that was.
        """

        weird = _scratch(f"/Users/a[d]a{{x,y}}/.0xcopilot/{SCRATCH_DIR_NAME}")
        mine = f"{weird.posix}/{CONVERSATION}/meta.json"
        # The tree the UNESCAPED pattern would expand to — a real directory
        # belonging to nobody in particular, and not the agent's scratch.
        stranger = f"/Users/adax/.0xcopilot/{SCRATCH_DIR_NAME}/{CONVERSATION}/meta.json"
        rules = _rules(weird)

        matched = _matching_rule(rules, "write", mine)
        assert matched is not None and matched.mode == "allow"
        assert _matching_rule(rules, "write", stranger) is None


class TestScopedToTheScratchAndNothingAbove:
    """§5 — "the allow is scoped to `$COPILOT_HOME/.tmp` and nothing above it".

    `COPILOT_HOME` is itself dotted (`~/.0xcopilot`), so EVERYTHING under it is
    matcher-blind and the rule set is structurally unable to protect the app's
    own state directory. That is not a defect in this rule; it is the same
    upstream fact `host_floor` exists for. So each case below is checked twice:
    that no rule ALLOWS it (`_matching_rule`), and that the composed stack
    refuses it (`_effective_write`).
    """

    SCRATCH = _scratch()

    @pytest.mark.parametrize(
        "path",
        [
            f"{HIDDEN_HOME}/.copilot-version",
            f"{HIDDEN_HOME}/runtime/darwin-arm64/python/bin/python",
            f"{HIDDEN_HOME}/{SCRATCH_DIR_NAME}-evil/x",
            f"{HIDDEN_HOME}/{SCRATCH_DIR_NAME}x/x",
            "/Users/ada/.ssh/id_rsa",
            "/Users/ada/.aws/credentials",
        ],
    )
    def test_a_write_outside_the_scratch_is_never_allowed(self, path: str) -> None:
        matched = _matching_rule(_rules(self.SCRATCH), "write", path)

        assert matched is None or matched.mode != "allow", path
        assert _effective_write(self.SCRATCH, path) != "allow", path

    def test_the_staged_runtime_beside_the_scratch_stays_untouchable(self) -> None:
        """The sibling that makes "nothing above it" matter.

        `$COPILOT_HOME` also holds the staged Python runtime, Postgres and the
        version marker the supervisor boots from. An allow anchored one segment
        higher would let the model rewrite the app it is running inside.
        """

        runtime = f"{HIDDEN_HOME}/runtime/darwin-arm64/python/bin/python"

        assert _effective_write(self.SCRATCH, runtime) != "allow"
        assert (
            HostFilesystemFloor(object(), scratch=self.SCRATCH).permits_write(runtime)
            is False
        )

    def test_the_scratch_allow_does_not_widen_an_UNGRANTED_folder(self) -> None:
        """The scratch is its own allowance and grants nothing beyond itself.

        A writable grant is writable on its own merits (see
        `test_host_filesystem`); what must never happen is the SCRATCH rule
        leaking write access to somewhere the user never attached.

        The grant's own write reads `interrupt` here rather than `allow` only
        because these rules are built under the default MANUAL posture, where
        each write inside a grant asks. That is incidental to this test — the
        assertion that carries weight is the third one.
        """

        granted = GrantedRoot(path="/Users/ada/Projects", writable=True)
        rules = _rules(self.SCRATCH, granted)

        assert _check_fs_permission(rules, "read", f"{granted.path}/a.md") == "allow"
        assert (
            _effective_write(self.SCRATCH, f"{granted.path}/a.md", granted)
            == "interrupt"
        )
        assert (
            _effective_write(self.SCRATCH, "/Users/ada/Downloads/a.md", granted)
            == "deny"
        )

    def test_the_scratch_is_writable_with_zero_grants(self) -> None:
        """The point of moving it out of a granted folder (D7).

        `.copilot` needed a writable grant to exist. The agent now has a place
        to work on a first run where the user has attached nothing.
        """

        target = f"{self.SCRATCH.posix}/{CONVERSATION}/notes.json"
        matched = _matching_rule(_rules(self.SCRATCH), "write", target)

        assert matched is not None and matched.mode == "allow"
        assert _effective_write(self.SCRATCH, target) == "allow"

    def test_no_scratch_means_no_host_write_anywhere(self) -> None:
        """An unusable scratch degrades toward closed, never toward open."""

        granted = GrantedRoot(path="/Users/ada/Projects", writable=True)
        target = f"{self.SCRATCH.posix}/{CONVERSATION}/notes.json"

        assert _effective_write(None, "/Users/ada/x.txt", granted) == "deny"
        assert _effective_write(None, target, granted) == "deny"


class TestNamedByIdNeverByTitle:
    """D4 — a title must not be able to become a path segment."""

    @pytest.mark.parametrize(
        "title",
        [
            "Q3 plan for Acme Corp",
            "Sarah's onboarding",
            "notes/for/legal",
            "../../etc/passwd",
            "..",
            ".hidden",
            "",
            "café ☕",
            "trailing space ",
            "a" * 200,
        ],
    )
    def test_a_title_shaped_value_is_refused_not_sanitised(self, title: str) -> None:
        """Refusing beats sanitising: two titles could normalise to one dir."""

        with pytest.raises(ScratchIdError):
            safe_segment(title, kind="conversation_id")

    @pytest.mark.parametrize(
        "identifier",
        [CONVERSATION, RUN, "conv_1", "conv-1.2", "a", "0", "run.2026-07-31"],
    )
    def test_an_opaque_identifier_is_accepted_verbatim(self, identifier: str) -> None:
        assert safe_segment(identifier) == identifier

    def test_the_refusal_never_echoes_the_rejected_value(self) -> None:
        """The value may be user content; this module exists to keep it out of logs."""

        secret = "Acme Corp merger with Initech"
        with pytest.raises(ScratchIdError) as excinfo:
            safe_segment(secret, kind="conversation_id")

        assert secret not in str(excinfo.value)
        assert "conversation_id" in str(excinfo.value)

    def test_the_conversation_directory_is_the_id_itself(self, tmp_path: Path) -> None:
        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)

        assert conversation.path.name == CONVERSATION
        assert conversation.path.parent == tmp_path

    def test_a_run_directory_is_the_run_id_itself(self, tmp_path: Path) -> None:
        run = AgentScratchRoot(tmp_path).conversation(CONVERSATION).run(RUN)

        assert run.path.name == RUN
        with pytest.raises(ScratchIdError):
            AgentScratchRoot(tmp_path).conversation(CONVERSATION).run("Run 1 (retry)")


class TestWhereItLives:
    """D3 — `$COPILOT_HOME/.tmp`, and `COPILOT_HOME` is `~/.0xcopilot`."""

    def test_the_default_is_the_users_home_not_the_install_directory(self) -> None:
        """An upgrade replaces the install dir, and it may be read-only."""

        home = copilot_home(env={})

        assert home == Path.home() / ".0xcopilot"
        assert home.is_absolute()

    def test_an_explicit_copilot_home_wins(self, tmp_path: Path) -> None:
        home = copilot_home(env={COPILOT_HOME_ENV: str(tmp_path / "elsewhere")})

        assert home == tmp_path / "elsewhere"

    def test_an_empty_copilot_home_is_treated_as_unset(self) -> None:
        """A shell exporting it blank must not resolve the scratch to `/`.

        Mirrors `tools/cli/lib/paths.mjs`, which applies the same guard.
        """

        assert copilot_home(env={COPILOT_HOME_ENV: "   "}) == Path.home() / ".0xcopilot"

    def test_the_scratch_is_the_dotted_child_of_copilot_home(
        self, tmp_path: Path
    ) -> None:
        root = agent_scratch_root(env={COPILOT_HOME_ENV: str(tmp_path)})

        assert root.path == tmp_path / ".tmp"
        assert SCRATCH_DIR_NAME == ".tmp"


class TestTheLayout:
    """§3 — what is rooted at the run, what at the conversation, what neither."""

    def test_drafts_are_conversation_scoped_not_run_scoped(
        self, tmp_path: Path
    ) -> None:
        """A draft outlives the run that started it."""

        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)

        assert conversation.drafts == conversation.path / "drafts"
        assert conversation.run(RUN).path not in conversation.drafts.parents

    def test_tool_results_and_subagents_are_run_scoped(self, tmp_path: Path) -> None:
        """Both describe THIS run's calls and mean nothing detached from it."""

        run = AgentScratchRoot(tmp_path).conversation(CONVERSATION).run(RUN)

        assert run.tool_results == tmp_path / CONVERSATION / RUN / "tool-results"
        assert run.subagents == tmp_path / CONVERSATION / RUN / "subagents"

    def test_memories_policies_and_skills_are_not_here(self, tmp_path: Path) -> None:
        """ "Memory that dies with a run is not memory" — §3.

        They are USER-scoped and already file-backed by
        `runtime_adapters.file.FileMemoryBackendFactory`. A `.tmp` attribute for
        any of them would mean someone re-rooted them at the wrong lifetime.
        """

        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)

        for name in ("memories", "policies", "skills", "memory"):
            assert not hasattr(conversation, name)
        conversation.provision()
        # ``mcp`` belongs here for the opposite reason: the connector catalog is
        # rebuilt from the registry on every run, so it IS disposable — but it
        # must outlive the RUN, because the harness is rebuilt per run and again
        # on approval resume.
        assert sorted(child.name for child in conversation.path.iterdir()) == [
            "drafts",
            "mcp",
            "meta.json",
        ]

    def test_provision_is_idempotent_and_creates_the_run_tier_on_demand(
        self, tmp_path: Path
    ) -> None:
        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)
        conversation.provision()
        conversation.provision()

        run = conversation.run(RUN).provision()

        assert run.tool_results.is_dir()
        assert run.subagents.is_dir()


class TestMetaCarriesTheNameThePathCannot:
    """D5 — the opaque id names the path; `meta.json` carries the meaning."""

    TITLE = "Q3 plan for Acme Corp"

    def test_the_title_is_inside_the_file_and_nowhere_in_the_path(
        self, tmp_path: Path
    ) -> None:
        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)
        conversation.provision(title=self.TITLE)

        document = json.loads(conversation.meta_path.read_text())
        assert document["title"] == self.TITLE
        assert document["conversation_id"] == CONVERSATION
        # DoD: "No chat title appears in any path".
        for path in conversation.path.rglob("*"):
            assert self.TITLE not in str(path)
        assert self.TITLE not in str(conversation.meta_path)

    def test_a_rename_rewrites_the_metadata_and_never_the_path(
        self, tmp_path: Path
    ) -> None:
        """Titles change; the directory must not."""

        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)
        conversation.provision(title=self.TITLE)
        before = conversation.path

        conversation.provision(title="Renamed")

        assert conversation.path == before
        assert json.loads(conversation.meta_path.read_text())["title"] == "Renamed"

    def test_an_enormous_title_cannot_inflate_the_scratch(self, tmp_path: Path) -> None:
        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)
        conversation.provision(title="x" * 100_000)

        assert len(json.loads(conversation.meta_path.read_text())["title"]) == 512

    def test_extra_orientation_is_carried_without_displacing_the_id(
        self, tmp_path: Path
    ) -> None:
        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)
        conversation.provision(title=None, conversation_id="spoofed", project="atlas")

        document = json.loads(conversation.meta_path.read_text())
        assert document["conversation_id"] == CONVERSATION
        assert document["project"] == "atlas"
        assert document["title"] is None


class TestDeletionCascades:
    """D6 — deleting a chat deletes its `.tmp` directory."""

    def test_deleting_removes_the_whole_subtree(self, tmp_path: Path) -> None:
        conversation = AgentScratchRoot(tmp_path).conversation(CONVERSATION)
        conversation.provision(title="whatever")
        run = conversation.run(RUN).provision()
        (run.tool_results / "big.txt").write_text("offloaded")

        assert conversation.delete() is True
        assert not conversation.path.exists()

    def test_deleting_one_chat_leaves_its_neighbours_alone(
        self, tmp_path: Path
    ) -> None:
        root = AgentScratchRoot(tmp_path)
        doomed = root.conversation(CONVERSATION).provision()
        kept = root.conversation("aaaabbbbccccdddd").provision()

        doomed.delete()

        assert not doomed.path.exists()
        assert kept.meta_path.is_file()

    def test_deleting_a_chat_that_never_wrote_anything_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        """`.tmp/<conv>/` is created on first NEED, so it often does not exist."""

        assert AgentScratchRoot(tmp_path).conversation(CONVERSATION).delete() is False


class TestNoTimerCleanup:
    """D8 — nothing ages out, nothing is size-capped. Pinned negatively."""

    def test_the_scratch_exposes_no_retention_machinery(self) -> None:
        """A sweeper API appearing here means D8 was reversed without a decision."""

        from agent_runtime.capabilities.desktop import agent_scratch

        forbidden = ("ttl", "max_age", "retention", "sweep", "expire", "prune", "gc")
        names = [name.lower() for name in dir(agent_scratch)]
        for name in names:
            assert not any(word in name for word in forbidden), name

    def test_the_only_deletion_verb_is_the_per_conversation_one(
        self, tmp_path: Path
    ) -> None:
        root = AgentScratchRoot(tmp_path)

        assert hasattr(root.conversation(CONVERSATION), "delete")
        assert not hasattr(root, "delete")
        assert not hasattr(root.conversation(CONVERSATION).run(RUN), "delete")
