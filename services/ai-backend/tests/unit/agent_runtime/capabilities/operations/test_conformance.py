from __future__ import annotations

from datetime import date, timedelta

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.conformance import (
    CapabilityRegistration,
    OperationConformanceError,
    OperationConformanceGate,
    OperationDescriptorExemption,
    current_capability_registrations,
    registrations_from_model_tools,
)
from agent_runtime.delegation.subagents.atlas_task_tool import (
    build_atlas_task_tool,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import _model_visible_tools
from agent_runtime.surfaces_v2.ledger_models import EffectClass


def _tool(name: str) -> StructuredTool:
    async def invoke(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=f"{name} test tool",
    )


def _framework_model_tools() -> tuple[object, ...]:
    task = build_atlas_task_tool(
        (
            {
                "name": "researcher",
                "description": "Researches.",
                "runnable": RunnableLambda(lambda value: value),
            },
        )
    )
    return (
        *TodoListMiddleware().tools,
        *FilesystemMiddleware().tools,
        task,
    )


class TestOperationConformance:
    def test_current_inventory_has_exact_descriptor_or_exemption_coverage(
        self,
    ) -> None:
        OperationConformanceGate.validate_current()

    @pytest.mark.parametrize(
        "op",
        [
            "browser_navigate",
            "browser_snapshot",
            "browser_wait",
            "browser_screenshot",
            "browser_close",
        ],
    )
    def test_desktop_browser_dispatch_keys_resolve_exact_descriptors(
        self,
        op: str,
    ) -> None:
        entry = DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(
            "desktop_browser",
            op,
        )

        assert entry is not None
        assert entry.descriptor.capability == "desktop-browser"
        assert entry.descriptor.op == op

    def test_duplicate_registration_fails(self) -> None:
        registration = CapabilityRegistration(
            capability="builtin",
            op="web_search",
            source="one",
        )
        with pytest.raises(
            OperationConformanceError,
            match="duplicate capability registration",
        ):
            OperationConformanceGate.validate(
                registrations=(registration, registration.model_copy()),
            )

    def test_expired_exemption_fails_closed(self) -> None:
        registration = CapabilityRegistration(
            capability="future-provider",
            op="future-tool",
            source="test",
        )
        exemption = OperationDescriptorExemption(
            capability="future-provider",
            op="future-tool",
            owner="runtime",
            expires_on=date.today() - timedelta(days=1),
            reason="temporary",
            safe_default_classification=EffectClass.UNKNOWN,
        )
        with pytest.raises(OperationConformanceError, match="expired"):
            OperationConformanceGate.validate(
                registrations=(registration,),
                exemptions=(exemption,),
            )

    def test_descriptor_and_exemption_cannot_both_cover_operation(self) -> None:
        registration = CapabilityRegistration(
            capability="builtin",
            op="web_search",
            source="test",
        )
        exemption = OperationDescriptorExemption(
            capability="builtin",
            op="web_search",
            owner="runtime",
            expires_on=date.today() + timedelta(days=1),
            reason="invalid duplicate authority",
            safe_default_classification=EffectClass.UNKNOWN,
        )
        with pytest.raises(OperationConformanceError, match="both"):
            OperationConformanceGate.validate(
                registrations=(registration,),
                exemptions=(exemption,),
            )

    def test_unregistered_operation_fails(self) -> None:
        with pytest.raises(
            OperationConformanceError,
            match="unregistered model-facing operation",
        ):
            OperationConformanceGate.validate(
                registrations=(
                    CapabilityRegistration(
                        capability="builtin",
                        op="planted_fake",
                        source="test",
                    ),
                )
            )

    def test_concrete_factory_tool_surface_matches_inventory(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        actual_tools = _model_visible_tools(
            tools=(_tool("web_search"),),
            mcp_registry=object(),
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            code_mode_tool=_tool("run_code_mode"),
            sandbox_execute_tool=_tool("run_in_sandbox"),
            runtime_context=runtime_context_admin,
        )

        actual = registrations_from_model_tools(actual_tools)
        assert {registration.op for registration in actual} == {
            "web_search",
            "ask_a_question",
            "suggest_mcp_connector",
            "run_code_mode",
            "run_in_sandbox",
        }
        OperationConformanceGate.validate_model_tool_surface(actual_tools)

    def test_concrete_framework_tool_surface_matches_inventory(self) -> None:
        actual_tools = _framework_model_tools()
        actual = registrations_from_model_tools(actual_tools)

        assert {registration.key for registration in actual} == {
            ("builtin", "write_todos"),
            ("builtin", "execute"),
            ("builtin", "task"),
            ("workspace", "ls"),
            ("workspace", "read"),
            ("workspace", "write"),
            ("workspace", "edit"),
            ("workspace", "glob"),
            ("workspace", "grep"),
        }
        OperationConformanceGate.validate_model_tool_surface(actual_tools)

    def test_planted_fake_on_concrete_factory_surface_trips_gate(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        actual_tools = _model_visible_tools(
            tools=(_tool("web_search"),),
            mcp_registry=object(),
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            runtime_context=runtime_context_admin,
        )

        with pytest.raises(
            OperationConformanceError,
            match="missing from inventory",
        ):
            OperationConformanceGate.validate_model_tool_surface(
                (*actual_tools, _tool("planted_fake"))
            )

    def test_planted_fake_on_framework_surface_trips_gate(self) -> None:
        with pytest.raises(
            OperationConformanceError,
            match="missing from inventory",
        ):
            OperationConformanceGate.validate_model_tool_surface(
                (*_framework_model_tools(), _tool("planted_fake"))
            )

    def test_inventory_is_not_a_self_validating_subset(self) -> None:
        inventory = {item.key for item in current_capability_registrations()}
        descriptors = {
            (
                entry.descriptor.capability,
                entry.descriptor.op,
            )
            for entry in DEFAULT_OPERATION_DESCRIPTORS.all_entries()
        }
        assert ("builtin", "planted_fake") not in inventory
        assert ("builtin", "planted_fake") not in descriptors
