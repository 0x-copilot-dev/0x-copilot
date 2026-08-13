"""A ``(permission × pattern)`` rule layer over the three-value action axis.

The PDP's approval axis (``mode_for_kind(read|write|destructive)``) is a
*category* judgement: it can say "writes wait for you", never "allow
``npm run *``", "deny ``git push``", or "never touch ``~/.ssh``". Binary
approval on top of a three-cell axis is also why every repeated write re-prompts,
which is the well-trodden road to a user switching Bypass on wholesale — trading
a precise refusal they never got for a blanket one they should not want.

This module adds the missing expressiveness **without replacing the axis**. A
ruleset is consulted by :class:`~agent_runtime.capabilities.policy.service.PdpPolicyService`
*around* the axis; when no rule matches, the axis decides exactly as before, so
an empty ruleset (the default everywhere) is byte-for-byte today's behaviour.

Shape, and where it comes from
------------------------------
The design is OpenCode's permission ruleset (``packages/opencode/src/permission/index.ts``
— ``evaluate`` :28-38, ``fromConfig`` :186-198), which is worth copying because
it is the smallest thing that expresses all three asks above:

* a rule is ``(permission, pattern) -> allow | deny | ask``;
* **last match wins**, so a later rule overrides an earlier one and config
  layering is just concatenation (:meth:`PermissionRuleset.merge`);
* both halves are globs, so one vocabulary covers "this exact tool" and "every
  tool on this connector" without a second keying scheme.

Two deliberate departures from that reference:

1. **An unmatched subject is not an implicit ask.** OpenCode defaults ``evaluate``
   to ``ask``; here :meth:`PermissionRuleset.verdict` returns ``None`` and the
   caller falls through to the action axis. The axis already answers "what
   happens by default" — re-answering it here would mean adding one narrow rule
   silently changed the default for everything else.
2. **The never-list is a separate ruleset, not an ``action: "deny"`` row.**
   Ordinary rules are consulted at one point in the PDP's ladder; the never-list
   is consulted above everything a posture can touch. Keeping them in separate
   objects is what makes "a Bypass posture cannot lift this" a property of the
   *call site* rather than a comment on a row.

What a pattern is matched against
---------------------------------
:class:`PolicySubjects` — the capability's URN plus the call's own string
arguments. Both are **opaque strings** here: nothing in this module parses a tool
name's shape, so a change to how tool names are composed cannot break rule
matching. The user's pattern does the discriminating, not our parser.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from agent_runtime.capabilities.policy.contracts import PolicyContract


class RuleAction(StrEnum):
    """What a matched rule says to do about the call.

    Maps onto the PDP's tri-state, but is NOT the same enum: a rule is an
    *authored preference*, and the PDP still folds it against availability,
    authorization and the never-list before it becomes a decision. ``ASK`` is a
    pause (a Bypass posture may lift it); ``DENY`` is a refusal (it may not).
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class Wildcard:
    """Glob matching and ``~`` / ``$HOME`` expansion for rule patterns.

    Ported from OpenCode's ``util/wildcard.ts`` — ``*`` → ``.*``, ``?`` → ``.``,
    every other regex metacharacter escaped, matched whole against the input.
    Deliberately NOT ``fnmatch``/``wcmatch``: those give ``*`` a
    path-segment-aware meaning, and the subjects here are a mix of URNs, shell
    strings and paths, so a matcher that stops at ``/`` would silently fail to
    match ``mcp:linear:create_issue`` against ``mcp:linear:*``.

    Consequence worth stating because the filesystem lane has the opposite one
    (see ``desktop/host_filesystem.py``'s "the one thing these patterns CANNOT
    say"): here ``*`` **does** cross ``/`` and **does** match a leading dot, so
    ``~/.ssh/**`` and ``~/*`` both cover ``~/.ssh/id_rsa``. A never-list that
    could not see a dotfile would be worthless.
    """

    _ESCAPE = re.compile(r"[.+^${}()|\[\]\\]")
    #: Bounded compile cache — patterns come from config and from `always`
    #: replies, both bounded, but the cache is capped so a pathological run
    #: cannot grow it without limit.
    _COMPILED: ClassVar[dict[str, re.Pattern[str]]] = {}
    _COMPILED_MAX: ClassVar[int] = 512

    @classmethod
    def match(cls, value: str, pattern: str) -> bool:
        """Whole-string glob match; total (a bad pattern matches nothing)."""

        return cls._compile(pattern).fullmatch(value.replace("\\", "/")) is not None

    @classmethod
    def expand(cls, pattern: str, *, home: str | None = None) -> str:
        """Expand a leading ``~`` / ``$HOME`` so a rule is portable across hosts.

        Only a LEADING occurrence expands, and only as a whole first segment:
        ``~/.ssh/**`` and ``$HOME/.ssh/**`` become the real home path, while a
        ``~`` in the middle of a pattern is left alone (it is a literal there).
        ``home`` is injected so this stays testable without touching the
        environment; it defaults to the OS home once, at config-parse time —
        never inside the PDP's ``decide``, which reads no globals.
        """

        root = home if home is not None else str(Path.home())
        root = root.rstrip("/") or "/"
        for prefix in ("~", "$HOME"):
            if pattern == prefix:
                return root
            if pattern.startswith(f"{prefix}/"):
                return f"{root}{pattern[len(prefix) :]}"
        return pattern

    @classmethod
    def _compile(cls, pattern: str) -> re.Pattern[str]:
        cached = cls._COMPILED.get(pattern)
        if cached is not None:
            return cached
        escaped = cls._ESCAPE.sub(r"\\\g<0>", pattern.replace("\\", "/"))
        compiled = re.compile(
            escaped.replace("*", ".*").replace("?", "."), re.DOTALL
        )
        if len(cls._COMPILED) >= cls._COMPILED_MAX:
            cls._COMPILED.clear()
        cls._COMPILED[pattern] = compiled
        return compiled


class PermissionRule(PolicyContract):
    """One authored rule: ``(permission, pattern) -> action``.

    Both halves default to ``"*"`` so a config entry may name only the axis it
    cares about (``{"mcp:git:*": "deny"}`` or a bare ``"deny"`` for a whole key).
    """

    permission: str = "*"
    pattern: str = "*"
    action: RuleAction

    def matches(self, permission: str, subject: str) -> bool:
        """True when this rule speaks to ``(permission, subject)``."""

        return Wildcard.match(permission, self.permission) and Wildcard.match(
            subject, self.pattern
        )


class PermissionRuleset(PolicyContract):
    """An ordered rule list resolved last-match-wins.

    Order is the whole semantic: :meth:`merge` concatenates, so a ruleset built
    as ``config ⧺ session`` lets a mid-run ``always`` override a broader config
    default, and never the reverse.
    """

    rules: tuple[PermissionRule, ...] = ()

    class Keys:
        """Wire keys for the ruleset shape on ``user_policies_json``."""

        TOOL_USE = "tool_use"
        PERMISSION_RULES = "permission_rules"
        NEVER = "never"

    @property
    def is_empty(self) -> bool:
        return not self.rules

    def evaluate(self, permission: str, subject: str) -> PermissionRule | None:
        """The LAST rule matching ``(permission, subject)``, or ``None``.

        ``None`` — no rule spoke — is a real answer and is not collapsed into
        ``ask``: the caller falls back to the action axis (see the module
        docstring, departure 1).
        """

        for rule in reversed(self.rules):
            if rule.matches(permission, subject):
                return rule
        return None

    def verdict(
        self, permission: str, subjects: Sequence[str]
    ) -> RuleAction | None:
        """Fold ``evaluate`` across every subject, most-restrictive first.

        DENY beats ASK beats ALLOW, matching OpenCode's ``ask`` loop (:72-82),
        because one call carries several subjects (its URN and each argument) and
        the strictest thing said about any of them is the honest answer.
        Unmatched subjects contribute nothing — they are the fall-through case,
        not a vote.
        """

        seen: set[RuleAction] = set()
        for subject in subjects:
            rule = self.evaluate(permission, subject)
            if rule is not None:
                seen.add(rule.action)
        for action in (RuleAction.DENY, RuleAction.ASK, RuleAction.ALLOW):
            if action in seen:
                return action
        return None

    def merge(self, *others: "PermissionRuleset") -> "PermissionRuleset":
        """Concatenate rulesets left-to-right; later rules win on a tie."""

        merged = list(self.rules)
        for other in others:
            merged.extend(other.rules)
        return PermissionRuleset(rules=tuple(merged))

    def with_allow(
        self, *, permission: str, patterns: Sequence[str]
    ) -> "PermissionRuleset":
        """Append one ALLOW rule per pattern — what an ``always`` reply writes."""

        return self.merge(
            PermissionRuleset(
                rules=tuple(
                    PermissionRule(
                        permission=permission, pattern=pattern, action=RuleAction.ALLOW
                    )
                    for pattern in patterns
                )
            )
        )

    @classmethod
    def from_config(
        cls, config: object, *, home: str | None = None
    ) -> "PermissionRuleset":
        """Parse ``{"key": "allow"} | {"key": {"pattern": "deny", ...}}`` (total).

        Mirrors OpenCode's ``fromConfig`` (:186-198): a string value is a rule
        over every pattern; a mapping value is one rule per ``pattern -> action``
        entry, with ``~`` / ``$HOME`` expanded. Anything unparseable is skipped
        rather than raised — this runs on a run's hydrated user policy, which is
        untrusted input, and a malformed row must cost that row and not the run.
        """

        if not isinstance(config, Mapping):
            return cls()
        rules: list[PermissionRule] = []
        for permission, value in config.items():
            if not isinstance(permission, str) or not permission:
                continue
            if isinstance(value, str):
                action = cls._action(value)
                if action is not None:
                    rules.append(PermissionRule(permission=permission, action=action))
                continue
            if not isinstance(value, Mapping):
                continue
            for pattern, raw in value.items():
                action = cls._action(raw)
                if not isinstance(pattern, str) or not pattern or action is None:
                    continue
                rules.append(
                    PermissionRule(
                        permission=permission,
                        pattern=Wildcard.expand(pattern, home=home),
                        action=action,
                    )
                )
        return cls(rules=tuple(rules))

    @classmethod
    def never_from_config(
        cls, config: object, *, home: str | None = None
    ) -> "PermissionRuleset":
        """Parse the never-list: a flat list of patterns, denied for every key.

        A list rather than the ``{permission: {pattern: action}}`` shape on
        purpose. "Never touch ``~/.ssh``" is a statement about a *subject*, not
        about a tool, and the moment it has to be repeated per tool it stops
        being a floor — a connector added next week would arrive outside it.
        """

        if not isinstance(config, Sequence) or isinstance(config, str | bytes):
            return cls()
        return cls(
            rules=tuple(
                PermissionRule(
                    pattern=Wildcard.expand(pattern, home=home), action=RuleAction.DENY
                )
                for pattern in config
                if isinstance(pattern, str) and pattern
            )
        )

    @classmethod
    def authored(
        cls, user_policies_json: object, *, home: str | None = None
    ) -> tuple["PermissionRuleset", "PermissionRuleset"]:
        """Read ``(rules, never)`` off a run's ``user_policies_json`` (total).

        Sits beside ``ConnectorWritePolicyOverrides.from_user_policies`` under the
        same ``tool_use`` sub-policy, so the whole tool-use posture is authored in
        one place and snapshotted once at run start — the PDP/PEP rule in
        ``services/ai-backend/CLAUDE.md``: policy is *authored* in `backend` and
        *enforced* here, never fetched per tool call.
        """

        if not isinstance(user_policies_json, Mapping):
            return cls(), cls()
        tool_use = user_policies_json.get(cls.Keys.TOOL_USE)
        if not isinstance(tool_use, Mapping):
            return cls(), cls()
        return (
            cls.from_config(tool_use.get(cls.Keys.PERMISSION_RULES), home=home),
            cls.never_from_config(tool_use.get(cls.Keys.NEVER), home=home),
        )

    @staticmethod
    def _action(value: object) -> RuleAction | None:
        """Coerce a wire value to a :class:`RuleAction`; ``None`` when unknown."""

        if not isinstance(value, str):
            return None
        try:
            return RuleAction(value.strip().lower())
        except ValueError:
            return None


class PolicySubjects:
    """The strings one call offers up for a rule pattern to match.

    The capability URN first (so ``mcp:linear:*`` works), then every top-level
    string argument (so ``git push`` / ``~/.ssh/id_rsa`` work). Both are treated
    as OPAQUE: this class never parses a tool name or an argument name, so it
    cannot be broken by a change to how either is composed.

    Bounded on both axes — a model-authored argument is untrusted input, and an
    unbounded subject list would put an unbounded regex loop on the hottest path
    in the runtime.
    """

    _MAX_SUBJECTS: ClassVar[int] = 32
    _MAX_CHARS: ClassVar[int] = 1024

    @classmethod
    def of(cls, *, urn: str, args: Mapping[str, object]) -> tuple[str, ...]:
        """Return the ordered, deduplicated, bounded subject list for a call."""

        subjects: list[str] = []
        seen: set[str] = set()
        for candidate in (urn, *cls._argument_values(args)):
            trimmed = candidate.strip()
            if not trimmed or trimmed in seen:
                continue
            seen.add(trimmed)
            subjects.append(trimmed[: cls._MAX_CHARS])
            if len(subjects) >= cls._MAX_SUBJECTS:
                break
        return tuple(subjects)

    @classmethod
    def _argument_values(cls, args: Mapping[str, object]) -> tuple[str, ...]:
        """Every top-level string argument value, in declaration order."""

        if not isinstance(args, Mapping):
            return ()
        return tuple(
            value for value in args.values() if isinstance(value, str) and value
        )


__all__ = [
    "PermissionRule",
    "PermissionRuleset",
    "PolicySubjects",
    "RuleAction",
    "Wildcard",
]
