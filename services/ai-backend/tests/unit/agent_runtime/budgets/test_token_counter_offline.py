"""Hermetic proof that pre-run token counting never hits the network.

CI-hermeticity keystone. With the HuggingFace tokenizer path forced to raise and
outbound sockets blocked, ``LitellmTokenCounter`` still returns counts for every
provider — because ``apply_offline_litellm_config`` disables the HF download and
pins the bundled cost map, so llama/cohere slugs are routed through the offline
tiktoken encoders instead of a ``Tokenizer.from_pretrained("Xenova/…")`` fetch.
"""

from __future__ import annotations

import os
import socket

import litellm
import litellm.utils as litellm_utils
import pytest

from agent_runtime.budgets.token_counter import LitellmTokenCounter
from agent_runtime.pricing.litellm_runtime import apply_offline_litellm_config

_MESSAGES = [
    {"role": "user", "content": "Count these tokens without any network access."},
]


class _NetworkBlockedError(RuntimeError):
    """Raised if any code under test attempts an outbound connection."""


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(*_args: object, **_kwargs: object) -> None:
        raise _NetworkBlockedError("network access is blocked in this test")

    # Block outbound TCP at the socket layer (defence in depth beneath the
    # disable-HF guardrail the count relies on).
    monkeypatch.setattr(socket.socket, "connect", _deny, raising=True)
    monkeypatch.setattr(socket, "create_connection", _deny, raising=True)


class TestOfflineConfig:
    def test_apply_sets_env_and_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)
        litellm.disable_hf_tokenizer_download = False
        apply_offline_litellm_config()
        assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
        assert litellm.disable_hf_tokenizer_download is True

    def test_apply_is_idempotent(self) -> None:
        apply_offline_litellm_config()
        apply_offline_litellm_config()
        assert litellm.disable_hf_tokenizer_download is True


class TestOfflineTokenCounting:
    def test_llama_slug_never_reaches_huggingface(
        self, monkeypatch: pytest.MonkeyPatch, block_network: None
    ) -> None:
        # Without disable_hf, an ``ollama/llama-3`` count would call
        # ``_return_huggingface_tokenizer`` (a HF download). Force that path to
        # blow up: the count must STILL succeed, proving the guardrail
        # short-circuits to the offline tiktoken encoder before reaching it.
        def _boom(_model: str) -> object:
            raise AssertionError("HuggingFace tokenizer path must not be reached")

        monkeypatch.setattr(litellm_utils, "_return_huggingface_tokenizer", _boom)
        count = LitellmTokenCounter().count(model="ollama/llama-3", messages=_MESSAGES)
        assert isinstance(count, int)
        assert count > 0

    def test_native_providers_count_offline(self, block_network: None) -> None:
        counter = LitellmTokenCounter()
        for model in ("gpt-5.4-mini", "claude-3-5-sonnet-20240620", "gemini-1.5-pro"):
            count = counter.count(model=model, messages=_MESSAGES)
            assert isinstance(count, int)
            assert count > 0
