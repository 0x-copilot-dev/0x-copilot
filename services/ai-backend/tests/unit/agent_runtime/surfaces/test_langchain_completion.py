"""Unit tests for :class:`LangChainSpecCompletion` (generative-UI PRD-07).

Exercises the production completion seam with a stub chat model (no live model,
no network): the structured-output happy path with usage extraction, and the
JSON-mode fallback when the provider lacks structured output.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from agent_runtime.capabilities.surfaces.generator import LangChainSpecCompletion

_PARSED = {"spec_version": 1, "archetype": "record", "title_path": "issue.title"}


class _StubStructured:
    def __init__(self, result: object) -> None:
        self._result = result

    async def ainvoke(self, messages: object) -> object:
        return self._result


class _StubModel:
    def __init__(self, *, structured: object | None, message: object | None) -> None:
        self._structured = structured
        self._message = message

    def with_structured_output(
        self, schema: object, include_raw: bool = True
    ) -> object:
        if self._structured is None:
            raise NotImplementedError("provider lacks structured output")
        return _StubStructured(self._structured)

    async def ainvoke(self, messages: object) -> object:
        return self._message


class TestLangChainSpecCompletion:
    async def test_structured_output_returns_candidate_and_usage(self) -> None:
        raw = AIMessage(
            content="{}",
            usage_metadata={
                "input_tokens": 120,
                "output_tokens": 40,
                "total_tokens": 160,
            },
        )
        model = _StubModel(
            structured={"raw": raw, "parsed": dict(_PARSED)}, message=None
        )
        completion = LangChainSpecCompletion(model=model, model_id="nano", schema={})

        result = await completion.complete(system="s", user="u")

        assert result.candidate == _PARSED
        assert result.input_tokens == 120
        assert result.output_tokens == 40
        assert result.model == "nano"

    async def test_json_mode_fallback_parses_text(self) -> None:
        message = AIMessage(
            content='```json\n{"spec_version": 1, "archetype": "record", "title_path": "x"}\n```',
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        model = _StubModel(structured=None, message=message)
        completion = LangChainSpecCompletion(model=model, model_id="nano", schema={})

        result = await completion.complete(system="s", user="u")

        assert result.candidate == {
            "spec_version": 1,
            "archetype": "record",
            "title_path": "x",
        }
        assert result.input_tokens == 10
