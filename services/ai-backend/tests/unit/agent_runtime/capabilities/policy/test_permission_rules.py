"""Unit tests for the ``(permission × pattern)`` rule layer and decision scopes.

Covers the three things the action axis alone cannot express, and the two
properties that make the layer safe to add underneath it:

* :class:`Wildcard` — whole-string glob semantics, and the ``~`` / ``$HOME``
  expansion that makes an authored pattern portable across hosts;
* :class:`PermissionRuleset` — last-match-wins ordering, the most-restrictive
  fold across a call's several subjects, and the *total* config parse (a
  malformed row costs that row, never the run);
* :class:`PolicySubjects` — what a call offers a pattern to match, and its bounds;
* :class:`RunDecisionLedger` — ``once`` writes nothing, ``always`` writes a rule,
  and a new ``always`` retroactively resolves the pending asks it now covers.

Builders and constants live in :class:`RuleFixtureMixin` per ``tests/CLAUDE.md``.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.policy.decisions import (
    DecisionScope,
    PendingAsk,
    RunDecisionLedger,
    RunDecisionLedgers,
)
from agent_runtime.capabilities.policy.rules import (
    PermissionRule,
    PermissionRuleset,
    PolicySubjects,
    RuleAction,
    Wildcard,
)


class RuleFixtureMixin:
    """Builders and constants shared by the rule-layer test classes."""

    _HOME = "/Users/sarah"
    _URN = "mcp:linear:create_issue"

    def _rule(
        self,
        action: RuleAction,
        *,
        permission: str = "*",
        pattern: str = "*",
    ) -> PermissionRule:
        return PermissionRule(permission=permission, pattern=pattern, action=action)

    def _ruleset(self, *rules: PermissionRule) -> PermissionRuleset:
        return PermissionRuleset(rules=rules)

    def _ask(
        self, request_id: str, *subjects: str, permission: str | None = None
    ) -> PendingAsk:
        return PendingAsk(
            request_id=request_id,
            permission=permission or self._URN,
            subjects=subjects or (self._URN,),
        )


class TestWildcardMatching(RuleFixtureMixin):
    @pytest.mark.parametrize(
        ("value", "pattern", "expected"),
        [
            ("mcp:linear:create_issue", "mcp:linear:*", True),
            ("mcp:linear:create_issue", "mcp:github:*", False),
            ("mcp:linear:create_issue", "*", True),
            ("mcp:linear:create_issue", "mcp:linear:create_issue", True),
            # Whole-string, not a prefix: a pattern that matches the head of a
            # longer subject must not match.
            ("mcp:linear:create_issue_v2", "mcp:linear:create_issue", False),
            ("git push", "git *", True),
            ("git status", "git push", False),
            ("abc", "a?c", True),
            ("ac", "a?c", False),
        ],
    )
    def test_match(self, value: str, pattern: str, expected: bool) -> None:
        assert Wildcard.match(value, pattern) is expected

    def test_star_crosses_slashes_and_sees_dotfiles(self) -> None:
        # The deliberate departure from a path-segment-aware matcher: a
        # never-list that could not see a dotfile, or could not cross a "/",
        # would be worthless for exactly the paths it exists to protect.
        assert Wildcard.match("/Users/sarah/.ssh/id_rsa", "/Users/sarah/*")
        assert Wildcard.match("/Users/sarah/.ssh/id_rsa", "/Users/sarah/.ssh/**")

    def test_regex_metacharacters_in_a_pattern_are_literal(self) -> None:
        # A pattern is a glob, not a regex: "." must not mean "any character",
        # or an authored rule would silently cover neighbours it never named.
        assert Wildcard.match("a.txt", "a.txt")
        assert not Wildcard.match("axtxt", "a.txt")
        assert Wildcard.match("cost (usd)", "cost (usd)")

    def test_backslashes_are_normalised_on_both_sides(self) -> None:
        assert Wildcard.match("C:\\Users\\sarah\\notes", "C:/Users/sarah/*")

    def test_match_is_total_for_an_unmatched_value(self) -> None:
        assert not Wildcard.match("", "mcp:linear:*")


class TestWildcardHomeExpansion(RuleFixtureMixin):
    @pytest.mark.parametrize("prefix", ["~", "$HOME"])
    def test_leading_prefix_expands(self, prefix: str) -> None:
        assert (
            Wildcard.expand(f"{prefix}/.ssh/**", home=self._HOME)
            == f"{self._HOME}/.ssh/**"
        )

    @pytest.mark.parametrize("prefix", ["~", "$HOME"])
    def test_bare_prefix_expands_to_the_home_root(self, prefix: str) -> None:
        assert Wildcard.expand(prefix, home=self._HOME) == self._HOME

    def test_a_trailing_slash_on_home_does_not_double_up(self) -> None:
        assert Wildcard.expand("~/.ssh", home=f"{self._HOME}/") == f"{self._HOME}/.ssh"

    @pytest.mark.parametrize(
        "pattern",
        [
            # Not a leading whole first segment — a literal there.
            "/tmp/~/notes",
            "~backup/**",
            "$HOMEWORK/**",
            "mcp:linear:*",
        ],
    )
    def test_a_non_leading_or_partial_prefix_is_left_alone(self, pattern: str) -> None:
        assert Wildcard.expand(pattern, home=self._HOME) == pattern

    def test_an_expanded_rule_matches_the_real_path(self) -> None:
        ruleset = PermissionRuleset.from_config(
            {"*": {"~/.ssh/**": "deny"}}, home=self._HOME
        )
        rule = ruleset.evaluate("mcp:fs:write", f"{self._HOME}/.ssh/id_rsa")
        assert rule is not None
        assert rule.action is RuleAction.DENY
        # And the unexpanded literal no longer matches anything, which is the
        # whole reason expansion happens at parse time.
        assert ruleset.evaluate("mcp:fs:write", "~/.ssh/id_rsa") is None


class TestLastMatchWins(RuleFixtureMixin):
    def test_the_later_of_two_matching_rules_decides(self) -> None:
        deny_then_allow = self._ruleset(
            self._rule(RuleAction.DENY), self._rule(RuleAction.ALLOW)
        )
        allow_then_deny = self._ruleset(
            self._rule(RuleAction.ALLOW), self._rule(RuleAction.DENY)
        )
        first = deny_then_allow.evaluate(self._URN, self._URN)
        second = allow_then_deny.evaluate(self._URN, self._URN)
        assert first is not None and first.action is RuleAction.ALLOW
        assert second is not None and second.action is RuleAction.DENY

    def test_merge_is_concatenation_so_the_right_hand_side_wins(self) -> None:
        config = self._ruleset(self._rule(RuleAction.ASK, permission="mcp:linear:*"))
        session = self._ruleset(self._rule(RuleAction.ALLOW, permission=self._URN))
        merged = config.merge(session)
        assert merged.rules == config.rules + session.rules
        assert merged.verdict(self._URN, (self._URN,)) is RuleAction.ALLOW
        # Reversed, the broader config default wins again — which is why the
        # call site's argument order is the semantic and not a style choice.
        assert session.merge(config).verdict(self._URN, (self._URN,)) is RuleAction.ASK

    def test_an_unmatched_subject_is_none_not_an_implicit_ask(self) -> None:
        # The deliberate departure from the OpenCode reference: "no rule spoke"
        # is a real answer, so adding one narrow rule cannot silently change the
        # default for everything else.
        ruleset = self._ruleset(self._rule(RuleAction.DENY, permission="mcp:github:*"))
        assert ruleset.evaluate(self._URN, self._URN) is None
        assert ruleset.verdict(self._URN, (self._URN,)) is None


class TestVerdictFold(RuleFixtureMixin):
    @pytest.mark.parametrize(
        ("actions", "expected"),
        [
            ((RuleAction.ALLOW, RuleAction.ASK), RuleAction.ASK),
            ((RuleAction.ALLOW, RuleAction.DENY), RuleAction.DENY),
            ((RuleAction.ASK, RuleAction.DENY), RuleAction.DENY),
            ((RuleAction.ALLOW, RuleAction.ALLOW), RuleAction.ALLOW),
        ],
    )
    def test_most_restrictive_matched_subject_wins(
        self, actions: tuple[RuleAction, ...], expected: RuleAction
    ) -> None:
        # One call carries several subjects; the strictest thing said about any
        # of them is the honest answer.
        ruleset = self._ruleset(
            *(
                self._rule(action, pattern=f"subject-{index}")
                for index, action in enumerate(actions)
            )
        )
        subjects = tuple(f"subject-{index}" for index in range(len(actions)))
        assert ruleset.verdict(self._URN, subjects) is expected

    def test_unmatched_subjects_do_not_dilute_a_match(self) -> None:
        ruleset = self._ruleset(self._rule(RuleAction.DENY, pattern="*id_rsa*"))
        subjects = (self._URN, "/tmp/notes.txt", "/home/s/.ssh/id_rsa")
        assert ruleset.verdict(self._URN, subjects) is RuleAction.DENY

    def test_empty_ruleset_says_nothing(self) -> None:
        assert PermissionRuleset().verdict(self._URN, (self._URN,)) is None


class TestConfigParsing(RuleFixtureMixin):
    def test_a_string_value_is_a_rule_over_every_pattern(self) -> None:
        ruleset = PermissionRuleset.from_config({"mcp:git:*": "deny"})
        assert ruleset.rules == (
            PermissionRule(permission="mcp:git:*", pattern="*", action=RuleAction.DENY),
        )

    def test_a_mapping_value_is_one_rule_per_pattern(self) -> None:
        ruleset = PermissionRuleset.from_config(
            {"edit": {"src/**": "allow", "~/.ssh/**": "deny"}}, home=self._HOME
        )
        assert [(rule.pattern, rule.action) for rule in ruleset.rules] == [
            ("src/**", RuleAction.ALLOW),
            (f"{self._HOME}/.ssh/**", RuleAction.DENY),
        ]

    @pytest.mark.parametrize(
        "config",
        [
            None,
            "deny",
            ["deny"],
            42,
            {"": "deny"},
            {"edit": "not-an-action"},
            {"edit": {"src/**": "not-an-action"}},
            {"edit": {"": "allow"}},
            {"edit": 7},
        ],
    )
    def test_an_unparseable_row_costs_that_row_and_not_the_run(
        self, config: object
    ) -> None:
        # This parses a run's hydrated user policy, which is untrusted input.
        assert PermissionRuleset.from_config(config).rules == ()

    def test_a_bad_row_does_not_discard_its_good_neighbours(self) -> None:
        ruleset = PermissionRuleset.from_config(
            {"edit": {"src/**": "allow", "lib/**": "nonsense"}}
        )
        assert [rule.pattern for rule in ruleset.rules] == ["src/**"]

    def test_action_values_are_case_and_whitespace_tolerant(self) -> None:
        ruleset = PermissionRuleset.from_config({"edit": " DENY "})
        assert ruleset.rules[0].action is RuleAction.DENY

    def test_never_list_is_a_flat_pattern_list_denied_for_every_key(self) -> None:
        never = PermissionRuleset.never_from_config(
            ["~/.ssh/**", "/etc/**"], home=self._HOME
        )
        assert [(rule.permission, rule.pattern) for rule in never.rules] == [
            ("*", f"{self._HOME}/.ssh/**"),
            ("*", "/etc/**"),
        ]
        # "*" for the permission is the point: a connector added next week
        # arrives INSIDE the floor rather than outside it.
        assert never.verdict("mcp:brand-new:write", (f"{self._HOME}/.ssh/id_rsa",)) is (
            RuleAction.DENY
        )

    @pytest.mark.parametrize("config", [None, "~/.ssh/**", {"a": "b"}, [None, "", 7]])
    def test_never_list_parse_is_total(self, config: object) -> None:
        assert PermissionRuleset.never_from_config(config).rules == ()

    def test_authored_reads_both_halves_off_the_tool_use_sub_policy(self) -> None:
        rules, never = PermissionRuleset.authored(
            {
                "tool_use": {
                    "permission_rules": {"mcp:linear:*": "allow"},
                    "never": ["~/.ssh/**"],
                }
            },
            home=self._HOME,
        )
        assert (
            rules.verdict("mcp:linear:create_issue", (self._URN,)) is RuleAction.ALLOW
        )
        assert never.verdict("anything", (f"{self._HOME}/.ssh/id_rsa",)) is (
            RuleAction.DENY
        )

    @pytest.mark.parametrize(
        "policies", [None, {}, {"tool_use": None}, {"tool_use": []}, "nonsense"]
    )
    def test_authored_is_total_and_defaults_to_two_empty_rulesets(
        self, policies: object
    ) -> None:
        rules, never = PermissionRuleset.authored(policies)
        assert rules.rules == () and never.rules == ()


class TestPolicySubjects(RuleFixtureMixin):
    def test_the_urn_comes_first_then_string_arguments_in_order(self) -> None:
        subjects = PolicySubjects.of(
            urn=self._URN, args={"title": "Fix bug", "body": "details"}
        )
        assert subjects == (self._URN, "Fix bug", "details")

    def test_non_string_arguments_are_not_subjects(self) -> None:
        subjects = PolicySubjects.of(
            urn=self._URN,
            args={"force": True, "count": 3, "rows": [{"a": 1}], "nothing": None},
        )
        assert subjects == (self._URN,)

    def test_blank_and_duplicate_values_are_dropped(self) -> None:
        subjects = PolicySubjects.of(
            urn=self._URN, args={"a": "  ", "b": "same", "c": "same", "d": " same "}
        )
        assert subjects == (self._URN, "same")

    def test_subject_count_and_length_are_bounded(self) -> None:
        # A model-authored argument is untrusted input, and the rule fold runs on
        # the hottest path in the runtime.
        subjects = PolicySubjects.of(
            urn=self._URN,
            args={f"k{index}": f"v{index}" for index in range(200)},
        )
        assert len(subjects) == 32
        long_arg = PolicySubjects.of(urn=self._URN, args={"blob": "x" * 5000})
        assert len(long_arg[1]) == 1024


class TestDecisionScopeWireCoercion:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("always", DecisionScope.ALWAYS),
            (" ALWAYS ", DecisionScope.ALWAYS),
            ("once", DecisionScope.ONCE),
            # Fail-closed by construction: an unrecognised or absent scope can
            # only ever under-grant.
            (None, DecisionScope.ONCE),
            ("", DecisionScope.ONCE),
            ("forever", DecisionScope.ONCE),
            (True, DecisionScope.ONCE),
            (42, DecisionScope.ONCE),
        ],
    )
    def test_from_wire(self, value: object, expected: DecisionScope) -> None:
        assert DecisionScope.from_wire(value) is expected


class TestRunDecisionLedger(RuleFixtureMixin):
    def test_once_writes_no_rule(self) -> None:
        ledger = RunDecisionLedger()
        ledger.register(self._ask("a"))
        outcome = ledger.reply(request_id="a", scope=DecisionScope.ONCE)
        assert outcome.scope is DecisionScope.ONCE
        assert outcome.resolved == ()
        assert ledger.rules.rules == ()
        assert ledger.pending() == ()

    def test_always_writes_one_allow_rule_per_subject(self) -> None:
        ledger = RunDecisionLedger()
        ledger.register(self._ask("a", self._URN, "Fix bug"))
        outcome = ledger.reply(request_id="a", scope=DecisionScope.ALWAYS)
        assert outcome.scope is DecisionScope.ALWAYS
        assert [
            (rule.permission, rule.pattern, rule.action) for rule in ledger.rules.rules
        ] == [
            (self._URN, self._URN, RuleAction.ALLOW),
            (self._URN, "Fix bug", RuleAction.ALLOW),
        ]

    def test_an_always_retroactively_resolves_the_asks_it_covers(self) -> None:
        # The friction this whole module exists to remove: a run parks three
        # calls to the same tool, the user answers one with `always`, and the
        # other two stop being questions.
        ledger = RunDecisionLedger()
        ledger.register(self._ask("a", self._URN, "first"))
        ledger.register(self._ask("b", self._URN, "second"))
        ledger.register(self._ask("c", self._URN, "third"))
        outcome = ledger.reply(request_id="a", scope=DecisionScope.ALWAYS)
        assert outcome.scope is DecisionScope.ALWAYS
        assert sorted(outcome.resolved) == ["b", "c"]
        assert ledger.pending() == ()

    def test_resolution_uses_the_same_fold_the_pdp_applies(self) -> None:
        # The sibling asks above differ in their arguments, so a stricter
        # "every subject must match" rescan would report nothing resolved while
        # the PDP went on to ALLOW them. The ledger must not disagree with the
        # decision it is describing.
        ledger = RunDecisionLedger()
        ledger.register(self._ask("a", self._URN, "first"))
        ledger.register(self._ask("b", self._URN, "second"))
        ledger.reply(request_id="a", scope=DecisionScope.ALWAYS)
        assert (
            ledger.rules.verdict(self._URN, (self._URN, "second")) is RuleAction.ALLOW
        )

    def test_an_always_does_not_resolve_an_ask_on_another_permission(self) -> None:
        other = "mcp:linear:delete_issue"
        ledger = RunDecisionLedger()
        ledger.register(self._ask("a", self._URN))
        ledger.register(self._ask("b", other, permission=other))
        outcome = ledger.reply(request_id="a", scope=DecisionScope.ALWAYS)
        assert outcome.resolved == ()
        assert [ask.request_id for ask in ledger.pending()] == ["b"]

    def test_an_unknown_request_id_cannot_widen_anything(self) -> None:
        ledger = RunDecisionLedger()
        outcome = ledger.reply(request_id="never-parked", scope=DecisionScope.ALWAYS)
        assert outcome.scope is DecisionScope.ONCE
        assert ledger.rules.rules == ()

    def test_register_is_idempotent_across_a_langgraph_replay(self) -> None:
        # The tool node re-executes from the top on resume, so the same ask is
        # registered again; the approval id is deterministic for exactly this.
        ledger = RunDecisionLedger()
        ledger.register(self._ask("a", self._URN))
        ledger.register(self._ask("a", self._URN))
        assert len(ledger.pending()) == 1

    def test_pending_registrations_are_bounded(self) -> None:
        ledger = RunDecisionLedger()
        for index in range(300):
            ledger.register(self._ask(f"a{index}", f"subject-{index}"))
        assert len(ledger.pending()) == 256


class TestRunDecisionLedgers(RuleFixtureMixin):
    def setup_method(self) -> None:
        RunDecisionLedgers.reset()

    def teardown_method(self) -> None:
        RunDecisionLedgers.reset()

    def test_the_same_run_gets_the_same_ledger(self) -> None:
        assert RunDecisionLedgers.for_run("run-1") is RunDecisionLedgers.for_run(
            "run-1"
        )

    def test_runs_do_not_share_rules(self) -> None:
        # A grant is run-scoped; leaking it to a concurrent run in the same
        # worker process would be a grant from a card that run never showed.
        RunDecisionLedgers.for_run("run-1").register(self._ask("a", self._URN))
        RunDecisionLedgers.for_run("run-1").reply(
            request_id="a", scope=DecisionScope.ALWAYS
        )
        assert RunDecisionLedgers.for_run("run-2").rules.rules == ()

    def test_the_registry_is_bounded(self) -> None:
        for index in range(200):
            RunDecisionLedgers.for_run(f"run-{index}")
        assert len(RunDecisionLedgers._BY_RUN) <= 64
