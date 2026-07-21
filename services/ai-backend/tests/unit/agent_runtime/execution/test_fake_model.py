"""Unit coverage for the env-gated deterministic fake model + its gate bypass."""

from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.fake_model import (
    DeterministicFakeChatModel,
    FakeModelProvider,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings


def _keyless_settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class TestFakeModelActivation:
    def test_disabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv(FakeModelProvider.ENV_FLAG, raising=False)
        assert FakeModelProvider.is_enabled() is False

    @pytest.mark.parametrize("raw", ["", "0", "false", "off", "no", "nope", "2"])
    def test_off_for_falsey_or_unrecognized(self, monkeypatch, raw: str) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, raw)
        assert FakeModelProvider.is_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "On", "enabled"])
    def test_on_for_truthy(self, monkeypatch, raw: str) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, raw)
        assert FakeModelProvider.is_enabled() is True


class TestFakeModelStreaming:
    async def test_streams_reasoning_then_text_and_final(self) -> None:
        model = DeterministicFakeChatModel()
        chunks = [chunk async for chunk in model._astream([])]

        reasoning = [c for c in chunks if isinstance(c.message.content, list)]
        text = [c for c in chunks if isinstance(c.message.content, str)]
        assert reasoning, "no reasoning chunks streamed"
        assert len(text) >= 2, "text should stream as multiple deltas"

        block_types = [b.get("type") for c in reasoning for b in c.message.content]
        assert "reasoning_summary_text_delta" in block_types
        assert "reasoning_summary_text_done" in block_types

        assert "".join(c.message.content for c in text) == model.response_text
        result = await model._agenerate([])
        assert result.generations[0].message.content == model.response_text

    async def test_reasoning_can_be_disabled(self) -> None:
        model = DeterministicFakeChatModel(emit_reasoning=False)
        chunks = [chunk async for chunk in model._astream([])]
        assert all(isinstance(c.message.content, str) for c in chunks)


class TestBuildChatModelFakeBranch:
    def test_returns_fake_when_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        config = ModelConfigResolver(_keyless_settings()).resolve()
        model = build_chat_model(config)
        assert isinstance(model, DeterministicFakeChatModel)


class TestCredentialGateBypass:
    def test_gate_still_fires_without_key_and_without_fake(self, monkeypatch) -> None:
        monkeypatch.delenv(FakeModelProvider.ENV_FLAG, raising=False)
        with pytest.raises(AgentRuntimeError) as excinfo:
            ModelConfigResolver(_keyless_settings()).resolve()
        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR

    def test_gate_bypassed_in_fake_mode_without_key(self, monkeypatch) -> None:
        monkeypatch.setenv(FakeModelProvider.ENV_FLAG, "1")
        config = ModelConfigResolver(_keyless_settings()).resolve()
        assert config.provider == "openai"
