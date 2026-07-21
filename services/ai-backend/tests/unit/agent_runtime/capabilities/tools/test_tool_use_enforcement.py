"""D2 — run-start enforcement of the tool-use policy on the model tool surface.

Proves the stored policy is ENFORCED (not merely stored): the resolver builds
the run snapshot from ``user_policies_json`` and fails open to deployment
defaults, and the enforcer routes ``call_mcp_tool`` to the existing approval
interrupt (ask/require), a blocked result (block), or straight through (auto).
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from agent_runtime.capabilities.tools.tool_use_enforcement import (
    PolicyBlockedTool,
    ToolUsePolicyEnforcer,
    ToolUsePolicyResolver,
)
from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
    ToolUsePolicySnapshot,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_CALL_MCP_TOOL = "call_mcp_tool"


class _McpArgs(BaseModel):
    server_name: str = ""
    tool_name: str = ""


class ToolSurfaceMixin:
    """Builders for a representative model tool surface + runtime context."""

    @staticmethod
    def _model_config() -> ModelConfig:
        return ModelConfig(
            provider="Fake",
            model_name="fake-model",
            max_input_tokens=128_000,
            timeout_seconds=30,
            temperature=0,
            supports_streaming=True,
        )

    def context(
        self, user_policies_json: dict[str, object] | None
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_1",
            org_id="org_1",
            roles={"Admin"},
            permission_scopes=set(),
            connector_scopes={},
            model_profile=self._model_config(),
            trace_id="trace_1",
            user_policies_json=user_policies_json or {},
        )

    @staticmethod
    def _tool(name: str) -> StructuredTool:
        def _noop(server_name: str = "", tool_name: str = "") -> dict[str, str]:
            return {"server_name": server_name, "tool_name": tool_name}

        return StructuredTool.from_function(
            func=_noop,
            name=name,
            description=f"{name} description",
            args_schema=_McpArgs,
        )

    def model_tools(self) -> tuple[object, ...]:
        # A read-only meta tool + the gated umbrella MCP tool.
        return (self._tool("load_mcp_server"), self._tool(_CALL_MCP_TOOL))

    @staticmethod
    def _snapshot(
        kind: ToolUsePolicyKind, mode: ToolUsePolicyMode
    ) -> ToolUsePolicySnapshot:
        return ToolUsePolicySnapshot({kind: mode})


class TestResolverFailOpen(ToolSurfaceMixin):
    def test_absent_tool_use_falls_back_to_deployment_defaults(self) -> None:
        snapshot = ToolUsePolicyResolver.resolve(self.context(None))
        # Deployment default: read=auto, write=ask, destructive=require.
        assert snapshot.mode_for_kind(ToolUsePolicyKind.READ) is ToolUsePolicyMode.AUTO
        assert snapshot.mode_for_kind(ToolUsePolicyKind.WRITE) is ToolUsePolicyMode.ASK
        assert (
            snapshot.mode_for_kind(ToolUsePolicyKind.DESTRUCTIVE)
            is ToolUsePolicyMode.REQUIRE
        )

    def test_malformed_tool_use_falls_back_to_defaults(self) -> None:
        # A non-mapping ``tool_use`` (e.g. corrupt snapshot) must not raise.
        snapshot = ToolUsePolicyResolver.resolve(self.context({"tool_use": "nonsense"}))
        assert snapshot.mode_for_kind(ToolUsePolicyKind.WRITE) is ToolUsePolicyMode.ASK

    def test_snapshot_built_from_representative_payload(self) -> None:
        # Wire shape produced by /internal/v1/policies/runtime: workspace default
        # + a user override that wins for its axis.
        payload = {
            "tool_use": {
                "workspace": {"read": "auto", "write": "ask", "destructive": "require"},
                "user": {"write": "block"},
            },
            "privacy": {"training_opt_out": True},
        }
        snapshot = ToolUsePolicyResolver.resolve(self.context(payload))
        # User override wins on write; unset axes inherit the workspace default.
        assert (
            snapshot.mode_for_kind(ToolUsePolicyKind.WRITE) is ToolUsePolicyMode.BLOCK
        )
        assert snapshot.mode_for_kind(ToolUsePolicyKind.READ) is ToolUsePolicyMode.AUTO
        assert (
            snapshot.mode_for_kind(ToolUsePolicyKind.DESTRUCTIVE)
            is ToolUsePolicyMode.REQUIRE
        )


class TestEnforcerRouting(ToolSurfaceMixin):
    def _names(self, tools: tuple[object, ...]) -> set[str]:
        return {str(getattr(tool, "name", "")) for tool in tools}

    def test_auto_dispatches_without_interrupt_or_wrapper(self) -> None:
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=self.model_tools(),
            snapshot=self._snapshot(ToolUsePolicyKind.WRITE, ToolUsePolicyMode.AUTO),
        )
        assert result.interrupt_on == {}
        # The umbrella tool is untouched (not wrapped) — behavior unchanged.
        assert self._names(result.tools) == {"load_mcp_server", _CALL_MCP_TOOL}
        assert not any(isinstance(tool, PolicyBlockedTool) for tool in result.tools)

    def test_ask_routes_through_approval_interrupt(self) -> None:
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=self.model_tools(),
            snapshot=self._snapshot(ToolUsePolicyKind.WRITE, ToolUsePolicyMode.ASK),
        )
        assert _CALL_MCP_TOOL in result.interrupt_on
        assert result.interrupt_on[_CALL_MCP_TOOL] == {
            "allowed_decisions": ["approve", "edit", "reject"]
        }
        assert not any(isinstance(tool, PolicyBlockedTool) for tool in result.tools)

    def test_require_routes_through_approval_interrupt(self) -> None:
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=self.model_tools(),
            snapshot=self._snapshot(ToolUsePolicyKind.WRITE, ToolUsePolicyMode.REQUIRE),
        )
        assert _CALL_MCP_TOOL in result.interrupt_on

    def test_block_denies_call_without_interrupt(self) -> None:
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=self.model_tools(),
            snapshot=self._snapshot(ToolUsePolicyKind.WRITE, ToolUsePolicyMode.BLOCK),
        )
        # Block is NOT an approval — no interrupt entry.
        assert result.interrupt_on == {}
        blocked = [tool for tool in result.tools if isinstance(tool, PolicyBlockedTool)]
        assert len(blocked) == 1
        # The blocked wrapper preserves the model-visible surface.
        assert blocked[0].name == _CALL_MCP_TOOL

    async def test_blocked_tool_returns_safe_message_without_crashing(self) -> None:
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=self.model_tools(),
            snapshot=self._snapshot(ToolUsePolicyKind.WRITE, ToolUsePolicyMode.BLOCK),
        )
        blocked = next(
            tool for tool in result.tools if isinstance(tool, PolicyBlockedTool)
        )
        # Calling it returns the safe rejection message — the run does not crash.
        output = await blocked.ainvoke({"server_name": "acme", "tool_name": "delete"})
        assert "Write tools are blocked" in output

    def test_default_snapshot_gates_call_mcp_tool_like_today(self) -> None:
        # Fail-open lane: an unconfigured run uses the deployment default
        # (write=ask) so ``call_mcp_tool`` still interrupts exactly as before
        # enforcement existed — no behavior change.
        snapshot = ToolUsePolicyResolver.resolve(self.context(None))
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=self.model_tools(),
            snapshot=snapshot,
        )
        assert _CALL_MCP_TOOL in result.interrupt_on

    def test_no_umbrella_tool_present_yields_empty_config(self) -> None:
        # A surface without ``call_mcp_tool`` (MCP unavailable) installs no HITL.
        result = ToolUsePolicyEnforcer.enforce(
            model_tools=(self._tool("load_mcp_server"),),
            snapshot=self._snapshot(ToolUsePolicyKind.WRITE, ToolUsePolicyMode.BLOCK),
        )
        assert result.interrupt_on == {}
        assert not any(isinstance(tool, PolicyBlockedTool) for tool in result.tools)
