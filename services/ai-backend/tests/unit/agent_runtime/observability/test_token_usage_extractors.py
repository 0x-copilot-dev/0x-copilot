"""Per-provider snapshot tests for token-usage extractors (Sub-PRD 01a).

Each test pins the wire shape we currently see from each provider's
LangChain adapter against the normalized output. Adding a new provider
chunk shape requires landing a fixture *and* an extractor change in
lockstep — the fixture fails loudly if the wire shape drifts.
"""

from __future__ import annotations

from types import SimpleNamespace


from agent_runtime.observability.token_usage import (
    AnthropicProviderTokenUsageExtractor,
    GeminiProviderTokenUsageExtractor,
    NormalizedTokenUsage,
    OpenAIProviderTokenUsageExtractor,
    TokenUsageExtractorRegistry,
    _LcdFallbackExtractor,
)


def _chunk_with_usage_metadata(usage_metadata: dict[str, object]) -> object:
    """LangChain-native AIMessageChunk shape."""

    return SimpleNamespace(usage_metadata=usage_metadata, id="msg_test")


def _chunk_with_response_metadata(token_usage: dict[str, object]) -> object:
    """Raw provider-dict-under-response_metadata.token_usage shape."""

    return SimpleNamespace(
        response_metadata={"token_usage": token_usage}, id="msg_test"
    )


def _envelope_chunk(usage_metadata: dict[str, object]) -> dict[str, object]:
    """Event-stream envelope (chunk is a dict with .message inside)."""

    return {
        "message": SimpleNamespace(usage_metadata=usage_metadata, id="msg_test"),
    }


class TestOpenAIChatCompletionsChunk:
    extractor = OpenAIProviderTokenUsageExtractor()

    def test_basic_input_output(self) -> None:
        chunk = _chunk_with_usage_metadata(
            {"input_tokens": 1200, "output_tokens": 250, "total_tokens": 1450}
        )
        usage = self.extractor.extract(chunk)
        assert usage == NormalizedTokenUsage(input_tokens=1200, output_tokens=250)
        assert usage.total_tokens == 1450

    def test_cached_input_from_prompt_tokens_details(self) -> None:
        # Wire shape: chat completions returns cached under
        # response_metadata.token_usage.prompt_tokens_details.cached_tokens.
        chunk = _chunk_with_response_metadata(
            {
                "prompt_tokens": 1200,
                "completion_tokens": 200,
                "total_tokens": 1400,
                "prompt_tokens_details": {"cached_tokens": 800, "audio_tokens": 0},
                "completion_tokens_details": {
                    "reasoning_tokens": 0,
                    "audio_tokens": 0,
                },
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 1200
        assert usage.output_tokens == 200
        assert usage.cached_input_tokens == 800
        assert usage.reasoning_tokens == 0
        assert usage.audio_input_tokens == 0


class TestOpenAIOSeriesChunk:
    """o1 / o3 carry reasoning tokens under completion_tokens_details."""

    extractor = OpenAIProviderTokenUsageExtractor()

    def test_reasoning_tokens_extracted(self) -> None:
        chunk = _chunk_with_response_metadata(
            {
                "prompt_tokens": 500,
                "completion_tokens": 1200,
                "total_tokens": 1700,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 900},
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.reasoning_tokens == 900
        assert usage.output_tokens == 1200
        # total_tokens computed = input + output + reasoning = 500 + 1200 + 900.
        assert usage.total_tokens == 2600

    def test_responses_api_output_tokens_details_shape(self) -> None:
        # Newer Responses API surfaces details under
        # ``input_tokens_details`` / ``output_tokens_details`` instead.
        chunk = _chunk_with_response_metadata(
            {
                "input_tokens": 600,
                "output_tokens": 400,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens_details": {"reasoning_tokens": 350},
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 600
        assert usage.cached_input_tokens == 200
        assert usage.reasoning_tokens == 350


class TestOpenAIResponsesAudioChunk:
    extractor = OpenAIProviderTokenUsageExtractor()

    def test_audio_input_and_output_extracted(self) -> None:
        chunk = _chunk_with_response_metadata(
            {
                "prompt_tokens": 300,
                "completion_tokens": 220,
                "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 150},
                "completion_tokens_details": {
                    "reasoning_tokens": 0,
                    "audio_tokens": 80,
                },
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.audio_input_tokens == 150
        assert usage.audio_output_tokens == 80


class TestAnthropicChunk:
    extractor = AnthropicProviderTokenUsageExtractor()

    def test_prompt_caching_gross_input_normalized(self) -> None:
        # Anthropic wire: input_tokens is the non-cache portion only.
        # Gross = input + cache_creation + cache_read.
        chunk = _chunk_with_response_metadata(
            {
                "input_tokens": 200,
                "output_tokens": 150,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 500,
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 1700  # 200 + 1000 + 500
        assert usage.cache_creation_input_tokens == 1000
        assert usage.cached_input_tokens == 500
        assert usage.output_tokens == 150

    def test_langchain_short_cache_keys(self) -> None:
        # LangChain's Anthropic adapter sometimes uses cache_creation /
        # cache_read directly under usage_metadata.
        chunk = _chunk_with_usage_metadata(
            {
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_creation": 50,
                "cache_read": 25,
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 175  # 100 + 50 + 25
        assert usage.cache_creation_input_tokens == 50
        assert usage.cached_input_tokens == 25

    def test_cache_read_from_input_token_details(self) -> None:
        # When the cache_read field lives nested under input_token_details.
        chunk = _chunk_with_usage_metadata(
            {
                "input_tokens": 300,
                "output_tokens": 100,
                "cache_creation_input_tokens": 0,
                "input_token_details": {"cache_read": 75},
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.cached_input_tokens == 75
        assert usage.input_tokens == 375  # 300 + 0 + 75

    def test_extended_thinking_reasoning_tokens(self) -> None:
        chunk = _chunk_with_response_metadata(
            {
                "input_tokens": 400,
                "output_tokens": 300,
                "reasoning_tokens": 2200,
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.reasoning_tokens == 2200


class TestGeminiChunk:
    extractor = GeminiProviderTokenUsageExtractor()

    def test_basic_token_counts(self) -> None:
        chunk = _chunk_with_response_metadata(
            {
                "prompt_token_count": 800,
                "candidates_token_count": 220,
                "total_token_count": 1020,
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 800
        assert usage.output_tokens == 220
        # No cache / reasoning / audio in current Gemini API.
        assert usage.cached_input_tokens == 0
        assert usage.reasoning_tokens == 0

    def test_thinking_mode_reasoning_tokens(self) -> None:
        chunk = _chunk_with_usage_metadata(
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "thoughts_token_count": 500,
            }
        )
        usage = self.extractor.extract(chunk)
        assert usage is not None
        assert usage.reasoning_tokens == 500


class TestEmptyAndMalformedChunks:
    def test_no_usage_block_returns_none(self) -> None:
        chunk = SimpleNamespace(id="msg_test")
        assert OpenAIProviderTokenUsageExtractor().extract(chunk) is None
        assert AnthropicProviderTokenUsageExtractor().extract(chunk) is None
        assert GeminiProviderTokenUsageExtractor().extract(chunk) is None

    def test_zero_input_and_output_returns_none(self) -> None:
        chunk = _chunk_with_usage_metadata({"input_tokens": 0, "output_tokens": 0})
        assert OpenAIProviderTokenUsageExtractor().extract(chunk) is None

    def test_malformed_field_types_dont_raise(self) -> None:
        chunk = _chunk_with_usage_metadata(
            {"input_tokens": "not a number", "output_tokens": [42], "total_tokens": -1}
        )
        # No raise, no crash. Returns None when nothing actionable.
        assert OpenAIProviderTokenUsageExtractor().extract(chunk) is None

    def test_negative_values_treated_as_zero(self) -> None:
        chunk = _chunk_with_response_metadata(
            {"prompt_tokens": -1, "completion_tokens": -1}
        )
        assert OpenAIProviderTokenUsageExtractor().extract(chunk) is None

    def test_bool_value_treated_as_zero(self) -> None:
        # bool is a subclass of int in Python — must be excluded.
        chunk = _chunk_with_usage_metadata({"input_tokens": True, "output_tokens": 100})
        usage = OpenAIProviderTokenUsageExtractor().extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 0
        assert usage.output_tokens == 100

    def test_envelope_message_unwrap(self) -> None:
        chunk = _envelope_chunk({"input_tokens": 50, "output_tokens": 25})
        usage = OpenAIProviderTokenUsageExtractor().extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 50
        assert usage.output_tokens == 25


class TestRegistryDispatch:
    def test_openai_provider(self) -> None:
        assert isinstance(
            TokenUsageExtractorRegistry.for_provider("openai"),
            OpenAIProviderTokenUsageExtractor,
        )

    def test_anthropic_provider(self) -> None:
        assert isinstance(
            TokenUsageExtractorRegistry.for_provider("anthropic"),
            AnthropicProviderTokenUsageExtractor,
        )

    def test_gemini_provider(self) -> None:
        assert isinstance(
            TokenUsageExtractorRegistry.for_provider("gemini"),
            GeminiProviderTokenUsageExtractor,
        )

    def test_provider_lookup_is_case_insensitive(self) -> None:
        assert isinstance(
            TokenUsageExtractorRegistry.for_provider("OpenAI"),
            OpenAIProviderTokenUsageExtractor,
        )
        assert isinstance(
            TokenUsageExtractorRegistry.for_provider("  ANTHROPIC  "),
            AnthropicProviderTokenUsageExtractor,
        )

    def test_unknown_provider_falls_back_to_lcd(self) -> None:
        extractor = TokenUsageExtractorRegistry.for_provider("xai")
        assert isinstance(extractor, _LcdFallbackExtractor)

    def test_lcd_fallback_extracts_basic_kinds(self) -> None:
        # New provider shape that happens to use openai-like field names —
        # the LCD path should still surface input/output/cached.
        chunk = _chunk_with_usage_metadata(
            {"prompt_tokens": 100, "completion_tokens": 50}
        )
        extractor = TokenUsageExtractorRegistry.for_provider("xai")
        usage = extractor.extract(chunk)
        assert usage is not None
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
