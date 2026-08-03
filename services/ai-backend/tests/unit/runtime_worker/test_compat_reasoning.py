"""Reasoning on OpenAI-wire gateways: request it, then recognise it.

Two independent bugs kept every reasoning model behind OpenRouter silent, and
either one alone is enough to produce zero thinking:

1. the builder skipped `_openai_model_kwargs` for compat providers (correct — it
   re-routes onto `/responses`) and therefore asked for nothing at all;
2. the parser only knew typed content blocks, so a gateway's sibling
   `reasoning` / `reasoning_content` field was invisible even when present.

A direct call to OpenRouter confirms the field is real:
``message keys: [..., 'reasoning', 'reasoning_details']``.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningEffort,
)
from agent_runtime.execution.deep_agent_builder import _merge_compat_reasoning_kwargs
from agent_runtime.execution.models import ModelConfigResolver
from runtime_worker.stream_messages import StreamMessageParser


def config(**overrides: object) -> ModelConfig:
    base: dict[str, object] = {
        "provider": "openrouter",
        "model_name": "deepseek/deepseek-r1",
        "max_input_tokens": 100_000,
        "timeout_seconds": 60,
        "temperature": 0,
        "supports_reasoning": True,
        "reasoning": ModelReasoningConfig(
            enabled=True, effort=ModelReasoningEffort.HIGH
        ),
    }
    base.update(overrides)
    return ModelConfig(**base)  # type: ignore[arg-type]


class TestRequestingReasoning:
    def test_reasoning_travels_in_extra_body_not_as_a_responses_kwarg(self) -> None:
        # `extra_body` is forwarded verbatim on chat-completions; a top-level
        # `reasoning` kwarg would flip ChatOpenAI onto /responses, which these
        # gateways do not implement.
        kwargs: dict[str, object] = {}
        _merge_compat_reasoning_kwargs(kwargs, config())
        assert kwargs == {
            "extra_body": {"reasoning": {"enabled": True, "effort": "high"}}
        }
        assert "reasoning" not in kwargs

    def test_nothing_requested_for_a_non_reasoning_model(self) -> None:
        # A gateway can reject a `reasoning` field on a model that has none, and
        # a failed run is worse than a missing thinking stream.
        kwargs: dict[str, object] = {}
        _merge_compat_reasoning_kwargs(kwargs, config(supports_reasoning=False))
        assert kwargs == {}

    def test_effort_none_opts_out(self) -> None:
        kwargs: dict[str, object] = {}
        _merge_compat_reasoning_kwargs(
            kwargs,
            config(
                reasoning=ModelReasoningConfig(
                    enabled=True, effort=ModelReasoningEffort.NONE
                )
            ),
        )
        assert kwargs == {}

    def test_disabled_reasoning_opts_out(self) -> None:
        kwargs: dict[str, object] = {}
        _merge_compat_reasoning_kwargs(
            kwargs, config(reasoning=ModelReasoningConfig(enabled=False))
        )
        assert kwargs == {}

    def test_an_existing_extra_body_is_not_clobbered(self) -> None:
        kwargs: dict[str, object] = {"extra_body": {"provider": {"order": ["x"]}}}
        _merge_compat_reasoning_kwargs(kwargs, config())
        assert kwargs["extra_body"]["provider"] == {"order": ["x"]}  # type: ignore[index]
        assert kwargs["extra_body"]["reasoning"]["effort"] == "high"  # type: ignore[index]


class TestCapabilityGate:
    def test_gateway_reasoning_families_are_recognised(self) -> None:
        for name in (
            "deepseek/deepseek-r1",
            "deepseek-r1:14b",
            "qwen/qwen3-32b",
            "arcee-ai/trinity-large-thinking",
            "openai/gpt-5.6",
        ):
            assert ModelConfigResolver._gateway_supports_reasoning(name), name

    def test_non_reasoning_gateway_models_are_excluded(self) -> None:
        for name in (
            "deepseek/deepseek-chat",
            "meta-llama/llama-3-70b-instruct",
            "openai/gpt-4o",
        ):
            assert not ModelConfigResolver._gateway_supports_reasoning(name), name

    def test_anthropic_never_uses_the_gateway_path(self) -> None:
        # Anthropic thinking is negotiated by `_anthropic_model_kwargs`; routing
        # it through extra_body as well would send two competing controls.
        assert not ModelConfigResolver._model_supports_reasoning(
            "anthropic", "claude-sonnet-5"
        )


class TestRecognisingReasoning:
    def test_openrouter_reasoning_field_on_the_delta(self) -> None:
        assert (
            StreamMessageParser.reasoning_delta({"content": "", "reasoning": "hmm"})
            == "hmm"
        )

    def test_deepseek_reasoning_content_field(self) -> None:
        assert (
            StreamMessageParser.reasoning_delta(
                {"content": "", "reasoning_content": "weighing options"}
            )
            == "weighing options"
        )

    def test_field_parked_on_additional_kwargs(self) -> None:
        # LangChain parks unmodelled provider fields here rather than promoting
        # them to attributes, so the same field arrives in a different place
        # depending on client version.
        assert (
            StreamMessageParser.reasoning_delta(
                {"content": "", "additional_kwargs": {"reasoning": "parked"}}
            )
            == "parked"
        )

    def test_visible_text_is_untouched_by_the_sibling_field(self) -> None:
        message = {"content": "the answer", "reasoning": "hidden"}
        assert StreamMessageParser.message_delta(message) == "the answer"
        assert StreamMessageParser.reasoning_delta(message) == "hidden"

    def test_typed_blocks_still_win_and_never_double_count(self) -> None:
        # A provider supplying both shapes must not have its reasoning counted
        # twice; the block walk is authoritative.
        message = {
            "content": [{"type": "thinking", "thinking": "block form"}],
            "reasoning": "sibling form",
        }
        assert StreamMessageParser.reasoning_delta(message) == "block form"

    def test_absent_reasoning_stays_none(self) -> None:
        assert StreamMessageParser.reasoning_delta({"content": "plain"}) is None
