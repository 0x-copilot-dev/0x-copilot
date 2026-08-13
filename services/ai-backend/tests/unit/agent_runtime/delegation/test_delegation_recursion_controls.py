"""The four controls that keep delegation from running away.

Each test here fails on the pre-change tree for a distinct reason, and the
reasons are worth naming because three of them were invisible:

* the ``task`` tool stamped no depth and refused nothing, so a delegate could
  delegate again — unbounded, on the user's own BYOK key;
* a child inherited the parent's tool list verbatim, ``task`` included, so the
  capability was there even when nobody meant to grant it;
* ``inherited_parent_grant`` substituted the DEFAULT approval posture for the
  parent's real one, which widens any parent stricter than the default;
* the per-turn tool budget is shared with delegates, and that is load-bearing —
  it is the only thing bounding total spend once a fan-out starts.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetAdmit,
    ToolBudgetMiddleware,
    ToolBudgetReject,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicyMode
from agent_runtime.execution.tool_outcomes import ToolErrorCode
from agent_runtime.persistence.records.tool_budgets import DefaultToolBudget
from agent_runtime.delegation.subagents import atlas_task_tool
from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_atlas_task_tool,
    build_subagent_invocation_config,
)
from agent_runtime.delegation.subagents.authority import (
    SubagentAuthorityPolicy,
    SubagentCapabilityGrant,
    SubagentPolicyGrant,
)
from agent_runtime.delegation.subagents.constants import Defaults, Limits
from agent_runtime.delegation.subagents.contracts import (
    SubagentDefinition,
    SubagentErrorCode,
)
from agent_runtime.delegation.subagents.handoff import SubagentHandoffPolicy
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.delegation.subagents.coordination import DelegationAdmissionPolicy
from agent_runtime.delegation.subagents.recursion import (
    ALLOW_NESTED_DELEGATION_KEY,
    DELEGATION_TOOL_NAME,
    SUBAGENT_DELEGATION_DEPTH_KEY,
    DelegationDepthPolicy,
    SubagentRecursionPolicy,
)


class _RecordingRunnable:
    """Runnable-shaped child graph that records whether it was ever entered."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def with_config(self, *_args: object, **_kwargs: object) -> "_RecordingRunnable":
        return self

    def invoke(self, _state: object, config: object = None) -> dict[str, object]:
        self.invocations.append({"config": config})
        return {"messages": []}

    async def ainvoke(self, _state: object, config: object = None) -> dict[str, object]:
        self.invocations.append({"config": config})
        return {"messages": []}


class _FakeToolRuntime:
    """The two ``ToolRuntime`` attributes the task tool actually reads."""

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        self.tool_call_id = "call_alpha"
        self.state: dict[str, Any] = {"messages": []}
        self.config: dict[str, Any] = config or {}


class _NamedTool:
    """Minimal tool stand-in — the recursion policy matches on ``name`` only."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover — assertion output only
        return f"_NamedTool({self.name!r})"


class _FakeToolCallLedger:
    """The two counting reads ``ToolBudgetMiddleware`` performs.

    The real ledger lives in ``runtime_worker`` and carries locking, entry
    lifecycles, and a run id none of which this admission arithmetic touches.
    """

    def __init__(self) -> None:
        self._calls: dict[str, int] = {}

    def charge(self, tool_name: str, *, times: int = 1) -> None:
        self._calls[tool_name] = self._calls.get(tool_name, 0) + times

    def charged_calls(self, tool_name: str) -> int:
        return self._calls.get(tool_name, 0)

    def total_input_tokens(self, _tool_name: str) -> int:
        return 0


class DelegationFixtureMixin:
    """Shared builders for the recursion-control suites."""

    SUBAGENT_NAME = "researcher"

    def build_task_tool(self, runnable: _RecordingRunnable) -> Any:
        return build_atlas_task_tool(
            [
                {
                    "name": self.SUBAGENT_NAME,
                    "description": "runs research on a narrow question",
                    "runnable": runnable,
                }
            ]
        )

    def call_task(self, tool: Any, runtime: _FakeToolRuntime) -> object:
        return asyncio.run(
            tool.coroutine(
                description="summarise the filing",
                subagent_type=self.SUBAGENT_NAME,
                runtime=runtime,
            )
        )

    @staticmethod
    def runtime_at_depth(depth: int) -> _FakeToolRuntime:
        """A tool runtime whose ambient config says it already ran ``depth`` deep."""

        return _FakeToolRuntime(
            config={"metadata": {SUBAGENT_DELEGATION_DEPTH_KEY: depth}}
        )

    @staticmethod
    def bypassing_parent() -> SubagentCapabilityGrant:
        return SubagentCapabilityGrant(
            capabilities={SubagentAuthorityPolicy.DISPATCH_CAPABILITY},
            tools={"web_search"},
            skills={"research"},
            permission_scopes={"tools:read"},
            policy=SubagentPolicyGrant(
                read=ToolUsePolicyMode.AUTO,
                write=ToolUsePolicyMode.AUTO,
                destructive=ToolUsePolicyMode.AUTO,
            ),
        )


class TestDelegationDepthRefusal(DelegationFixtureMixin):
    def test_supervisor_delegation_is_admitted_and_stamps_depth_one(self) -> None:
        runnable = _RecordingRunnable()
        tool = self.build_task_tool(runnable)

        result = self.call_task(tool, _FakeToolRuntime())

        assert runnable.invocations, "the supervisor's own delegation must proceed"
        config = runnable.invocations[0]["config"]
        assert config["metadata"][SUBAGENT_DELEGATION_DEPTH_KEY] == 1
        assert config["configurable"][SUBAGENT_DELEGATION_DEPTH_KEY] == 1
        assert not isinstance(result, str), "an admitted call returns a Command"

    def test_depth_two_spawn_is_refused_before_the_child_is_entered(self) -> None:
        """The control that did not exist: a delegate delegating again."""

        runnable = _RecordingRunnable()
        tool = self.build_task_tool(runnable)

        result = self.call_task(tool, self.runtime_at_depth(1))

        assert runnable.invocations == [], (
            "a refused delegation must not enter the child graph at all"
        )
        assert isinstance(result, str)
        assert "Delegation refused" in result
        assert "maximum" in result

    def test_refusal_is_the_typed_error_with_a_safe_public_message(self) -> None:
        refusal = DelegationDepthPolicy().refusal(
            {"metadata": {SUBAGENT_DELEGATION_DEPTH_KEY: 1}}
        )

        assert refusal is not None
        assert refusal.code is SubagentErrorCode.DEPTH_LIMIT_EXCEEDED
        assert refusal.retryable is False
        assert "Delegation refused" in refusal.safe_message
        assert "Traceback" not in refusal.safe_message

    def test_sync_task_entrypoint_refuses_at_the_same_depth(self) -> None:
        """The gateway-off synchronous branch must not be a way around it."""

        runnable = _RecordingRunnable()
        tool = self.build_task_tool(runnable)

        result = tool.func(
            description="summarise the filing",
            subagent_type=self.SUBAGENT_NAME,
            runtime=self.runtime_at_depth(1),
        )

        assert runnable.invocations == []
        assert isinstance(result, str)
        assert "Delegation refused" in result

    def test_a_raised_depth_ceiling_admits_the_second_hop(self) -> None:
        """The limit is configuration, not a constant baked into the check."""

        policy = DelegationDepthPolicy(DelegationAdmissionPolicy(max_depth=2))

        assert policy.refusal({"metadata": {SUBAGENT_DELEGATION_DEPTH_KEY: 1}}) is None
        assert (
            policy.refusal({"metadata": {SUBAGENT_DELEGATION_DEPTH_KEY: 2}}) is not None
        )

    def test_untrusted_depth_values_resolve_to_the_supervisor(self) -> None:
        """A config is merged from many sources; a junk value must not admit more."""

        for junk in ("2", -5, None, True, {"nested": 2}):
            assert (
                DelegationDepthPolicy.parent_depth(
                    {"metadata": {SUBAGENT_DELEGATION_DEPTH_KEY: junk}}
                )
                == 0
            ), f"{junk!r} must not be read as a depth"
        assert DelegationDepthPolicy.parent_depth(None) == 0
        assert DelegationDepthPolicy.parent_depth({"metadata": "not-a-mapping"}) == 0

    def test_the_configured_ceiling_is_sourced_from_the_document(self) -> None:
        """The hyperparameter seam is the source, not a second literal."""

        assert DelegationDepthPolicy.snapshot().max_depth == (
            Defaults.MAX_DELEGATION_DEPTH
        )
        assert DelegationAdmissionPolicy().max_depth == Defaults.MAX_DELEGATION_DEPTH
        with pytest.raises(ValueError):
            DelegationAdmissionPolicy(max_depth=Limits.DELEGATION_DEPTH_MAX + 1)

    def test_invocation_config_never_stamps_a_depth_below_one(self) -> None:
        config = build_subagent_invocation_config("call_alpha", child_depth=0)

        assert config["metadata"][SUBAGENT_DELEGATION_DEPTH_KEY] == 1


class TestChildToolSurface(DelegationFixtureMixin):
    def test_child_tool_surface_excludes_task_by_default(self) -> None:
        spec = {
            "name": self.SUBAGENT_NAME,
            "description": "runs research",
            "tools": [_NamedTool("web_search"), _NamedTool(DELEGATION_TOOL_NAME)],
        }

        narrowed = SubagentRecursionPolicy.narrow_spec(spec)

        assert [tool.name for tool in narrowed["tools"]] == ["web_search"]
        assert [tool.name for tool in spec["tools"]] == [
            "web_search",
            DELEGATION_TOOL_NAME,
        ], "narrowing must not mutate the caller's spec"

    def test_child_tool_surface_keeps_task_when_the_definition_grants_it(self) -> None:
        spec = {
            "name": self.SUBAGENT_NAME,
            "description": "runs research",
            "tools": [_NamedTool("web_search"), _NamedTool(DELEGATION_TOOL_NAME)],
            ALLOW_NESTED_DELEGATION_KEY: True,
        }

        narrowed = SubagentRecursionPolicy.narrow_spec(spec)

        assert [tool.name for tool in narrowed["tools"]] == [
            "web_search",
            DELEGATION_TOOL_NAME,
        ]

    def test_a_spec_without_task_is_returned_unchanged(self) -> None:
        spec = {
            "name": self.SUBAGENT_NAME,
            "description": "runs research",
            "tools": [_NamedTool("web_search")],
        }

        assert SubagentRecursionPolicy.narrow_spec(spec) is spec

    def test_a_precompiled_runnable_spec_is_out_of_reach(self) -> None:
        """Its graph was built by the caller; there is no tool list to narrow."""

        spec = {
            "name": self.SUBAGENT_NAME,
            "description": "runs research",
            "runnable": _RecordingRunnable(),
        }

        assert SubagentRecursionPolicy.narrow_spec(spec) is spec

    def test_the_live_builder_compiles_the_child_without_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The narrowing has to happen on the path Deep Agents actually walks.

        ``build_atlas_task_tool`` is what the monkey-patched
        ``_build_task_tool`` seam calls, and ``create_sub_agent`` is where the
        child graph is compiled from its spec — so capturing the spec at that
        call is the honest test of "the child's tool list excludes ``task``".
        """

        compiled: list[dict[str, Any]] = []

        def _capture(spec: dict[str, Any], **_kwargs: object) -> _RecordingRunnable:
            compiled.append(spec)
            return _RecordingRunnable()

        monkeypatch.setattr(atlas_task_tool, "create_sub_agent", _capture)

        build_atlas_task_tool(
            [
                {
                    "name": self.SUBAGENT_NAME,
                    "description": "runs research on a narrow question",
                    "system_prompt": "research",
                    "model": "fake-model",
                    "tools": [
                        _NamedTool("web_search"),
                        _NamedTool(DELEGATION_TOOL_NAME),
                    ],
                },
                {
                    "name": "delegator",
                    "description": "may fan work out further",
                    "system_prompt": "delegate",
                    "model": "fake-model",
                    "tools": [
                        _NamedTool("web_search"),
                        _NamedTool(DELEGATION_TOOL_NAME),
                    ],
                    ALLOW_NESTED_DELEGATION_KEY: True,
                },
            ]
        )

        by_name = {spec["name"]: spec for spec in compiled}
        assert [tool.name for tool in by_name[self.SUBAGENT_NAME]["tools"]] == [
            "web_search"
        ]
        assert [tool.name for tool in by_name["delegator"]["tools"]] == [
            "web_search",
            DELEGATION_TOOL_NAME,
        ]

    def test_the_general_purpose_subagent_does_not_grant_nested_delegation(
        self,
    ) -> None:
        """Deep Agents' auto-added spec carries no opt-in, so it never nests."""

        from deepagents.middleware.subagents import (  # noqa: PLC0415
            GENERAL_PURPOSE_SUBAGENT,
        )

        assert not SubagentRecursionPolicy.grants_nested_delegation(
            GENERAL_PURPOSE_SUBAGENT
        )


class TestChildPermissionFloor(DelegationFixtureMixin):
    def test_a_bypass_holding_parent_yields_a_non_bypass_child(self) -> None:
        clamped = SubagentAuthorityPolicy.delegable_parent_grant(
            self.bypassing_parent(), bypass_active=True
        )

        assert clamped.policy.write is ToolUsePolicyMode.ASK
        assert clamped.policy.destructive is ToolUsePolicyMode.REQUIRE
        assert clamped.tools == frozenset({"web_search"}), (
            "the clamp is about posture; it must not also strip capabilities"
        )

    def test_a_non_bypassing_parent_is_returned_untouched(self) -> None:
        parent = self.bypassing_parent()

        assert (
            SubagentAuthorityPolicy.delegable_parent_grant(parent, bypass_active=False)
            is parent
        )

    def test_a_parent_stricter_than_the_default_is_not_widened(self) -> None:
        """The leak: an absent grant used to substitute the DEFAULT posture."""

        strict = SubagentPolicyGrant(
            read=ToolUsePolicyMode.ASK,
            write=ToolUsePolicyMode.REQUIRE,
            destructive=ToolUsePolicyMode.BLOCK,
        )

        inherited = SubagentAuthorityPolicy.inherited_parent_grant(
            context_scopes=frozenset({"tools:read"}),
            definition_tools=frozenset({"web_search"}),
            definition_skills=frozenset(),
            parent_policy=strict,
        )

        assert inherited.policy == strict

    def test_a_child_can_be_stricter_than_its_parent(self) -> None:
        """Floored-at is an upper bound, not an assignment."""

        narrowed = SubagentAuthorityPolicy.narrow(
            parent=self.bypassing_parent(),
            definition_tools=frozenset({"web_search"}),
            definition_skills=frozenset({"research"}),
            definition_allowed_scopes=frozenset(),
            definition_policy=SubagentPolicyGrant(
                read=ToolUsePolicyMode.ASK,
                write=ToolUsePolicyMode.BLOCK,
                destructive=ToolUsePolicyMode.BLOCK,
            ),
            requested_tools=(),
            requested_skills=(),
            context_scopes=frozenset({"tools:read"}),
        )

        assert narrowed.policy.read is ToolUsePolicyMode.ASK
        assert narrowed.policy.write is ToolUsePolicyMode.BLOCK


class HandoffSeamMixin:
    """Builders for entering the floor at ``narrow_authority``, not below it.

    ``inherited_parent_grant`` takes ``parent_policy`` as a keyword, so a test
    that calls it directly supplies the very thing under test and passes even
    when no caller ever does. These builders exist so the assertions below can
    enter one level up, where the ceiling has to be *derived* from the run.
    """

    @staticmethod
    def context(tool_use: dict[str, object] | None = None) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_1",
            org_id="org_1",
            roles={"Admin"},
            permission_scopes={"tools:read"},
            model_profile=ModelConfig(
                provider="Fake",
                model_name="fake-model",
                max_input_tokens=128_000,
                timeout_seconds=30,
                temperature=0,
                supports_streaming=True,
            ),
            trace_id="trace_1",
            user_policies_json={"tool_use": tool_use} if tool_use else {},
        )

    @staticmethod
    def definition() -> SubagentDefinition:
        return SubagentDefinition.model_validate(
            {
                "name": "researcher",
                "description": "Researches a narrow question on request.",
                "graph_id": "graph_research",
                "tools": frozenset({"web_search"}),
            }
        )

    def narrowed(self, tool_use: dict[str, object] | None = None) -> Any:
        return SubagentHandoffPolicy.narrow_authority(
            context=self.context(tool_use),
            definition=self.definition(),
            requested_tools=(),
            requested_skills=(),
            parent_grant=None,
        )


class TestParentPostureReachesTheFloor(HandoffSeamMixin):
    """The live half of rule 3: the derived ceiling is the parent's own posture.

    Without the wiring, ``narrow_authority`` synthesises the parent ceiling with
    a fresh ``SubagentPolicyGrant()`` — the deployment default — so a workspace
    that tightened its policy is silently widened back to the default for every
    delegated task. The parameter existed and was tested; nothing passed it.
    """

    def test_a_strict_workspace_policy_is_carried_into_the_child_grant(self) -> None:
        narrowed = self.narrowed({"workspace": {"write": "block"}})

        assert narrowed.policy.write is ToolUsePolicyMode.BLOCK, (
            "the parent's real posture must be the ceiling, not the default"
        )

    def test_a_user_override_beats_the_workspace_default(self) -> None:
        """The ceiling is the resolved snapshot, not either input alone."""

        narrowed = self.narrowed(
            {"workspace": {"destructive": "require"}, "user": {"destructive": "block"}}
        )

        assert narrowed.policy.destructive is ToolUsePolicyMode.BLOCK

    def test_an_unconfigured_run_is_unchanged(self) -> None:
        """The fail-open lane still resolves to the deployment defaults."""

        narrowed = self.narrowed()

        assert narrowed.policy.read is ToolUsePolicyMode.AUTO
        assert narrowed.policy.write is ToolUsePolicyMode.ASK
        assert narrowed.policy.destructive is ToolUsePolicyMode.REQUIRE

    def test_a_looser_policy_cannot_widen_past_the_definition(self) -> None:
        """The floor is a ceiling on both sides: AUTO writes still meet ASK."""

        narrowed = self.narrowed({"workspace": {"write": "auto"}})

        assert narrowed.policy.write is ToolUsePolicyMode.ASK, (
            "the definition's own posture still applies; deriving the parent "
            "ceiling must not become a widening path"
        )


class TestDelegatedBudgetSemantics(DelegationFixtureMixin):
    """Characterise the budget a delegate actually spends against.

    This is not a new control, and pretending otherwise would be the dishonest
    version of this suite. The point is the dependency: the per-tool cap is
    scoped to the TURN and is shared with delegates, so it only bounds spend
    while the number of delegates is bounded. Unbounded depth meant an
    unbounded number of agents drawing on one pool and the cap stopped meaning
    anything. These assertions pin the semantics the depth ceiling protects.
    """

    @staticmethod
    def _wildcard_budget(limit: int) -> Any:
        return DefaultToolBudget.record().model_copy(
            update={"max_calls_per_run": limit}
        )

    def test_a_child_exceeding_the_cap_is_rejected_not_crashed(self) -> None:
        middleware = ToolBudgetMiddleware([self._wildcard_budget(2)])
        ledger = _FakeToolCallLedger()
        ledger.charge("web_search", times=2)

        decision = middleware.check_admit(ledger=ledger, tool_name="web_search")

        assert isinstance(decision, ToolBudgetReject)
        assert decision.error_code is ToolErrorCode.TOOL_BUDGET_EXCEEDED
        assert "web_search" in decision.safe_message
        assert "finalize now" in decision.safe_message

    def test_the_pool_is_one_ledger_shared_by_parent_and_delegate(self) -> None:
        """A delegate's calls are charged to the same per-tool counter."""

        middleware = ToolBudgetMiddleware([self._wildcard_budget(2)])
        ledger = _FakeToolCallLedger()

        ledger.charge("web_search")  # the supervisor's own call
        assert isinstance(
            middleware.check_admit(ledger=ledger, tool_name="web_search"),
            ToolBudgetAdmit,
        )
        ledger.charge("web_search")  # a delegate's call, same ledger
        assert isinstance(
            middleware.check_admit(ledger=ledger, tool_name="web_search"),
            ToolBudgetReject,
        )

    def test_the_prompt_states_the_same_cap_it_enforces(self) -> None:
        """The model is told the enforced number, and told delegates share it."""

        from agent_runtime.execution.deep_agent_builder import (  # noqa: PLC0415
            format_web_subagent_suffix,
        )

        suffix = format_web_subagent_suffix(DefaultToolBudget.MAX_CALLS_PER_RUN)

        assert f"at most {DefaultToolBudget.MAX_CALLS_PER_RUN} invocations" in suffix
        assert "including calls your delegated subagents make" in suffix
