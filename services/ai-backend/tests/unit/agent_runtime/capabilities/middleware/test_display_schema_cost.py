"""The display decoration is paid for once per tool, per turn — so pin its price.

``wrap_tool_with_display`` appends two optional display arguments to *every*
model-visible tool. The bytes are identical on all of them, so whatever those
two fields cost is multiplied by the width of the tool surface and re-sent on
every request. Tool schemas are not part of the cacheable stable prefix, so
that multiplication is paid in full each turn.

That makes this one of the few places where a docstring-sized edit has a
runtime bill attached, and where the bill is invisible at the edit site: a
future maintainer adding "just one more clarifying sentence" to a field
description is really adding it a dozen-odd times per request, forever. These
tests put a number on it.

The measurement deliberately takes a *difference* — wrapped minus bare, same
tool — rather than an absolute size. LangChain owns the surrounding JSON shape
and reformats it between versions; subtracting cancels that out and leaves only
what the decoration itself contributes.
"""

from __future__ import annotations

import json

import pytest
import tiktoken
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from agent_runtime.capabilities.middleware.display_metadata import (
    DISPLAY_SUMMARY_KEY,
    DISPLAY_TITLE_KEY,
    TOOL_CALL_ID_KEY,
    wrap_tool_with_display,
)
from agent_runtime.prompts.runtime import (
    DEFAULT_INSTRUCTIONS,
    DISPLAY_FIELD_CONVENTION,
)

# What the decoration is allowed to cost, in cl100k_base tokens, per tool.
#
# History, so the number is not mistaken for an arbitrary threshold:
#
# * 273 — as first measured, with the full authoring convention (examples and
#   counter-examples) repeated inside both field descriptions.
# * 220 — the same code measured against the OpenAI wire payload, which is what
#   actually crosses the network. The gap is measurement technique, not a fix.
# * 98  — after moving the convention into ``DISPLAY_FIELD_CONVENTION``, leaving
#   only each field's shape in the schema.
#
# The residue is mostly structural, not prose: of the ~49 tokens per field only
# ~18 are the description. The rest is the ``anyOf: [string, null]`` union, the
# ``default: null``, and the property name. That union is deliberately kept —
# collapsing it to a bare ``string`` would shave tokens by changing which values
# validate, and a model that emits an explicit ``null`` would start erroring.
#
# The ceiling sits a little above the measured value so a LangChain formatting
# change does not fail the build, but far enough below the old number that the
# convention cannot quietly migrate back into the schema.
MAX_DECORATION_TOKENS_PER_TOOL = 110

# The measured cost at the time of writing. Asserted as a floor-and-ceiling band
# rather than an equality so this is a budget, not a change-detector.
MEASURED_DECORATION_TOKENS_PER_TOOL = 98

# The cost before the convention moved to the system prompt, on the same OpenAI
# wire payload these tests price. Kept so the size of the trade stays visible.
PRE_RELOCATION_DECORATION_TOKENS_PER_TOOL = 220


class _SearchArgs(BaseModel):
    query: str = Field(description="The search query.")
    limit: int = Field(default=10, description="Max rows to return.")


class _ReadArgs(BaseModel):
    """Structurally unlike ``_SearchArgs``: fewer fields, different types."""

    path: str = Field(description="A filesystem path.")


class DisplayCostMixin:
    """Builds probe tools and prices their model-facing payload."""

    encoder = tiktoken.get_encoding("cl100k_base")

    @classmethod
    def _tokens(cls, payload: object) -> int:
        text = (
            payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        )
        return len(cls.encoder.encode(text))

    @staticmethod
    def _tool(name: str, args_schema: type[BaseModel]) -> StructuredTool:
        async def _invoke(**kwargs: object) -> str:
            return str(kwargs)

        return StructuredTool.from_function(
            coroutine=_invoke,
            name=name,
            description="A probe tool used for cost measurement.",
            args_schema=args_schema,
        )

    @classmethod
    def _openai_payload(cls, tool: object) -> dict:
        return convert_to_openai_tool(tool)

    @classmethod
    def _anthropic_payload(cls, tool: object) -> dict:
        """Mirror the shape ``langchain_anthropic`` binds, to price both vendors."""
        function = convert_to_openai_tool(tool)["function"]
        return {
            "name": function["name"],
            "description": function["description"],
            "input_schema": function["parameters"],
        }

    @classmethod
    def _decoration_cost(
        cls,
        name: str,
        args_schema: type[BaseModel],
        *,
        wire: str = "openai",
    ) -> int:
        """Return the token delta the display wrap adds to one tool."""

        render = cls._openai_payload if wire == "openai" else cls._anthropic_payload
        bare = cls._tokens(render(cls._tool(name, args_schema)))
        wrapped = cls._tokens(
            render(wrap_tool_with_display(cls._tool(name, args_schema)))
        )
        return wrapped - bare

    @classmethod
    def _model_visible_properties(cls, tool: object) -> dict:
        return cls._openai_payload(tool)["function"]["parameters"].get("properties", {})


class TestDecorationCost(DisplayCostMixin):
    """The per-tool price of the display wrap, pinned."""

    @pytest.mark.parametrize("wire", ["openai", "anthropic"])
    def test_the_decoration_stays_within_its_per_tool_budget(self, wire: str) -> None:
        cost = self._decoration_cost("search_probe", _SearchArgs, wire=wire)

        assert cost <= MAX_DECORATION_TOKENS_PER_TOOL, (
            f"the display decoration now costs {cost} tokens per tool on the "
            f"{wire} wire, over the {MAX_DECORATION_TOKENS_PER_TOOL} budget. "
            "Every model-visible tool pays this on every turn. Guidance that "
            "is identical for all tools belongs in DISPLAY_FIELD_CONVENTION "
            "(agent_runtime.prompts.runtime), which is stated once and is "
            "cacheable, not in the field descriptions."
        )

    def test_the_measured_cost_has_not_drifted_from_the_recorded_figure(self) -> None:
        """A loose band: catches silent growth without failing on reformatting."""

        cost = self._decoration_cost("search_probe", _SearchArgs)

        assert abs(cost - MEASURED_DECORATION_TOKENS_PER_TOOL) <= 12, (
            f"decoration cost moved to {cost} tokens from the recorded "
            f"{MEASURED_DECORATION_TOKENS_PER_TOOL}. If this is intended, "
            "re-measure and update the recorded figure and the rationale above."
        )

    def test_the_cost_is_identical_for_structurally_different_tools(self) -> None:
        """The premise of a single per-tool budget: the added bytes never vary.

        If this ever fails, the decoration has started depending on the tool it
        wraps, and a single multiplier no longer describes the surface cost.
        """

        search_cost = self._decoration_cost("search_probe", _SearchArgs)
        read_cost = self._decoration_cost("read_probe", _ReadArgs)

        assert search_cost == read_cost

    def test_moving_the_convention_to_the_prompt_is_net_cheaper(self) -> None:
        """The trade this design makes, asserted rather than assumed.

        Relocating the convention buys a per-tool, per-turn saving at the cost
        of one fixed block of prompt. That is only a win while the block stays
        smaller than the saving it unlocks across the surface — and the block
        is the easy thing to grow, since nothing about editing a prompt string
        suggests a per-tool multiplier is involved.
        """

        per_tool = self._decoration_cost("search_probe", _SearchArgs)
        surface_width = 14  # the deferred-posture model-visible surface
        saved_per_turn = (
            PRE_RELOCATION_DECORATION_TOKENS_PER_TOOL - per_tool
        ) * surface_width
        convention_cost = DisplayCostMixin._tokens(DISPLAY_FIELD_CONVENTION)

        assert convention_cost < saved_per_turn, (
            f"DISPLAY_FIELD_CONVENTION now costs {convention_cost} tokens "
            f"against a {saved_per_turn}-token surface-wide saving. It is "
            "charged on every turn too — it is merely charged once instead of "
            "once per tool, and only the stable prefix makes it cacheable."
        )


class TestWhatTheModelActuallySees(DisplayCostMixin):
    """The savings must not have come out of the model's ability to comply."""

    def test_both_display_arguments_remain_model_visible(self) -> None:
        properties = self._model_visible_properties(
            wrap_tool_with_display(self._tool("search_probe", _SearchArgs))
        )

        assert "display_title" in properties
        assert "display_summary" in properties

    def test_each_display_argument_still_carries_a_usable_description(self) -> None:
        """A pointer with no shape would push all the work onto the prompt."""

        properties = self._model_visible_properties(
            wrap_tool_with_display(self._tool("search_probe", _SearchArgs))
        )

        for name in ("display_title", "display_summary"):
            description = properties[name].get("description", "")
            assert description, f"{name} lost its description entirely"
            # Enough to convey shape at the point of use, not enough to be a
            # second copy of the convention.
            assert 5 <= self._tokens(description) <= 40, (
                f"{name} description is {self._tokens(description)} tokens"
            )

    def test_the_injected_tool_call_id_costs_the_model_nothing(self) -> None:
        """It is injected, never authored — so it must not reach the schema.

        This is also why the field names the model sees are the bare
        ``display_*`` forms: declaring an injected argument makes LangChain
        rebuild the model-facing schema, and that rebuild drops the
        ``_display_*`` aliases.
        """

        properties = self._model_visible_properties(
            wrap_tool_with_display(self._tool("search_probe", _SearchArgs))
        )

        assert TOOL_CALL_ID_KEY not in properties

    def test_the_wire_aliases_are_still_accepted_by_the_wrapped_schema(self) -> None:
        """Shortening prose must not narrow what validates."""

        schema = wrap_tool_with_display(
            self._tool("search_probe", _SearchArgs)
        ).args_schema

        assert schema.model_validate({"query": "q", DISPLAY_TITLE_KEY: "T"})
        assert schema.model_validate({"query": "q", "display_title": "T"})
        assert schema.model_validate({"query": "q", DISPLAY_SUMMARY_KEY: "S"})
        # The null branch of the union is load-bearing: it is why the schema
        # still carries ``anyOf`` rather than a cheaper bare ``string``.
        assert schema.model_validate({"query": "q", "display_title": None})


class TestConventionLivesInThePrompt:
    """What left the schema has to exist somewhere, exactly once."""

    def test_the_convention_is_part_of_the_default_instructions(self) -> None:
        assert DISPLAY_FIELD_CONVENTION in DEFAULT_INSTRUCTIONS

    def test_the_convention_names_the_arguments_the_model_is_shown(self) -> None:
        """The model never sees the ``_display_*`` aliases — see the test above."""

        assert "display_title" in DISPLAY_FIELD_CONVENTION
        assert "display_summary" in DISPLAY_FIELD_CONVENTION
        assert DISPLAY_TITLE_KEY not in DISPLAY_FIELD_CONVENTION
        assert DISPLAY_SUMMARY_KEY not in DISPLAY_FIELD_CONVENTION

    def test_the_convention_keeps_the_guidance_the_schema_gave_up(self) -> None:
        """Worked examples and the counter-example both survived the move.

        The counter-example matters most: without it the model writes a
        narrated sentence ("Searching Linear for...") instead of a label, and
        the activity card reads as prose.
        """

        assert "Q1 launch risk tickets" in DISPLAY_FIELD_CONVENTION
        assert "Risk-tagged tickets opened in the launch quarter" in (
            DISPLAY_FIELD_CONVENTION
        )
        assert "Searching Linear" in DISPLAY_FIELD_CONVENTION
        assert "never a sentence" in DISPLAY_FIELD_CONVENTION
