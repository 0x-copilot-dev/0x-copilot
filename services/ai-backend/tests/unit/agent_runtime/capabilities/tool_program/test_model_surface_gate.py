"""``run_tool_program``'s gate, asserted where the tokens are actually spent.

A flag test that reads the flag back proves nothing about cost. What a run pays
for is the **schema block handed to the provider on every model call** — 600
tokens for this tool, measured from ``context_occupancy.jsonl`` on the packaged
app (``tools/harness-bench/FINDINGS.md`` §3) — so that is what these tests
assert: the composed model tool surface, converted to the provider tool payload,
must not contain this tool's name, description or argument schema when the gate
is off, and must contain all three when it is on.

The chain driven here is the production one, not a stand-in:

    CapabilityToolWiring.tool_program_factory  (reads tool_program.enabled)
        -> RuntimeDependencies.tool_program_factory
            -> execution.factory._model_visible_tools  (appends, or does not)

The gate deliberately lives at the *first* hop. A tool registered at the third
hop and refusing at call time would still cost every one of those 600 tokens,
because the schema is billed whether or not the model ever calls it.
"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from agent_runtime.capabilities.tool_program.tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import _model_visible_tools
from agent_runtime.hyperparameters.contracts import (
    Hyperparameters,
    ToolProgramHyperparameters,
)
from agent_runtime.hyperparameters.loader import HyperparameterLoader
from runtime_worker.capability_tool_wiring import CapabilityToolWiring


class ToolProgramModelSurfaceMixin:
    """Compose the real model tool surface with the gate in either position."""

    #: A distinctive fragment of the tool's own description. Asserting on the
    #: rendered text rather than only on the name is what makes this a claim
    #: about *tokens*: a tool absent from the name list but whose description
    #: still rode along in some wrapper would fail here.
    DESCRIPTION_FRAGMENT = "Run several tool calls as one plan"
    #: A field name unique to ``RunToolProgramInput``'s JSON schema.
    SCHEMA_FRAGMENT = "$from"

    @staticmethod
    def stub_tool(name: str) -> StructuredTool:
        async def invoke(value: str = "") -> str:
            return value

        return StructuredTool.from_function(
            coroutine=invoke, name=name, description=f"{name} test tool"
        )

    @staticmethod
    def document(*, enabled: bool) -> Hyperparameters:
        return Hyperparameters(tool_program=ToolProgramHyperparameters(enabled=enabled))

    @classmethod
    def factory(
        cls, runtime_context: AgentRuntimeContext, *, enabled: bool
    ) -> object | None:
        """Run the worker's own gate — not a hand-built factory."""

        return CapabilityToolWiring(
            runtime_context=runtime_context,
            env={},
            hyperparameters=cls.document(enabled=enabled),
        ).tool_program_factory()

    @classmethod
    def model_surface(
        cls, runtime_context: AgentRuntimeContext, *, enabled: bool
    ) -> tuple[object, ...]:
        """The tools the graph binds to the model, composed exactly as in a run."""

        return _model_visible_tools(
            tools=(cls.stub_tool("web_search"),),
            mcp_registry=object(),
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            tool_program_factory=cls.factory(runtime_context, enabled=enabled),
            runtime_context=runtime_context,
        )

    @staticmethod
    def tool_names(tools: tuple[object, ...]) -> tuple[str, ...]:
        return tuple(str(getattr(tool, "name", "")) for tool in tools)

    @staticmethod
    def provider_tool_block(tools: tuple[object, ...]) -> str:
        """Serialize the surface the way the provider request carries it.

        ``convert_to_openai_tool`` is the same conversion LangChain performs
        when binding tools, so this string is the tool block whose token count
        the occupancy ledger reports — not a proxy for it.
        """

        return json.dumps([convert_to_openai_tool(tool) for tool in tools])


class TestToolProgramSchemaIsAbsentWhenGatedOff(ToolProgramModelSurfaceMixin):
    def test_disabled_run_composes_no_program_tool(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        assert TOOL_NAME not in self.tool_names(
            self.model_surface(runtime_context_admin, enabled=False)
        )

    def test_disabled_run_carries_none_of_the_schema_tokens(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """The 600 tokens, asserted as the bytes that produce them."""

        block = self.provider_tool_block(
            self.model_surface(runtime_context_admin, enabled=False)
        )

        assert TOOL_NAME not in block
        assert self.DESCRIPTION_FRAGMENT not in block
        assert self.SCHEMA_FRAGMENT not in block

    def test_the_gate_withholds_the_factory_rather_than_the_tool(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """Nothing downstream gets the chance to build it.

        A factory that existed and returned ``None`` would be indistinguishable
        in the surface assertions above, and would leave the 600 tokens one
        careless edit away from coming back.
        """

        assert self.factory(runtime_context_admin, enabled=False) is None


class TestToolProgramSchemaReturnsWhenEnabled(ToolProgramModelSurfaceMixin):
    def test_enabled_run_composes_the_program_tool_last(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        names = self.tool_names(self.model_surface(runtime_context_admin, enabled=True))

        assert names[-1] == TOOL_NAME

    def test_enabled_run_carries_the_full_schema(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        block = self.provider_tool_block(
            self.model_surface(runtime_context_admin, enabled=True)
        )

        assert TOOL_NAME in block
        assert self.DESCRIPTION_FRAGMENT in block
        assert self.SCHEMA_FRAGMENT in block

    def test_the_description_fragment_is_really_this_tool_s_own_text(self) -> None:
        """Guard the guard: a reworded description must not silently pass."""

        assert self.DESCRIPTION_FRAGMENT in TOOL_DESCRIPTION

    def test_enabling_costs_measurably_more_prompt_than_disabling(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """State the saving as a number, in the direction the ledger measures.

        The absolute figure is model-tokenizer dependent, so the assertion is on
        the byte delta of the provider tool block: turning the gate on adds this
        tool's whole schema and nothing else changes.
        """

        off = self.provider_tool_block(
            self.model_surface(runtime_context_admin, enabled=False)
        )
        on = self.provider_tool_block(
            self.model_surface(runtime_context_admin, enabled=True)
        )

        assert len(on) > len(off)
        assert len(on) - len(off) > 500


class TestShippedDocumentKeepsItOff(ToolProgramModelSurfaceMixin):
    def test_the_checked_in_document_ships_disabled(self) -> None:
        """The default that ships is the one the measurement argued for."""

        assert HyperparameterLoader.default().tool_program.enabled is False

    def test_an_operator_can_turn_it_back_on_without_a_code_change(self) -> None:
        """One documented override, bound-checked through the same model."""

        document = HyperparameterLoader.with_overrides(
            HyperparameterLoader.default(),
            env={"COPILOT_HP__TOOL_PROGRAM__ENABLED": "true"},
        )

        assert document.tool_program.enabled is True
