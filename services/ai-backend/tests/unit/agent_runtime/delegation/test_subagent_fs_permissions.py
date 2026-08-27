"""``SubagentDefinition.fs_permissions``: validation, clamp, and composition.

Three layers, and the middle one is the whole point of this file.

* the Pydantic spec — what a definition may SAY. Deliberately permissive:
  "starts with ``/``, no ``..`` or ``~``" and nothing else, so
  ``allow read+write /**`` is a valid thing to write down;
* the clamp — what a definition GETS. ``allow /**`` is valid to say and must
  never be valid to hold, because a declared subagent is user-authored through
  ``PUT /v1/agent/subagents/{name}`` and deepagents treats a child's rule list
  as a REPLACEMENT for the parent's, not an intersection with it;
* the composition — what a real run actually attaches. That lives next door in
  ``test_subagent_fs_permission_composition``, which imports none of the clamp's
  names on purpose, so it states the DEFECT rather than the fix and fails on any
  tree where a definition can out-reach its run.

The old version of this file asserted only that a rule list was ATTACHED, and
said so in its own docstring ("Tests assert the rule list shape, not deepagents
internals"). Nine tests passed over a definition that could hand itself the
whole disk.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot
from agent_runtime.delegation.subagents.authority import (
    FilesystemGrant,
    SubagentAuthorityError,
    SubagentAuthorityPolicy,
)
from agent_runtime.delegation.subagents.contracts import (
    FilesystemPermissionSpec,
    SubagentDefinition,
    SubagentTransport,
)

from agent_runtime.execution.factory import (
    _host_filesystem_permissions,
    _subagents_with_fs_permissions,
)


#: The folder the user attached. Everything below is measured relative to it.
GRANTED = "/Users/ada/Projects"


def _definition(**overrides: object) -> SubagentDefinition:
    base: dict[str, object] = {
        "name": "competitive_research",
        "description": "Researches competitive positioning across owned material.",
        "graph_id": "graph_competitive",
        "transport": SubagentTransport.ASGI,
    }
    base.update(overrides)
    return SubagentDefinition.model_validate(base)


class ParentBoundaryMixin:
    """The real rule set a desktop run composes, for a run with one grant."""

    class _WorkspaceBackend:
        granted_roots = (GrantedRoot(path=GRANTED),)

    def parent_rules(self, *, writable: bool = True) -> tuple[object, ...]:
        return _host_filesystem_permissions(
            self._WorkspaceBackend(),
            granted_host_roots=(GrantedRoot(path=GRANTED, writable=writable),),
        )

    def attached(
        self, *specs: FilesystemPermissionSpec, parent: tuple[object, ...] | None = None
    ) -> list[object]:
        """The rules a definition declaring ``specs`` actually ends up holding."""

        rules = self.parent_rules() if parent is None else parent
        result = _subagents_with_fs_permissions(
            (_definition(fs_permissions=specs),), parent_rules=rules
        )
        return list(getattr(result[0], "permissions", None) or [])

    def survivors(
        self, *specs: FilesystemPermissionSpec, parent: tuple[object, ...] | None = None
    ) -> list[object]:
        """Only the definition-owned rules — the appended parent list trimmed off."""

        rules = self.parent_rules() if parent is None else parent
        attached = self.attached(*specs, parent=rules)
        if not attached:
            return []
        return attached[: len(attached) - len(rules)]


class TestFilesystemPermissionSpec:
    def test_default_empty(self) -> None:
        definition = _definition()
        assert definition.fs_permissions == ()

    def test_grants_drafts_write(self) -> None:
        spec = FilesystemPermissionSpec(
            operations=("read", "write"), paths=("/drafts/",), mode="allow"
        )
        definition = _definition(fs_permissions=(spec,))
        assert definition.fs_permissions[0].mode == "allow"
        assert definition.fs_permissions[0].paths == ("/drafts/",)
        assert "write" in definition.fs_permissions[0].operations

    def test_paths_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError):
            FilesystemPermissionSpec(
                operations=("write",), paths=("drafts/",), mode="allow"
            )

    def test_paths_reject_dotdot(self) -> None:
        with pytest.raises(ValueError):
            FilesystemPermissionSpec(
                operations=("write",), paths=("/drafts/../etc/",), mode="allow"
            )

    def test_paths_reject_tilde(self) -> None:
        with pytest.raises(ValueError):
            FilesystemPermissionSpec(
                operations=("write",), paths=("/~/secret/",), mode="allow"
            )

    def test_whole_disk_is_still_expressible(self) -> None:
        """The validator does NOT reject it — which is why the clamp exists.

        Pinned deliberately. If a future change makes this raise, the clamp is
        no longer the only thing standing between a definition and the disk,
        and whoever made that change should have to say so here.
        """

        spec = FilesystemPermissionSpec(
            operations=("read", "write", "execute"), paths=("/**",), mode="allow"
        )
        assert spec.paths == ("/**",)


class TestFactoryTranslation(ParentBoundaryMixin):
    def test_passthrough_when_no_permissions(self) -> None:
        definition = _definition()
        result = _subagents_with_fs_permissions(
            (definition,), parent_rules=self.parent_rules()
        )
        assert result[0] is definition
        assert getattr(result[0], "permissions", None) in (None, [])

    def test_attaches_permissions_when_specs_present(self) -> None:
        survivors = self.survivors(
            FilesystemPermissionSpec(
                operations=("read", "write"), paths=("/drafts/",), mode="allow"
            )
        )
        assert len(survivors) == 1
        # Translated to deepagents' FilesystemPermission dataclass.
        translated = survivors[0]
        assert translated.mode == "allow"
        assert translated.paths == ["/drafts/"]
        assert "write" in translated.operations

    def test_multiple_specs_translate_in_order(self) -> None:
        deny = FilesystemPermissionSpec(
            operations=("write",), paths=("/drafts/secret/",), mode="deny"
        )
        allow = FilesystemPermissionSpec(
            operations=("read", "write"), paths=("/drafts/",), mode="allow"
        )
        assert [rule.mode for rule in self.survivors(deny, allow)] == ["deny", "allow"]

    def test_empty_input_passthrough(self) -> None:
        assert _subagents_with_fs_permissions((), parent_rules=()) == ()


class TestClampToParentGrants(ParentBoundaryMixin):
    """What a definition may hold, measured against what the parent holds."""

    def test_whole_disk_allow_does_not_survive(self) -> None:
        """The finding, at the seam that produced it.

        Before the clamp this attached exactly one rule —
        ``allow read+write+execute /**`` — and it REPLACED the parent's six,
        losing rule 4's ``interrupt`` on every other read and rule 5's ``deny``
        on every other write.
        """

        assert (
            self.survivors(
                FilesystemPermissionSpec(
                    operations=("read", "write", "execute"),
                    paths=("/**",),
                    mode="allow",
                )
            )
            == []
        )

    def test_parent_boundary_is_appended_not_replaced(self) -> None:
        parent = self.parent_rules()
        attached = self.attached(
            FilesystemPermissionSpec(
                operations=("read", "write", "execute"), paths=("/**",), mode="allow"
            ),
            parent=parent,
        )
        assert [(rule.mode, tuple(rule.paths)) for rule in attached] == [
            (rule.mode, tuple(rule.paths)) for rule in parent
        ]

    def test_allow_inside_a_granted_root_survives_for_read(self) -> None:
        survivors = self.survivors(
            FilesystemPermissionSpec(
                operations=("read",), paths=(f"{GRANTED}/notes/**",), mode="allow"
            )
        )
        assert [(rule.mode, tuple(rule.paths)) for rule in survivors] == [
            ("allow", (f"{GRANTED}/notes/**",))
        ]

    def test_allow_one_level_above_a_granted_root_does_not_survive(self) -> None:
        """A grant on a subfolder cannot be widened into its parent folder."""

        assert (
            self.survivors(
                FilesystemPermissionSpec(
                    operations=("read",), paths=("/Users/ada/**",), mode="allow"
                )
            )
            == []
        )

    def test_write_inside_a_granted_root_defers_to_the_run_s_bypass_posture(
        self,
    ) -> None:
        """Manual makes the parent's write rule ``interrupt``, so the child asks.

        The definition asked for read AND write on ground the user attached.
        Read is allowed outright by the parent, so it survives; write is
        ``interrupt`` for the parent, so the child does not get a silent
        ``allow`` — it falls through to the appended parent rule and pauses on
        the same consent card the supervisor would.
        """

        survivors = self.survivors(
            FilesystemPermissionSpec(
                operations=("read", "write"), paths=(f"{GRANTED}/**",), mode="allow"
            )
        )
        assert [tuple(rule.operations) for rule in survivors] == [("read",)]

    def test_write_outside_every_grant_does_not_survive(self) -> None:
        assert (
            self.survivors(
                FilesystemPermissionSpec(
                    operations=("write",), paths=("/etc/**",), mode="allow"
                )
            )
            == []
        )

    def test_read_only_grant_yields_no_write(self) -> None:
        parent = self.parent_rules(writable=False)
        assert (
            self.survivors(
                FilesystemPermissionSpec(
                    operations=("write",), paths=(f"{GRANTED}/**",), mode="allow"
                ),
                parent=parent,
            )
            == []
        )

    def test_deny_always_survives_and_leads(self) -> None:
        """A definition tightening itself is the legitimate use, and is kept."""

        attached = self.attached(
            FilesystemPermissionSpec(
                operations=("read", "write"), paths=("/drafts/locked/**",), mode="deny"
            )
        )
        assert attached[0].mode == "deny"
        assert attached[0].paths == ["/drafts/locked/**"]

    def test_only_the_permitted_path_of_a_multi_path_rule_survives(self) -> None:
        survivors = self.survivors(
            FilesystemPermissionSpec(
                operations=("read",),
                paths=("/drafts/", "/etc/**"),
                mode="allow",
            )
        )
        assert [tuple(rule.paths) for rule in survivors] == [("/drafts/",)]

    def test_no_boundary_leaves_the_definition_untouched(self) -> None:
        """A hosted image composes no host rules; the supervisor is unrestricted.

        There is no ceiling to clamp to and no floor to append, so the child is
        returned exactly as declared. This is the ONE permissive branch, and it
        is reachable only by passing a measured-empty parent list.
        """

        survivors = self.survivors(
            FilesystemPermissionSpec(
                operations=("read",), paths=("/**",), mode="allow"
            ),
            parent=(),
        )
        assert [tuple(rule.paths) for rule in survivors] == [("/**",)]


class TestFilesystemGrantAdapter:
    def test_reads_mappings_and_objects_alike(self) -> None:
        from deepagents.middleware.filesystem import FilesystemPermission

        grants = FilesystemGrant.from_rules(
            (
                {"operations": ["read"], "paths": ["/a/**"], "mode": "allow"},
                FilesystemPermission(
                    operations=["write"], paths=["/b/**"], mode="deny"
                ),
                FilesystemPermissionSpec(
                    operations=("read",), paths=("/c/**",), mode="allow"
                ),
            )
        )
        assert [grant.mode for grant in grants] == ["allow", "deny", "allow"]
        assert [grant.paths for grant in grants] == [
            ("/a/**",),
            ("/b/**",),
            ("/c/**",),
        ]

    def test_unreadable_rule_raises_rather_than_being_skipped(self) -> None:
        """Skipping would be fail-open on the half that matters: a parent deny."""

        with pytest.raises(SubagentAuthorityError):
            FilesystemGrant.from_rules(({"paths": ["/a/**"], "mode": "deny"},))


class TestNarrowFsPermissions:
    """The pure clamp, stated in its own vocabulary."""

    PARENT = (
        FilesystemGrant(operations=("read", "write"), paths=("/drafts/**",)),
        FilesystemGrant(operations=("read",), paths=("/**",), mode="interrupt"),
        FilesystemGrant(operations=("write",), paths=("/**",), mode="deny"),
    )

    def test_definition_rules_precede_the_parent_list(self) -> None:
        result = SubagentAuthorityPolicy.narrow_fs_permissions(
            parent=self.PARENT,
            definition=(
                FilesystemGrant(operations=("read",), paths=("/drafts/a/**",)),
            ),
        )
        assert result[0].paths == ("/drafts/a/**",)
        assert result[1:] == self.PARENT

    def test_empty_definition_yields_the_parent_list(self) -> None:
        assert (
            SubagentAuthorityPolicy.narrow_fs_permissions(
                parent=self.PARENT, definition=()
            )
            == self.PARENT
        )

    def test_single_star_parent_rule_is_not_reasoned_about(self) -> None:
        """Fixed-depth patterns are refused rather than guessed at."""

        result = SubagentAuthorityPolicy.narrow_fs_permissions(
            parent=(FilesystemGrant(operations=("read",), paths=("/a/*",)),),
            definition=(FilesystemGrant(operations=("read",), paths=("/a/b",)),),
        )
        assert len(result) == 1  # the parent rule only

    def test_operation_the_parent_never_mentions_is_refused(self) -> None:
        """``execute`` appears in no host rule, so no definition may hold it."""

        result = SubagentAuthorityPolicy.narrow_fs_permissions(
            parent=self.PARENT,
            definition=(
                FilesystemGrant(operations=("execute",), paths=("/drafts/**",)),
            ),
        )
        assert result == self.PARENT

    def test_a_narrow_deny_cannot_be_stepped_around_by_asking_for_its_parent(
        self,
    ) -> None:
        """Overlap refuses; only containment permits.

        The parent denies ``/drafts/secret/**`` before it allows ``/drafts/**``.
        A child asking to allow all of ``/drafts/**`` is not contained by the
        deny, but it reaches into it — so the first rule whose ground meets the
        request decides, and it is a deny.
        """

        parent = (
            FilesystemGrant(
                operations=("read",), paths=("/drafts/secret/**",), mode="deny"
            ),
            FilesystemGrant(operations=("read",), paths=("/drafts/**",)),
        )
        result = SubagentAuthorityPolicy.narrow_fs_permissions(
            parent=parent,
            definition=(FilesystemGrant(operations=("read",), paths=("/drafts/**",)),),
        )
        assert result == parent

    @pytest.mark.parametrize(
        "parent",
        [
            pytest.param(PARENT, id="with-a-boundary"),
            pytest.param((), id="without-a-boundary"),
        ],
    )
    def test_a_non_empty_definition_never_narrows_to_nothing(
        self, parent: tuple[FilesystemGrant, ...]
    ) -> None:
        """The invariant the factory relies on instead of an unreachable branch.

        An empty rule list is the WIDEST answer deepagents has — its matcher
        returns ``allow`` when nothing matches — so "everything was refused"
        must never come back as ``()``. It comes back as the parent's own list,
        or, when there is no parent list, as the definition it was handed.
        """

        result = SubagentAuthorityPolicy.narrow_fs_permissions(
            parent=parent,
            definition=(FilesystemGrant(operations=("execute",), paths=("/nope/**",)),),
        )
        assert result
