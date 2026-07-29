"""Pinned framework contracts for the graph-wide runtime middleware seam.

These tests intentionally fail on dependency API drift.  The runtime relies on
the public Deep Agents and LangChain middleware APIs below to cover tools added
after factory assembly; silently losing one of these seams would create a
policy and result-admission bypass.
"""

from __future__ import annotations

import dataclasses
import inspect
from importlib.metadata import version
from typing import Any, cast

from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest


_EXPECTED_FRAMEWORK_VERSIONS = {
    "deepagents": "0.6.12",
    "langchain": "1.3.14",
    # ``ToolCallRequest`` is re-exported by LangChain but implemented by
    # LangGraph, so its distribution pin is part of this contract too.
    "langgraph": "1.2.9",
}


def _parameter_contract(callable_: object) -> tuple[tuple[str, str, str], ...]:
    """Return the signature facts that affect how this runtime calls an API."""

    parameters = inspect.signature(callable_).parameters.values()
    return tuple(
        (
            parameter.name,
            parameter.kind.name,
            (
                "<required>"
                if parameter.default is inspect.Parameter.empty
                else repr(parameter.default)
            ),
        )
        for parameter in parameters
    )


def test_installed_framework_versions_match_the_service_pins() -> None:
    actual = {
        distribution: version(distribution)
        for distribution in _EXPECTED_FRAMEWORK_VERSIONS
    }

    assert actual == _EXPECTED_FRAMEWORK_VERSIONS, (
        "The installed agent framework versions no longer match the reviewed "
        "middleware contract. Update this contract together with "
        "services/ai-backend/pyproject.toml and requirements.txt after "
        f"reviewing the new APIs. expected={_EXPECTED_FRAMEWORK_VERSIONS!r}, "
        f"actual={actual!r}"
    )


def test_create_deep_agent_public_signature_is_pinned() -> None:
    expected = (
        ("model", "POSITIONAL_OR_KEYWORD", "None"),
        ("tools", "POSITIONAL_OR_KEYWORD", "None"),
        ("system_prompt", "KEYWORD_ONLY", "None"),
        ("middleware", "KEYWORD_ONLY", "()"),
        ("subagents", "KEYWORD_ONLY", "None"),
        ("skills", "KEYWORD_ONLY", "None"),
        ("memory", "KEYWORD_ONLY", "None"),
        ("permissions", "KEYWORD_ONLY", "None"),
        ("backend", "KEYWORD_ONLY", "None"),
        ("interrupt_on", "KEYWORD_ONLY", "None"),
        ("response_format", "KEYWORD_ONLY", "None"),
        ("state_schema", "KEYWORD_ONLY", "None"),
        ("context_schema", "KEYWORD_ONLY", "None"),
        ("checkpointer", "KEYWORD_ONLY", "None"),
        ("store", "KEYWORD_ONLY", "None"),
        ("debug", "KEYWORD_ONLY", "False"),
        ("name", "KEYWORD_ONLY", "None"),
        ("cache", "KEYWORD_ONLY", "None"),
    )
    actual = _parameter_contract(create_deep_agent)

    assert actual == expected, (
        "deepagents.create_deep_agent signature drifted. The runtime builder "
        "depends on its keyword-only middleware and harness assembly surface; "
        "review deep_agent_builder.py before accepting the new signature. "
        f"expected={expected!r}, actual={actual!r}"
    )
    middleware_annotation = (
        inspect.signature(create_deep_agent).parameters["middleware"].annotation
    )
    assert "Sequence" in str(middleware_annotation)
    assert "AgentMiddleware" in str(middleware_annotation)


def test_harness_profile_public_surface_is_pinned() -> None:
    expected_fields = (
        "base_system_prompt",
        "system_prompt_suffix",
        "tool_description_overrides",
        "excluded_tools",
        "excluded_middleware",
        "extra_middleware",
        "general_purpose_subagent",
    )
    actual_fields = tuple(field.name for field in dataclasses.fields(HarnessProfile))
    profile_parameters = inspect.signature(HarnessProfile).parameters

    assert actual_fields == expected_fields, (
        "deepagents.HarnessProfile fields drifted. The runtime depends on "
        "excluded_tools and callable extra_middleware being applied to the "
        "supervisor and local subagents. "
        f"expected={expected_fields!r}, actual={actual_fields!r}"
    )
    assert profile_parameters["extra_middleware"].default == (), (
        "HarnessProfile.extra_middleware no longer has the reviewed empty "
        "sequence default."
    )
    extra_middleware_annotation = str(profile_parameters["extra_middleware"].annotation)
    assert "Sequence" in extra_middleware_annotation
    assert "Callable" in extra_middleware_annotation

    def middleware_factory() -> tuple[AgentMiddleware, ...]:
        return (AgentMiddleware(),)

    profile = HarnessProfile(
        system_prompt_suffix="runtime suffix",
        excluded_tools=frozenset({"execute"}),
        extra_middleware=middleware_factory,
    )
    assert profile.system_prompt_suffix == "runtime suffix"
    assert profile.excluded_tools == frozenset({"execute"})
    assert profile.extra_middleware is middleware_factory


def test_register_harness_profile_public_signature_is_pinned() -> None:
    expected = (
        ("key", "POSITIONAL_OR_KEYWORD", "<required>"),
        ("profile", "POSITIONAL_OR_KEYWORD", "<required>"),
    )
    actual = _parameter_contract(register_harness_profile)

    assert actual == expected, (
        "deepagents.register_harness_profile signature drifted. The runtime "
        "registers provider-keyed profiles before graph construction. "
        f"expected={expected!r}, actual={actual!r}"
    )


def test_langchain_create_agent_middleware_parameter_is_pinned() -> None:
    signature = inspect.signature(create_agent)
    expected_names = (
        "model",
        "tools",
        "system_prompt",
        "middleware",
        "response_format",
        "state_schema",
        "context_schema",
        "checkpointer",
        "store",
        "interrupt_before",
        "interrupt_after",
        "debug",
        "name",
        "cache",
        "transformers",
    )

    assert tuple(signature.parameters) == expected_names, (
        "langchain.agents.create_agent signature drifted. Deep Agents composes "
        "the reviewed runtime middleware through this public factory. "
        f"expected={expected_names!r}, actual={tuple(signature.parameters)!r}"
    )
    middleware = signature.parameters["middleware"]
    assert middleware.kind is inspect.Parameter.KEYWORD_ONLY
    assert middleware.default == ()
    assert "AgentMiddleware" in str(middleware.annotation), (
        "create_agent.middleware no longer advertises AgentMiddleware values: "
        f"{middleware.annotation!r}"
    )


def test_agent_middleware_tool_hook_signatures_are_pinned() -> None:
    expected = (
        ("self", "POSITIONAL_OR_KEYWORD", "<required>"),
        ("request", "POSITIONAL_OR_KEYWORD", "<required>"),
        ("handler", "POSITIONAL_OR_KEYWORD", "<required>"),
    )
    sync_actual = _parameter_contract(AgentMiddleware.wrap_tool_call)
    async_actual = _parameter_contract(AgentMiddleware.awrap_tool_call)

    assert sync_actual == expected, (
        "AgentMiddleware.wrap_tool_call signature drifted; graph-wide sync "
        "tool admission may no longer wrap the final tool surface. "
        f"expected={expected!r}, actual={sync_actual!r}"
    )
    assert async_actual == expected, (
        "AgentMiddleware.awrap_tool_call signature drifted; graph-wide async "
        "tool admission may no longer wrap the final tool surface. "
        f"expected={expected!r}, actual={async_actual!r}"
    )
    assert not inspect.iscoroutinefunction(AgentMiddleware.wrap_tool_call)
    assert inspect.iscoroutinefunction(AgentMiddleware.awrap_tool_call)
    sync_annotations = inspect.get_annotations(
        AgentMiddleware.wrap_tool_call,
        eval_str=False,
    )
    async_annotations = inspect.get_annotations(
        AgentMiddleware.awrap_tool_call,
        eval_str=False,
    )
    assert sync_annotations["request"] == "ToolCallRequest"
    assert "ToolMessage | Command" in sync_annotations["handler"]
    assert async_annotations["request"] == "ToolCallRequest"
    assert "Awaitable[ToolMessage | Command" in async_annotations["handler"]


def test_tool_call_request_surface_is_pinned() -> None:
    expected = (
        ("tool_call", "POSITIONAL_OR_KEYWORD", "<required>"),
        ("tool", "POSITIONAL_OR_KEYWORD", "<required>"),
        ("state", "POSITIONAL_OR_KEYWORD", "<required>"),
        ("runtime", "POSITIONAL_OR_KEYWORD", "<required>"),
    )
    actual = _parameter_contract(ToolCallRequest)
    field_names = tuple(field.name for field in dataclasses.fields(ToolCallRequest))

    assert actual == expected, (
        "ToolCallRequest constructor drifted. Runtime tool control reads "
        "tool_call, tool, state, and runtime at the common interception seam. "
        f"expected={expected!r}, actual={actual!r}"
    )
    assert field_names == tuple(item[0] for item in expected)

    original_call = {
        "name": "read_file",
        "args": {"file_path": "/workspace/report.md"},
        "id": "call-1",
        "type": "tool_call",
    }
    request = ToolCallRequest(
        tool_call=original_call,
        tool=None,
        state={"messages": []},
        runtime=cast(Any, object()),
    )
    replacement_call = {**original_call, "id": "call-2"}
    overridden = request.override(tool_call=replacement_call)

    assert request.tool_call is original_call
    assert overridden is not request
    assert overridden.tool_call == replacement_call
    assert overridden.tool is request.tool
    assert overridden.state is request.state
    assert overridden.runtime is request.runtime
