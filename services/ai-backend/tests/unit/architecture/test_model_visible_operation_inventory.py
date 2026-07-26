"""F-014: exhaustive model-tool descriptor and presentation-path canaries."""

from __future__ import annotations

from pathlib import Path

from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.operations.builtin_catalog import (
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.delegation.subagents.atlas_task_tool import build_atlas_task_tool
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import _model_visible_tools
from tests.unit.architecture.model_visible_operation_inventory import (
    direct_bespoke_surface_violations,
    model_tool_descriptor_violations,
)


_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"


class _FeatureTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} test tool"

    async def ainvoke(self, _value: object) -> dict[str, object]:
        return {"ok": True}


class _AuthProvider:
    async def create_auth_session(self, **_kwargs: object) -> object:
        raise AssertionError("tool inventory must not start OAuth")


class _McpRegistry:
    providers = (_AuthProvider(),)

    async def resolve_server(self, _name: str) -> object:
        raise AssertionError("tool inventory must not resolve an MCP server")


class _SkillRegistry:
    async def load_skill_by_name(self, _name: str) -> object:
        raise AssertionError("tool inventory must not load a skill")


def _tool(name: str) -> StructuredTool:
    async def invoke(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=f"{name} test tool",
    )


def _fully_enabled_factory_tools(
    runtime_context_admin: AgentRuntimeContext,
) -> tuple[object, ...]:
    return _model_visible_tools(
        tools=(_tool("web_search"),),
        mcp_registry=_McpRegistry(),
        skill_registry=_SkillRegistry(),
        prior_tool_result_loader=object(),
        mcp_discovery_cache=None,
        code_mode_tool=_tool("run_code_mode"),
        sandbox_execute_tool=_tool("run_in_sandbox"),
        stage_rowset_write_tool=_FeatureTool("stage_rowset_write"),
        publish_artifact_tool=_FeatureTool("publish_artifact"),
        runtime_context=runtime_context_admin,
    )


def _framework_tools() -> tuple[object, ...]:
    task = build_atlas_task_tool(
        (
            {
                "name": "researcher",
                "description": "Researches.",
                "runnable": RunnableLambda(lambda value: value),
            },
        )
    )
    return (*TodoListMiddleware().tools, *FilesystemMiddleware().tools, task)


def test_every_assembled_model_tool_has_one_catalog_descriptor(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    tools = (*_fully_enabled_factory_tools(runtime_context_admin), *_framework_tools())

    assert model_tool_descriptor_violations(tools) == ()
    actual_keys = {
        DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_model_tool_name(tool.name).key
        for tool in tools
    }
    expected_keys = {
        entry.key for entry in DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()
    }
    assert actual_keys == expected_keys
    assert all(
        DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*entry.key) is not None
        for entry in DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()
    )


def test_unregistered_model_visible_capability_canary_fails_closed(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    tools = (
        *_fully_enabled_factory_tools(runtime_context_admin),
        _tool("planted_f014"),
    )

    assert model_tool_descriptor_violations(tools)[-1:] == (
        "unregistered model-visible capability: planted_f014",
    )


def test_no_model_capability_can_construct_a_bespoke_surface_result() -> None:
    assert direct_bespoke_surface_violations(_SOURCE_ROOT) == ()


def test_direct_bespoke_surface_result_canary_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime" / "capabilities" / "rogue_tool.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "def model_visible_tool():\n    return {'surface': {'kind': 'table'}}\n",
        encoding="utf-8",
    )

    assert direct_bespoke_surface_violations(tmp_path) == (
        "agent_runtime/capabilities/rogue_tool.py:2:direct-surface-result",
    )


def test_direct_surface_envelope_canary_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime" / "capabilities" / "rogue_tool.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "def model_visible_tool():\n"
        "    return SurfaceEnvelope(surface_uri='surface://rogue')\n",
        encoding="utf-8",
    )

    assert direct_bespoke_surface_violations(tmp_path) == (
        "agent_runtime/capabilities/rogue_tool.py:2:direct-surface-envelope",
    )


def test_aliased_direct_surface_envelope_canary_is_detected(tmp_path: Path) -> None:
    rogue = tmp_path / "agent_runtime" / "capabilities" / "rogue_tool.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text(
        "from agent_runtime.capabilities.surfaces.spec_models import SurfaceEnvelope as Envelope\n"
        "\n"
        "def model_visible_tool():\n"
        "    return Envelope(surface_uri='surface://rogue')\n",
        encoding="utf-8",
    )

    assert direct_bespoke_surface_violations(tmp_path) == (
        "agent_runtime/capabilities/rogue_tool.py:4:direct-surface-envelope",
    )
