"""Authoritative narrowing rules for delegated subagent work.

Subagent definitions describe the *maximum* capability envelope a task may
receive.  A parent grant is an independent ceiling.  The child always gets the
intersection of the two plus any explicit request; it can never recover a
tool, scope, skill, capability, or approval posture that one of those parents
withheld.

The permission floor, stated so nobody has to infer it
-----------------------------------------------------

**A child's posture is floored at its parent's and may only be stricter.**
Concretely, for every axis:

* tools, skills, scopes, capabilities — set intersection with the parent grant.
  A definition naming a tool the parent lacks does not conjure it.
* approval policy (read / write / destructive) — the *stricter* of parent and
  definition, per :meth:`SubagentPolicyGrant.narrow`. A definition may tighten
  its own posture and that wins; it may not loosen and have it stick.
* filesystem rules — the parent's rule list is the ceiling and the floor, per
  :meth:`SubagentAuthorityPolicy.narrow_fs_permissions`. A definition-owned
  ``allow`` survives only where the parent already allows the same operation on
  the same ground; a ``deny`` always survives, because a deny only tightens.

The filesystem axis is the newest and was the one missing, so it is worth
saying why it needed a rule of its own rather than another set intersection.
Deep Agents resolves a subagent's ``permissions`` as ``spec.get("permissions",
permissions)`` — a *replacement*, not an intersection — and its matcher answers
``allow`` when NO rule matches. Those two facts compose into the opposite of a
ceiling: a child rule list is a widening device by construction, because
everything it fails to mention becomes unmatched, and unmatched means allow.
``narrow_fs_permissions`` is what converts that replacement back into an
intersection, and it does so structurally — the surviving child rules are a
PREFIX and the parent's whole list is appended behind them, so every path the
child did not name still meets the parent's own ordered verdict.

Two consequences a reader routinely gets backwards:

1. **"Floored at the parent's" is not "inherits the parent's".** It is an upper
   bound, not an assignment. A stricter definition still wins.
2. **A bypass does not cross the delegation boundary.** The user approves a
   bypass posture for the work they can see the agent doing. Handing it to a
   fan-out of delegates turns one consent into N unattended ones, which is
   exactly how a bypass grant silently widens. :meth:`delegable_parent_grant`
   clamps a bypassing parent back to the asking posture *before* narrowing, so
   the child asks even though the parent did not.

The one place the floor used to leak is :meth:`inherited_parent_grant`, which
fabricates a parent ceiling when no verified grant was supplied. It defaulted
the approval posture instead of carrying the parent's, so a *strict* parent
could be widened by nothing more than the absence of an explicit grant.

Closing that took two halves, and the second is the one worth pointing at: the
parameter (``parent_policy``) is useless unless the caller passes it.
:meth:`SubagentHandoffPolicy.narrow_authority` — this module's only caller —
resolves the parent's real posture from the run context through
``ToolUsePolicyResolver`` and feeds it in, so the fallback ceiling is the
parent's own policy rather than the deployment default. A test that calls
``inherited_parent_grant(parent_policy=...)`` directly proves the parameter
works and proves nothing about the leak; the test that matters enters at
``narrow_authority``.

Which lane this is, stated plainly so nobody mistakes it for the live one
------------------------------------------------------------------------

**This narrowing lane is not yet on the path a model run takes.** The live
delegation path is Deep Agents' ``task`` tool
(``delegation.subagents.atlas_task_tool``); it never calls
``SubagentHandoffPolicy``. The only caller of ``narrow_authority`` is
``SubagentHandoffBuilder``'s task builder, whose only caller is the
``DelegationCoordinator`` planner — which has no product caller at all
(recorded in ``tools/dark_wiring_baseline.txt`` and ``PENDING-WIRINGS.md``). So
the rules above are the *designed* floor, correct and tested, waiting on the
coordinator being wired.

What actually bounds a delegate today is structural rather than postural, and
lives in ``delegation.subagents.recursion``: rule 1 refuses a too-deep ``task``
call, and rule 2 removes ``task`` from the child's tool surface. A child's
approval posture on the live path is the parent's, because Deep Agents hands a
tool-less subagent spec the parent's *already policy-wrapped* tool objects
(``execution.factory`` applies ``ToolUsePolicyEnforcer`` before the subagents
are built) — equal to the parent, never looser, but also not clamped, so the
bypass carve-out below is not yet enforced live. That is the gap to close when
the coordinator lands; it is deliberately not faked here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import ClassVar, Final, Literal

from pydantic import Field, field_validator

from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.validation import ValueNormalizer


class SubagentAuthorityError(ValueError):
    """The supplied child handoff attempted to escape verified authority."""


class SubagentPolicyGrant(RuntimeContract):
    """The three approval-policy ceilings that a delegated task must obey."""

    read: ToolUsePolicyMode = ToolUsePolicyMode.AUTO
    write: ToolUsePolicyMode = ToolUsePolicyMode.ASK
    destructive: ToolUsePolicyMode = ToolUsePolicyMode.REQUIRE

    _RANK: ClassVar[dict[ToolUsePolicyMode, int]] = {
        ToolUsePolicyMode.AUTO: 0,
        ToolUsePolicyMode.ASK: 1,
        ToolUsePolicyMode.REQUIRE: 2,
        ToolUsePolicyMode.BLOCK: 3,
    }

    @classmethod
    def narrow(
        cls,
        parent: "SubagentPolicyGrant",
        definition: "SubagentPolicyGrant",
    ) -> "SubagentPolicyGrant":
        """Keep the more restrictive value for every approval-policy axis."""

        def stricter(
            left: ToolUsePolicyMode, right: ToolUsePolicyMode
        ) -> ToolUsePolicyMode:
            return left if cls._RANK[left] >= cls._RANK[right] else right

        return cls(
            read=stricter(parent.read, definition.read),
            write=stricter(parent.write, definition.write),
            destructive=stricter(parent.destructive, definition.destructive),
        )

    @classmethod
    def from_policy_snapshot(
        cls, snapshot: ToolUsePolicySnapshot
    ) -> "SubagentPolicyGrant":
        """Project the run's resolved tool-use policy onto the three grant axes.

        This is the parent's *real* posture — the workspace default composed
        with the user's override, the same snapshot the tool gate enforces for
        the parent's own calls. Reading the ceiling from there is what makes
        "floored at the parent's" a fact rather than an aspiration: without it
        the fallback ceiling is a fresh ``SubagentPolicyGrant()``, i.e. the
        deployment default, which is a *widening* for any parent stricter than
        the default.

        A run with no stored policy resolves to exactly those defaults, so the
        ordinary path is unchanged; only a parent that actually tightened its
        posture sees a difference.
        """

        return cls(
            read=snapshot.mode_for_kind(ToolUsePolicyKind.READ),
            write=snapshot.mode_for_kind(ToolUsePolicyKind.WRITE),
            destructive=snapshot.mode_for_kind(ToolUsePolicyKind.DESTRUCTIVE),
        )

    def mode_for(self, kind: ToolUsePolicyKind) -> ToolUsePolicyMode:
        return {
            ToolUsePolicyKind.READ: self.read,
            ToolUsePolicyKind.WRITE: self.write,
            ToolUsePolicyKind.DESTRUCTIVE: self.destructive,
        }[kind]


#: Characters ``wcmatch`` reads as pattern syntax under the flags Deep Agents
#: matches with (``BRACE | GLOBSTAR``). A path segment containing any of them is
#: where a pattern stops being a literal prefix and starts being a guess.
_GLOB_SYNTAX: Final[frozenset[str]] = frozenset("*?[]{}!")

_ALLOW: Final = "allow"
_RULE_FIELDS: Final[tuple[str, ...]] = ("operations", "paths", "mode")


class FilesystemGrant(RuntimeContract):
    """One rule of a filesystem boundary, in this domain's own vocabulary.

    A structural mirror of the two rule shapes this module has to compare:
    Deep Agents' ``FilesystemPermission`` dataclass (what the parent run's
    boundary is made of) and ``SubagentDefinition.fs_permissions``'
    ``FilesystemPermissionSpec`` (what a declared child asks for). Adapting both
    into one type here is what lets the clamp be a single pure function instead
    of one written twice, and it keeps Deep Agents out of the domain — the same
    reason ``capabilities.desktop.host_filesystem`` emits plain dicts.

    ``mode`` carries all three Deep Agents verdicts, not the two a definition
    may express: the parent's list contains ``interrupt`` rules, and a clamp
    that could not read them would mistake "ask the user" for "no opinion".
    """

    operations: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    mode: Literal["allow", "deny", "interrupt"] = _ALLOW

    @classmethod
    def from_rules(cls, rules: Iterable[object]) -> tuple["FilesystemGrant", ...]:
        """Adapt rule-shaped objects or mappings into grants, or refuse.

        Accepts anything carrying ``operations`` / ``paths`` / ``mode``, whether
        as attributes (Deep Agents' dataclass, ``FilesystemPermissionSpec``) or
        as keys (the dicts ``HostFilesystemRules.build`` emits).

        A rule it cannot read raises rather than being skipped. Skipping would
        be fail-open on the half that matters most: an unreadable rule in the
        PARENT list is a restriction, and dropping it silently would widen the
        very ceiling this type exists to measure.
        """

        return tuple(
            cls(
                operations=tuple(str(item) for item in _rule_field(rule, "operations")),
                paths=tuple(str(item) for item in _rule_field(rule, "paths")),
                mode=_rule_field(rule, "mode"),  # type: ignore[arg-type]
            )
            for rule in rules
        )


class SubagentCapabilityGrant(RuntimeContract):
    """Verified parent or effective child capability ceiling.

    An empty set is a real deny-all ceiling, not a wildcard.  Callers that
    intentionally inherit a definition must construct a grant from that
    definition through :class:`SubagentAuthorityPolicy`.
    """

    capabilities: frozenset[str] = Field(default_factory=frozenset)
    tools: frozenset[str] = Field(default_factory=frozenset)
    skills: frozenset[str] = Field(default_factory=frozenset)
    permission_scopes: frozenset[str] = Field(default_factory=frozenset)
    policy: SubagentPolicyGrant = Field(default_factory=SubagentPolicyGrant)

    @field_validator("capabilities", "tools", "skills", mode="before")
    @classmethod
    def _normalize_slug_set(cls, value: object) -> frozenset[str]:
        return frozenset(
            ValueNormalizer.normalize_slug(item, "subagent_capability_grant")
            for item in _iterable(value)
        )

    @field_validator("permission_scopes", mode="before")
    @classmethod
    def _normalize_scope_set(cls, value: object) -> frozenset[str]:
        return frozenset(
            ValueNormalizer.normalize_scope(item, "subagent_permission_scopes")
            for item in _iterable(value)
        )


class SubagentAuthorityPolicy:
    """Pure intersection and identity checks shared by handoff and lifecycle."""

    DISPATCH_CAPABILITY = "subagent"

    #: The loosest posture a parent may hand across the delegation boundary:
    #: reads flow, writes ask, destructive work requires explicit approval.
    #: This is ``SubagentPolicyGrant``'s own default, named here so the clamp
    #: below reads as a rule rather than as a convenient coincidence.
    NON_DELEGABLE_BYPASS_FLOOR: ClassVar[SubagentPolicyGrant] = SubagentPolicyGrant()

    @classmethod
    def inherited_parent_grant(
        cls,
        *,
        context_scopes: frozenset[str],
        definition_tools: frozenset[str],
        definition_skills: frozenset[str],
        parent_policy: SubagentPolicyGrant | None = None,
    ) -> SubagentCapabilityGrant:
        """Build the compatibility parent ceiling when no explicit grant exists.

        This is deliberately the definition's own ceiling, not an unbounded
        wildcard.  Existing callers retain their behavior while new assembly
        points can pass a narrower verified parent grant.

        ``parent_policy`` is the parent's real approval posture. Omitting it
        used to substitute the *default* posture, which silently widened a
        parent that was stricter than the default — the leak named in this
        module's header.
        """

        return SubagentCapabilityGrant(
            capabilities=frozenset({cls.DISPATCH_CAPABILITY}),
            tools=definition_tools,
            skills=definition_skills,
            permission_scopes=context_scopes,
            policy=parent_policy or SubagentPolicyGrant(),
        )

    @classmethod
    def delegable_parent_grant(
        cls,
        parent: SubagentCapabilityGrant,
        *,
        bypass_active: bool,
    ) -> SubagentCapabilityGrant:
        """Clamp a bypassing parent to the asking posture before it delegates.

        A bypass removes the approval pause for the work the user is watching.
        It is not a grant the parent may pass on: a delegate runs unattended,
        and N delegates turn one consent into N. When the parent holds no
        bypass this returns the grant unchanged, so the ordinary path allocates
        nothing and behaves exactly as before.
        """

        if not bypass_active:
            return parent
        return parent.model_copy(
            update={
                "policy": SubagentPolicyGrant.narrow(
                    parent.policy, cls.NON_DELEGABLE_BYPASS_FLOOR
                )
            }
        )

    @classmethod
    def narrow(
        cls,
        *,
        parent: SubagentCapabilityGrant,
        definition_tools: frozenset[str],
        definition_skills: frozenset[str],
        definition_allowed_scopes: frozenset[str],
        definition_policy: SubagentPolicyGrant,
        requested_tools: Iterable[str],
        requested_skills: Iterable[str],
        context_scopes: frozenset[str],
    ) -> SubagentCapabilityGrant:
        """Compute a fail-closed child grant with no widening path."""

        requested_tool_set = _requested_or_configured(requested_tools, definition_tools)
        requested_skill_set = _requested_or_configured(
            requested_skills, definition_skills
        )
        scope_ceiling = (
            definition_allowed_scopes
            if definition_allowed_scopes
            else parent.permission_scopes
        )
        return SubagentCapabilityGrant(
            capabilities=parent.capabilities.intersection({cls.DISPATCH_CAPABILITY}),
            tools=parent.tools.intersection(definition_tools, requested_tool_set),
            skills=parent.skills.intersection(definition_skills, requested_skill_set),
            permission_scopes=parent.permission_scopes.intersection(
                context_scopes, scope_ceiling
            ),
            policy=SubagentPolicyGrant.narrow(parent.policy, definition_policy),
        )

    @classmethod
    def narrow_fs_permissions(
        cls,
        *,
        parent: Sequence[FilesystemGrant],
        definition: Sequence[FilesystemGrant],
    ) -> tuple[FilesystemGrant, ...]:
        """Intersect a declared child's filesystem rules with its parent's.

        The result is the child's COMPLETE effective rule list, ordered: the
        surviving definition-owned rules first, then the parent's list verbatim.
        Both halves are load-bearing.

        * The surviving child rules go first because Deep Agents' matcher
          returns the FIRST match, so a rule appended after the parent's
          catch-alls would decide nothing.
        * The parent's list is appended because a child's ``permissions``
          REPLACES the parent's rather than composing with it, and the matcher
          answers ``allow`` for a path no rule mentions. Without the append, a
          child list saying "allow me ``/drafts/``" would silently also say
          "and everything else is unmatched, therefore permitted" — losing the
          parent's ``interrupt`` on every other read and its ``deny`` on every
          other write. That is a widening dressed as a narrowing, and it is
          what made this axis a bypass.

        Which child rules survive:

        * ``deny`` and ``interrupt`` — always. They can only tighten, and a
          definition tightening itself is the whole legitimate use.
        * ``allow`` — only for the (path, operation) pairs the parent already
          allows. "Already allows" is the parent's own first-match verdict, read
          from its ordered list, so a granted root that is read-allow and
          write-interrupt hands the child exactly that split.

        An empty ``parent`` means the run has no filesystem boundary at all —
        the hosted images, where ``_host_filesystem_permissions`` returns
        nothing and the supervisor itself is unrestricted over a virtual-only
        backend. That is a measured fact about the run, not an unmeasured one,
        and the child is returned unchanged because there is no ceiling to clamp
        to and none to append. Callers must therefore always pass the parent's
        real list; a defaulted empty one would turn "nobody asked" into "no
        limits", which is the failure mode this method exists to prevent.
        """

        if not definition:
            return tuple(parent)
        if not parent:
            return tuple(definition)
        narrowed: list[FilesystemGrant] = []
        for rule in definition:
            if rule.mode != _ALLOW:
                narrowed.append(rule)
                continue
            # Split per path: one spec may name several paths, and the parent's
            # verdict differs between them (a read-only grant and a writable one
            # can both be named by a single child rule). Emitting one narrowed
            # rule per surviving path is the only shape that can say so.
            for path in rule.paths:
                operations = tuple(
                    operation
                    for operation in rule.operations
                    if _parent_allows(parent, path=path, operation=operation)
                )
                if operations:
                    narrowed.append(
                        rule.model_copy(
                            update={"paths": (path,), "operations": operations}
                        )
                    )
        return tuple(narrowed) + tuple(parent)

    @staticmethod
    def require_same_tenant(
        *,
        expected_user_id: str,
        expected_org_id: str,
        expected_trace_id: str,
        received_user_id: str,
        received_org_id: str,
        received_trace_id: str,
    ) -> None:
        """Reject a handoff whose context was not minted from this parent run."""

        if (
            expected_user_id != received_user_id
            or expected_org_id != received_org_id
            or expected_trace_id != received_trace_id
        ):
            raise SubagentAuthorityError("subagent context does not match parent run")


def _iterable(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ValueError("subagent capability grants must be iterables, not strings")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("subagent capability grants must be iterables") from exc


def _rule_field(rule: object, name: str) -> object:
    """Read one field off a rule-shaped mapping or object, or refuse."""

    if isinstance(rule, Mapping):
        if name in rule:
            return rule[name]
    else:
        try:
            return getattr(rule, name)
        except AttributeError:
            pass
    raise SubagentAuthorityError(
        f"filesystem rule is missing {name!r} and cannot be narrowed: {rule!r}"
    )


def _parent_allows(
    parent: Sequence[FilesystemGrant], *, path: str, operation: str
) -> bool:
    """Does the parent's ordered list allow ``operation`` everywhere ``path`` reaches?

    Deep Agents decides one CONCRETE path against the first rule that matches
    it. A child's rule is a PATTERN, so the honest question is whether every
    concrete path it could reach gets the same ``allow``. This answers it
    conservatively, on the same first-match walk:

    * the first parent rule whose ground OVERLAPS the candidate decides — not
      the first that contains it. A restriction the candidate only partly
      escapes still refuses it, so a child cannot step around a narrow ``deny``
      by asking for its parent directory;
    * that rule must both be ``allow`` and CONTAIN the candidate outright.
      Overlap is not enough to permit; only containment is.

    Anything it cannot reason about is a refusal: an operation no parent rule
    mentions (``execute``, which Deep Agents 0.7.x never checks), a single-``*``
    pattern whose depth is fixed, a candidate reaching wider than any rule.
    A refusal costs the definition a rule; a wrong ``True`` costs the user the
    boundary.
    """

    for rule in parent:
        if operation not in rule.operations:
            continue
        if not any(_overlaps(pattern, path) for pattern in rule.paths):
            continue
        return rule.mode == _ALLOW and any(
            _covers(pattern, path) for pattern in rule.paths
        )
    return False


def _covers(pattern: str, candidate: str) -> bool:
    """Does every path ``candidate`` can reach lie inside ``pattern``'s ground?"""

    if not _is_glob(pattern):
        # A literal rule path matches exactly itself and nothing beneath it.
        return not _is_glob(candidate) and _normalized(candidate) == _normalized(
            pattern
        )
    if "**" not in pattern:
        # A single ``*`` matches one segment, so the ground it covers is a
        # fixed depth rather than a subtree. Reasoning about that correctly
        # means reimplementing the matcher; refusing means losing a rule.
        return False
    return _within(_literal_prefix(candidate), _literal_prefix(pattern))


def _overlaps(pattern: str, candidate: str) -> bool:
    """Can ``pattern`` and ``candidate`` reach any path in common?"""

    ground = _literal_prefix(pattern)
    reach = _literal_prefix(candidate)
    return _within(reach, ground) or _within(ground, reach)


def _within(candidate: str, root: str) -> bool:
    """Is ``candidate`` at or below ``root``? Both are literal prefixes."""

    if root == "/":
        return True
    return candidate == root or candidate.startswith(f"{root}/")


def _literal_prefix(pattern: str) -> str:
    """The deepest ancestor of ``pattern`` that contains no pattern syntax."""

    segments: list[str] = []
    for segment in PurePosixPath(pattern).parts[1:]:
        if _is_glob(segment):
            break
        segments.append(segment)
    return "/" + "/".join(segments)


def _normalized(path: str) -> str:
    return str(PurePosixPath(path))


def _is_glob(text: str) -> bool:
    return any(character in _GLOB_SYNTAX for character in text)


def _requested_or_configured(
    requested: Iterable[str], configured: frozenset[str]
) -> frozenset[str]:
    normalized = frozenset(
        ValueNormalizer.normalize_slug(item, "requested_subagent_capability")
        for item in requested
    )
    return normalized if normalized else configured


__all__ = (
    "FilesystemGrant",
    "SubagentAuthorityError",
    "SubagentAuthorityPolicy",
    "SubagentCapabilityGrant",
    "SubagentPolicyGrant",
)
