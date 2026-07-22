"""Unit tests for the pre-run token counters (litellm + char-heuristic fallback).

All offline: ``LitellmTokenCounter`` runs under
``apply_offline_litellm_config`` (bundled cost map + HF-download disabled), so
no test here touches the network. The socket-blocked hermetic proof lives in
``test_token_counter_offline.py``.
"""

from __future__ import annotations

import litellm

from agent_runtime.budgets.token_counter import (
    CharHeuristicTokenCounter,
    LitellmTokenCounter,
    TokenCounterPort,
)

_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "How many tokens is this sentence, roughly?"},
]


class TestLitellmTokenCounter:
    def test_returns_plausible_int_for_each_provider(self) -> None:
        counter = LitellmTokenCounter()
        for model in (
            "gpt-5.4-mini",
            "claude-3-5-sonnet-20240620",
            "gemini-1.5-pro",
            "ollama/llama-3",
        ):
            count = counter.count(model=model, messages=_MESSAGES)
            assert isinstance(count, int)
            assert count > 0

    def test_applies_offline_config_before_counting(self) -> None:
        # Reset the process-global flag so we can prove the counter (re)applies
        # the guardrail on its own path rather than relying on prior state.
        litellm.disable_hf_tokenizer_download = False
        LitellmTokenCounter().count(model="gpt-5.4-mini", messages=_MESSAGES)
        assert litellm.disable_hf_tokenizer_download is True

    def test_returns_none_on_malformed_messages(self) -> None:
        # A non-mapping message list makes litellm raise internally; the counter
        # must swallow it and return None so the caller falls back.
        counter = LitellmTokenCounter()
        assert counter.count(model="gpt-5.4-mini", messages=[object()]) is None  # type: ignore[list-item]

    def test_satisfies_the_port_protocol(self) -> None:
        assert isinstance(LitellmTokenCounter(), TokenCounterPort)


class TestCharHeuristicTokenCounter:
    def test_is_len_div_four_over_content(self) -> None:
        counter = CharHeuristicTokenCounter()
        messages = [{"role": "user", "content": "x" * 40}]
        assert counter.count(model="anything", messages=messages) == 10

    def test_sums_across_messages_and_ignores_non_string_content(self) -> None:
        counter = CharHeuristicTokenCounter()
        messages = [
            {"role": "system", "content": "a" * 8},
            {"role": "user", "content": "b" * 12},
            {"role": "user"},  # no content key -> contributes 0
        ]
        assert counter.count(model="anything", messages=messages) == (8 + 12) // 4

    def test_empty_messages_is_zero(self) -> None:
        assert CharHeuristicTokenCounter().count(model="m", messages=[]) == 0

    def test_satisfies_the_port_protocol(self) -> None:
        assert isinstance(CharHeuristicTokenCounter(), TokenCounterPort)
