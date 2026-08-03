"""Sampling-parameter support per model.

The failure this guards is silent in unit tests and fatal in production:
``FakeModelProvider`` substitutes the model four lines above the kwargs
assembly, so no unit test has ever sent real kwargs to a real provider. A live
desktop journey caught it instead — every ``claude-sonnet-5`` run failed with
``400 invalid_request_error: `temperature` is deprecated for this model``.
"""

from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.execution.sampling_support import SamplingParameterSupport


class TestModelsThatRejectSampling:
    @pytest.mark.parametrize(
        "model_name",
        [
            "claude-sonnet-5",
            "claude-opus-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-fable-5",
            "claude-mythos-5",
        ],
    )
    def test_the_claude_47_plus_generation_rejects_temperature(
        self, model_name: str
    ) -> None:
        assert SamplingParameterSupport.accepts_temperature(model_name) is False

    @pytest.mark.parametrize(
        "model_name",
        [
            "anthropic/claude-sonnet-5",  # OpenRouter
            "anthropic.claude-sonnet-5",  # Bedrock-style
            "  Claude-Sonnet-5  ",  # case + whitespace
        ],
    )
    def test_the_same_model_is_recognized_under_every_gateway_spelling(
        self, model_name: str
    ) -> None:
        """A gateway prefix must not smuggle a rejecting model past the check."""

        assert SamplingParameterSupport.accepts_temperature(model_name) is False


class TestModelsThatStillAcceptSampling:
    @pytest.mark.parametrize(
        "model_name",
        [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "gpt-5.4-mini",
            "gemini-2.5-flash",
            "llama3.1",
        ],
    )
    def test_earlier_claude_and_other_vendors_keep_the_parameter(
        self, model_name: str
    ) -> None:
        """Claude 4.6 and earlier still accept it — this is per-model, not per-vendor."""

        assert SamplingParameterSupport.accepts_temperature(model_name) is True

    def test_an_unknown_model_is_assumed_to_accept_it(self) -> None:
        """Fail open: the parameter has been universally valid for years.

        A new model that drops it is one entry in SAMPLING_FREE_MODELS; the
        reverse default would silently stop honouring temperature everywhere.
        """

        assert SamplingParameterSupport.accepts_temperature("some-new-model") is True

    def test_an_empty_name_does_not_match_every_entry(self) -> None:
        """`"".endswith(x)` is False, but pin it — a bug here disables the knob."""

        assert SamplingParameterSupport.accepts_temperature("") is True


class TestBuildChatModelOmitsTheParameter:
    """The end that matters: what actually reaches the provider client."""

    @staticmethod
    def _config(model_name: str) -> ModelConfig:
        return ModelConfig(
            provider="anthropic",
            model_name=model_name,
            max_input_tokens=200_000,
            timeout_seconds=60.0,
            temperature=0.0,
        )

    def _captured_kwargs(self, monkeypatch, model_name: str) -> dict:
        captured: dict = {}

        def _fake_init(model, **kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(
            "agent_runtime.execution.deep_agent_builder.init_chat_model", _fake_init
        )
        monkeypatch.delenv("RUNTIME_FAKE_MODEL", raising=False)
        build_chat_model(self._config(model_name))
        return captured

    def test_a_sampling_free_model_never_receives_temperature(
        self, monkeypatch
    ) -> None:
        kwargs = self._captured_kwargs(monkeypatch, "claude-sonnet-5")

        assert "temperature" not in kwargs

    def test_a_model_that_accepts_it_still_receives_it(self, monkeypatch) -> None:
        """The fix must not silently drop the knob everywhere else."""

        kwargs = self._captured_kwargs(monkeypatch, "claude-sonnet-4-6")

        assert kwargs["temperature"] == 0.0
