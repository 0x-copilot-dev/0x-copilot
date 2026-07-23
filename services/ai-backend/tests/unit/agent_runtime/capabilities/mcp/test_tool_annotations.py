"""MCP tool annotations capture + registry (PRD-C1).

(a) ``McpToolAnnotations.from_wire`` reads only the three camelCase hints,
    ignores ``title`` / vendor keys, coerces non-bools to None.
(b) ``McpToolAnnotationsRegistry`` bind/register/get/unbind isolation with a
    normalized composite key (seed-prefixed vs bare connector round-trip).
(c) ``BackendMcpClient._tool_descriptor`` registers wire annotations on the
    bound registry, tolerates garbage, and leaves the descriptor dump
    byte-identical (flag-off invariant).
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.mcp.backend_provider import BackendMcpClient
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

_SERVER_ID = "seed:linear"
_SERVER_NAME = "linear"


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model_name="gpt-4o-mini",
        max_input_tokens=4096,
        timeout_seconds=30,
        temperature=0.0,
    )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        permission_scopes={"docs:read"},
        model_profile=_model_config(),
        trace_id="trace_annotations",
    )


def _card() -> McpServerCard:
    return McpServerCard(
        name=_SERVER_NAME,
        server_id=_SERVER_ID,
        short_description="Linear issues.",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        required_scopes=frozenset({"docs:read"}),
        health=McpServerHealth.HEALTHY,
        load_cost=10,
    )


def _client() -> BackendMcpClient:
    return BackendMcpClient(
        backend_url="http://backend.local",
        runtime_context=_context(),
        card=_card(),
    )


class TestFromWire:
    def test_reads_three_hints_ignores_others(self) -> None:
        ann = McpToolAnnotations.from_wire(
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "title": "Human Title",
                "openWorldHint": True,
                "vendorExtra": {"x": 1},
            }
        )
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is True

    def test_absent_keys_are_none(self) -> None:
        ann = McpToolAnnotations.from_wire({})
        assert ann.read_only_hint is None
        assert ann.destructive_hint is None
        assert ann.idempotent_hint is None

    def test_non_bool_coerces_to_none(self) -> None:
        ann = McpToolAnnotations.from_wire(
            {"readOnlyHint": "true", "destructiveHint": 1}
        )
        assert ann.read_only_hint is None
        assert ann.destructive_hint is None


class TestRegistry:
    def test_get_is_none_when_unbound(self) -> None:
        assert McpToolAnnotationsRegistry.active() is None
        assert McpToolAnnotationsRegistry.get("linear", "get_issue") is None
        # register is a safe no-op when unbound.
        McpToolAnnotationsRegistry.register(
            "linear", "get_issue", McpToolAnnotations.from_wire({"readOnlyHint": True})
        )
        assert McpToolAnnotationsRegistry.get("linear", "get_issue") is None

    def test_bind_register_get_unbind_isolation(self) -> None:
        registry: dict = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            McpToolAnnotationsRegistry.register(
                "seed:linear",
                "Get_Issue",
                McpToolAnnotations.from_wire({"readOnlyHint": True}),
            )
            # Read side passes the model-supplied bare name + different case —
            # both sides normalize, so the composite key round-trips.
            got = McpToolAnnotationsRegistry.get("linear", "get_issue")
            assert got is not None
            assert got.read_only_hint is True
        finally:
            McpToolAnnotationsRegistry.unbind(token)
        assert McpToolAnnotationsRegistry.active() is None

    def test_composite_key_disambiguates_by_connector(self) -> None:
        registry: dict = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            McpToolAnnotationsRegistry.register(
                "linear", "search", McpToolAnnotations.from_wire({"readOnlyHint": True})
            )
            McpToolAnnotationsRegistry.register(
                "github",
                "search",
                McpToolAnnotations.from_wire({"destructiveHint": True}),
            )
            assert (
                McpToolAnnotationsRegistry.get("linear", "search").read_only_hint
                is True
            )
            assert (
                McpToolAnnotationsRegistry.get("github", "search").destructive_hint
                is True
            )
        finally:
            McpToolAnnotationsRegistry.unbind(token)


class TestToolDescriptorCapture:
    _BASE = {
        "name": "list_issues",
        "description": "List Linear issues.",
        "inputSchema": {"type": "object", "properties": {}},
    }

    def test_wire_annotations_registered_on_bound_registry(self) -> None:
        registry: dict = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            _client()._tool_descriptor(
                {**self._BASE, "annotations": {"readOnlyHint": True, "title": "x"}}
            )
            # Registered under (card.name slug, tool slug); read with the
            # model-supplied server_name.
            got = McpToolAnnotationsRegistry.get(_SERVER_NAME, "list_issues")
            assert got is not None
            assert got.read_only_hint is True
        finally:
            McpToolAnnotationsRegistry.unbind(token)

    def test_garbage_annotations_register_no_entry(self) -> None:
        registry: dict = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            _client()._tool_descriptor({**self._BASE, "annotations": "lol"})
            assert McpToolAnnotationsRegistry.get(_SERVER_NAME, "list_issues") is None
            assert registry == {}
        finally:
            McpToolAnnotationsRegistry.unbind(token)

    def test_descriptor_dump_byte_identical_with_and_without_binding(self) -> None:
        # Registry capture must not change the descriptor payload.
        tool = {**self._BASE, "annotations": {"readOnlyHint": True}}
        without = _client()._tool_descriptor(tool).model_dump()

        registry: dict = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            with_binding = _client()._tool_descriptor(tool).model_dump()
        finally:
            McpToolAnnotationsRegistry.unbind(token)

        assert without == with_binding
