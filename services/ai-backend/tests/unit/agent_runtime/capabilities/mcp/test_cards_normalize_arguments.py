"""Unit tests for :class:`McpToolCallRequest` argument-shape normalization."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.mcp.cards import McpToolCallRequest


class NormalizeArgumentsMixin:
    """Shared constants for argument-shape normalization tests."""

    class TestValues:
        SERVER = "linear"
        TOOL = "list_issues"
        QUERY = "open"
        ASSIGNEE = "me"
        LIMIT = 50

        @classmethod
        def expected_arguments(cls) -> dict[str, object]:
            return {
                "assignee": cls.ASSIGNEE,
                "limit": cls.LIMIT,
                "query": cls.QUERY,
            }


class TestNormalizeArgumentShapes(NormalizeArgumentsMixin):
    """Five accepted input shapes collapse to one canonical shape."""

    @pytest.mark.parametrize(
        "raw_input_factory",
        [
            pytest.param(
                lambda v: {
                    "server_name": v.SERVER,
                    "tool_name": v.TOOL,
                    "arguments": v.expected_arguments(),
                },
                id="canonical",
            ),
            pytest.param(
                lambda v: {
                    "server_name": v.SERVER,
                    "tool_name": v.TOOL,
                    "parameters": v.expected_arguments(),
                },
                id="parameters-wrap",
            ),
            pytest.param(
                lambda v: {
                    "server_name": v.SERVER,
                    "tool_name": v.TOOL,
                    "params": v.expected_arguments(),
                },
                id="params-wrap",
            ),
            pytest.param(
                lambda v: {
                    "server_name": v.SERVER,
                    "tool_name": v.TOOL,
                    "args": v.expected_arguments(),
                },
                id="args-wrap",
            ),
            pytest.param(
                lambda v: {
                    "server_name": v.SERVER,
                    "tool_name": v.TOOL,
                    **v.expected_arguments(),
                },
                id="flat",
            ),
        ],
    )
    def test_all_shapes_produce_identical_canonical_arguments(
        self,
        raw_input_factory,
    ) -> None:
        request = McpToolCallRequest.model_validate(raw_input_factory(self.TestValues))

        assert request.server_name == self.TestValues.SERVER
        assert request.tool_name == self.TestValues.TOOL
        assert request.arguments == self.TestValues.expected_arguments()

    def test_canonical_wins_over_flat_extras_on_key_collision(self) -> None:
        request = McpToolCallRequest.model_validate(
            {
                "server_name": self.TestValues.SERVER,
                "tool_name": self.TestValues.TOOL,
                "arguments": {"query": "from_canonical"},
                "query": "from_flat",
                "limit": self.TestValues.LIMIT,
            }
        )

        assert request.arguments == {
            "query": "from_canonical",
            "limit": self.TestValues.LIMIT,
        }

    def test_canonical_takes_precedence_when_parameters_also_present(self) -> None:
        request = McpToolCallRequest.model_validate(
            {
                "server_name": self.TestValues.SERVER,
                "tool_name": self.TestValues.TOOL,
                "arguments": {"query": "from_canonical"},
                "parameters": {"query": "from_alias", "extra": 1},
            }
        )

        assert request.arguments == {"query": "from_canonical"}

    def test_non_dict_alias_value_is_treated_as_flat_extra(self) -> None:
        request = McpToolCallRequest.model_validate(
            {
                "server_name": self.TestValues.SERVER,
                "tool_name": self.TestValues.TOOL,
                "parameters": "not-a-dict",
                "query": self.TestValues.QUERY,
            }
        )

        assert request.arguments == {
            "parameters": "not-a-dict",
            "query": self.TestValues.QUERY,
        }

    def test_empty_input_yields_empty_arguments(self) -> None:
        request = McpToolCallRequest.model_validate(
            {
                "server_name": self.TestValues.SERVER,
                "tool_name": self.TestValues.TOOL,
            }
        )

        assert request.arguments == {}

    def test_aliased_wrap_with_empty_dict_yields_empty_arguments(self) -> None:
        request = McpToolCallRequest.model_validate(
            {
                "server_name": self.TestValues.SERVER,
                "tool_name": self.TestValues.TOOL,
                "parameters": {},
            }
        )

        assert request.arguments == {}
